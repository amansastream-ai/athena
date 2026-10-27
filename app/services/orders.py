"""Cycle de vie des commandes : creation, declaration WhatsApp, transitions controlees."""
from __future__ import annotations

import json
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..models import (
    Commission,
    Coupon,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentProof,
    PaymentStatus,
    Product,
    Shop,
    User,
    uid,
    utcnow,
)
from . import audit, fraud, ledger, notifications
from .registry import get_setting


class OrderError(Exception):
    pass


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.PENDING_VERIFICATION, OrderStatus.CANCELLED},
    OrderStatus.PENDING_VERIFICATION: {OrderStatus.CONFIRMED, OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PAID, OrderStatus.PREPARING, OrderStatus.CANCELLED, OrderStatus.DISPUTED},
    OrderStatus.PAID: {OrderStatus.PREPARING, OrderStatus.SHIPPED, OrderStatus.DELIVERED,
                       OrderStatus.REFUNDED, OrderStatus.DISPUTED},
    OrderStatus.PREPARING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED, OrderStatus.DISPUTED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.DISPUTED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.DISPUTED, OrderStatus.REFUNDED},
    OrderStatus.DISPUTED: {OrderStatus.DELIVERED, OrderStatus.REFUNDED, OrderStatus.CANCELLED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}

ALL_STATUSES = list(ALLOWED_TRANSITIONS.keys())


def new_reference() -> str:
    return f"JY-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


def _shop_products(db: OrmSession, shop_id: str, items: list[dict]) -> list[tuple[Product, int]]:
    out = []
    for item in items:
        p = db.get(Product, item["product_id"])
        if not p or p.shop_id != shop_id or p.status != "active":
            raise OrderError("Produit introuvable, inactif ou appartenant a une autre boutique.")
        qty = int(item["quantity"])
        if qty < 1 or qty > 999:
            raise OrderError("Quantite invalide.")
        if p.stock < qty:
            raise OrderError(f"Stock insuffisant pour « {p.title} » (disponible : {p.stock}).")
        out.append((p, qty))
    return out


def _coupon_discount(db: OrmSession, shop_id: str, code: str, subtotal: int) -> tuple[int, Coupon | None]:
    if not code:
        return 0, None
    coupon = db.execute(
        select(Coupon).where(Coupon.shop_id == shop_id, Coupon.code == code.strip().upper(), Coupon.active.is_(True))
    ).scalar_one_or_none()
    if not coupon:
        raise OrderError("Code promo invalide.")
    now = utcnow()
    if (coupon.starts_at and coupon.starts_at > now) or (coupon.ends_at and coupon.ends_at < now):
        raise OrderError("Code promo expire.")
    if coupon.used_count >= coupon.max_uses or subtotal < coupon.min_order_xof:
        raise OrderError("Code promo non applicable.")
    discount = min(subtotal, (subtotal * coupon.value) // 100 if coupon.kind == "percent" else coupon.value)
    return discount, coupon


def create_order(
    db: OrmSession,
    *,
    shop: Shop,
    items: list[dict],
    customer_name: str,
    customer_phone: str,
    customer_email: str | None = None,
    delivery_method: str = "pickup",
    delivery_address: str = "",
    claim_code: str = "",
    coupon_code: str = "",
    idempotency_key: str | None = None,
    actor: User | None = None,
    ip: str = "",
) -> Order:
    """Cree une commande avec des prix RECALCULES cote serveur. Aucun prix client n'est accepte."""
    if idempotency_key:
        existing = db.execute(
            select(Order).where(Order.idempotency_key == idempotency_key).limit(1)
        ).scalar_one_or_none()
        if existing:
            return existing

    pairs = _shop_products(db, shop.id, items)
    subtotal = sum(p.price_xof * q for p, q in pairs)
    discount, coupon = _coupon_discount(db, shop.id, coupon_code, subtotal)
    delivery_fee = 0 if delivery_method == "pickup" else max((p.delivery_fee_xof for p, _ in pairs), default=0)
    total = subtotal - discount + delivery_fee

    order = Order(
        id=uid(), reference=new_reference(), shop_id=shop.id,
        customer_id=actor.id if actor and actor.role == "customer" else None,
        customer_name=customer_name.strip(), customer_phone_e164=customer_phone,
        customer_email=customer_email, delivery_method=delivery_method,
        delivery_address=delivery_address, delivery_fee_xof=delivery_fee,
        subtotal_xof=subtotal, discount_xof=discount, total_xof=total,
        status=OrderStatus.PENDING, payment_status=PaymentStatus.UNPAID,
        origin="whatsapp" if claim_code else "checkout", claim_code=claim_code,
        idempotency_key=idempotency_key, created_by=actor.id if actor else None,
        created_at=utcnow(),
    )
    db.add(order)
    db.flush()
    for p, q in pairs:
        db.add(OrderItem(
            id=uid(), order_id=order.id, product_id=p.id, title_snapshot=p.title,
            unit_price_xof=p.price_xof, quantity=q, line_total_xof=p.price_xof * q,
        ))
        p.stock -= q
    if coupon:
        coupon.used_count += 1
    db.flush()

    fraud.evaluate_order(db, order, shop=shop)
    order._shop_cache = shop
    title, body = notifications.order_admin_notification(order, event="Nouvelle commande")
    notifications.notify_admins(
        db, kind="order_created", title=title, body=body,
        link=f"/admin/commandes/{order.id}", dedupe_key=f"order_created:{order.id}",
    )
    notifications.notify_user(
        db, shop.owner_id, kind="order_created", title=f"Nouvelle commande {order.reference}",
        body=f"{customer_name} - {total} F CFA. Paiement en attente de confirmation.",
        link=f"/vendeur/commandes/{order.id}", dedupe_key=f"vendor_order:{order.id}",
    )
    audit.log_action(db, action="order.created", actor=actor, entity_type="order", entity_id=order.id,
                     after={"reference": order.reference, "total_xof": total, "shop_id": shop.id}, ip=ip)
    db.commit()
    return order


def declare_sale(
    db: OrmSession,
    *,
    shop: Shop,
    vendor: User,
    product_id: str,
    quantity: int,
    sale_amount_xof: int,
    customer_name: str,
    customer_phone: str,
    claim_code: str = "",
    payment_provider: str = "wave",
    payment_reference: str = "",
    note: str = "",
    ip: str = "",
) -> Order:
    """Vente conclue sur WhatsApp. Cree une commande EN ATTENTE DE VERIFICATION.

    Un vendeur ne peut PAS marquer un paiement comme confirme : seule la validation
    d'une preuve par un administrateur ou un webhook signe peut le faire.
    """
    product = db.get(Product, product_id)
    if not product or product.shop_id != shop.id:
        raise OrderError("Produit introuvable dans votre boutique.")
    min_amount = int(get_setting(db, "min_declaration_amount_xof", 500))
    if sale_amount_xof < min_amount:
        raise OrderError(f"Montant minimum de declaration : {min_amount} F CFA.")
    if payment_reference and fraud.duplicate_reference(db, payment_provider, payment_reference):
        raise OrderError("Cette reference de paiement a deja ete utilisee.")

    unit = sale_amount_xof // quantity
    order = Order(
        id=uid(), reference=new_reference(), shop_id=shop.id,
        customer_name=customer_name, customer_phone_e164=customer_phone,
        subtotal_xof=sale_amount_xof, total_xof=sale_amount_xof,
        status=OrderStatus.PENDING_VERIFICATION,
        payment_status=PaymentStatus.PENDING_VERIFICATION,
        origin="vendor_declaration", claim_code=claim_code,
        created_by=vendor.id, created_at=utcnow(),
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(
        id=uid(), order_id=order.id, product_id=product.id, title_snapshot=product.title,
        unit_price_xof=unit, quantity=quantity, line_total_xof=sale_amount_xof,
    ))
    db.add(PaymentProof(
        id=uid(), order_id=order.id, submitted_by=vendor.id, provider=payment_provider,
        provider_reference=payment_reference or f"DECLARE-{order.reference}",
        amount_xof=sale_amount_xof, note=note, status="pending", created_at=utcnow(),
    ))
    db.flush()

    score, signals = fraud.evaluate_order(db, order, shop=shop)
    order._shop_cache = shop
    title, body = notifications.order_admin_notification(order, event="Vente declaree (WhatsApp)")
    body += f"\nCommission estimee : {(sale_amount_xof * ledger.effective_rate_bps(db, shop) + 5000) // 10000} F CFA"
    body += f"\nReference paiement : {payment_reference or 'non fournie'}"
    body += f"\nScore de risque : {score}/100" + (f" ({'; '.join(signals)})" if signals else "")
    notifications.notify_admins(
        db, kind="sale_declared", title=title, body=body,
        link=f"/admin/commandes/{order.id}", dedupe_key=f"sale_declared:{order.id}",
    )
    audit.log_action(db, action="sale.declared", actor=vendor, entity_type="order", entity_id=order.id,
                     after={"reference": order.reference, "amount": sale_amount_xof, "risk": score}, ip=ip)
    db.commit()
    return order


def _restore_stock(db: OrmSession, order: Order) -> None:
    for item in db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars():
        p = db.get(Product, item.product_id) if item.product_id else None
        if p:
            p.stock += item.quantity


def transition(
    db: OrmSession,
    order: Order,
    new_status: str,
    *,
    actor: User | None = None,
    note: str = "",
    ip: str = "",
) -> Order:
    if new_status not in ALL_STATUSES:
        raise OrderError("Statut inconnu.")
    current = order.status
    if new_status == OrderStatus.PAID and order.payment_status != PaymentStatus.PAID:
        raise OrderError("Impossible de marquer 'payee' sans paiement verifie.")
    if current == new_status:
        return order
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise OrderError(f"Transition interdite : {current} -> {new_status}.")

    before = {"status": current, "payment_status": order.payment_status}
    order.status = new_status
    order.version += 1
    if new_status == OrderStatus.DELIVERED:
        order.delivered_at = utcnow()
    if new_status in {OrderStatus.CANCELLED, OrderStatus.REFUNDED}:
        ledger.reverse_sale(db, order, reason=new_status, actor=actor.id if actor else "system",
                            mark_refunded=new_status == OrderStatus.REFUNDED)
        _restore_stock(db, order)
    db.flush()

    if new_status == OrderStatus.DELIVERED:
        ledger.maybe_recognize_sale(db, order, actor=actor.id if actor else "system",
                                    verification=order.payment_status)
    shop = db.get(Shop, order.shop_id)
    order._shop_cache = shop
    title, body = notifications.order_admin_notification(order, event=f"Statut -> {new_status}")
    if note:
        body += f"\nNote : {note}"
    notifications.notify_admins(db, kind="order_status", title=f"[Commande] {order.reference} -> {new_status}",
                                body=body, link=f"/admin/commandes/{order.id}")
    notifications.notify_user(db, shop.owner_id, kind="order_status",
                              title=f"Commande {order.reference} : {new_status}",
                              body=body, link=f"/vendeur/commandes/{order.id}")
    audit.log_action(db, action="order.status_changed", actor=actor, entity_type="order",
                     entity_id=order.id, before=before,
                     after={"status": new_status, "payment_status": order.payment_status}, ip=ip)
    db.commit()
    return order


def order_for_vendor(db: OrmSession, order_id: str, shop_id: str) -> Order:
    order = db.get(Order, order_id)
    if not order or order.shop_id != shop_id:
        raise OrderError("Commande introuvable.")
    return order


def order_view(db: OrmSession, order: Order) -> dict:
    items = list(db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars())
    commission = db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none()
    proofs = list(db.execute(select(PaymentProof).where(PaymentProof.order_id == order.id)).scalars())
    payments = list(db.execute(select(Payment).where(Payment.order_id == order.id)).scalars())
    return {
        "order": order,
        "items": items,
        "commission": commission,
        "proofs": proofs,
        "payments": payments,
        "risk_signals": json.loads(order.risk_signals_json or "[]"),
        "allowed": sorted(ALLOWED_TRANSITIONS.get(order.status, set())),
    }
