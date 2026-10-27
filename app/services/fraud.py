"""Controles anti-fraude sur les ventes declarees et les paiements."""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as OrmSession

from ..models import Inquiry, Order, OrderItem, Payment, PaymentProof, Product, Shop, User, utcnow


def evaluate_order(db: OrmSession, order: Order, *, shop: Shop | None = None) -> tuple[int, list[str]]:
    """Retourne un score 0-100 et la liste des signaux. Appele a la creation."""
    score = 0
    signals: list[str] = []
    shop = shop or db.get(Shop, order.shop_id)
    owner = db.get(User, shop.owner_id) if shop else None

    if owner and order.customer_phone_e164 == owner.phone_e164:
        score += 40
        signals.append("Achat declare sur le propre numero du vendeur (auto-vente).")
    if shop and order.customer_phone_e164 == shop.whatsapp_e164:
        score += 30
        signals.append("Numero client identique au WhatsApp de la boutique.")

    if order.origin in {"vendor_declaration", "whatsapp"}:
        if not order.claim_code:
            score += 15
            signals.append("Vente hors plateforme sans code de demande WhatsApp.")
        else:
            inq = db.execute(select(Inquiry).where(Inquiry.claim_code == order.claim_code)).scalar_one_or_none()
            if not inq:
                score += 25
                signals.append("Code de demande WhatsApp inconnu.")
            elif inq.shop_id != order.shop_id:
                score += 45
                signals.append("Code de demande appartenant a une autre boutique.")
            else:
                inq.order_id = order.id
                db.flush()

    items = list(db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars())
    for item in items:
        prod = db.get(Product, item.product_id) if item.product_id else None
        if prod and prod.price_xof > 0:
            ratio = item.unit_price_xof / prod.price_xof
            if ratio < 0.5 or ratio > 1.5:
                score += 20
                signals.append("Montant declare tres eloigne du prix catalogue.")

    since = utcnow() - timedelta(hours=1)
    recent = int(
        db.execute(
            select(func.count(Order.id)).where(
                Order.customer_phone_e164 == order.customer_phone_e164,
                Order.created_at >= since,
            )
        ).scalar_one()
    )
    if recent > 5:
        score += 20
        signals.append(f"Velocity inhabituelle : {recent} commandes pour ce numero en 1 h.")

    recent_vendor = int(
        db.execute(
            select(func.count(Order.id)).where(Order.shop_id == order.shop_id, Order.created_at >= since)
        ).scalar_one()
    )
    if recent_vendor > 12:
        score += 15
        signals.append(f"Velocity vendeur : {recent_vendor} declarations en 1 h.")

    # Detection de sous-declaration : beaucoup de contacts WhatsApp, peu de ventes declarees.
    inquiries = int(
        db.execute(select(func.count(Inquiry.id)).where(Inquiry.shop_id == order.shop_id)).scalar_one()
    )
    declarations = int(
        db.execute(
            select(func.count(Order.id)).where(
                Order.shop_id == order.shop_id,
                Order.origin.in_(["vendor_declaration", "whatsapp"]),
            )
        ).scalar_one()
    )
    if inquiries >= 20 and declarations / max(inquiries, 1) < 0.02:
        score += 15
        signals.append("Taux de declaration faible par rapport aux demandes WhatsApp.")

    score = min(score, 100)
    order.risk_score = score
    order.risk_signals_json = json.dumps(signals, ensure_ascii=False)
    if shop:
        shop.risk_score = max(shop.risk_score, score)
    db.flush()
    return score, signals


def duplicate_reference(db: OrmSession, provider: str, reference: str, exclude_order_id: str = "") -> bool:
    """Une reference de transaction ne peut servir qu'a une seule commande."""
    if not reference:
        return False
    payment_orders = list(
        db.execute(
            select(Payment.order_id).where(
                Payment.provider == provider, Payment.provider_reference == reference
            )
        ).scalars()
    )
    proof_orders = list(
        db.execute(
            select(PaymentProof.order_id).where(
                PaymentProof.provider == provider, PaymentProof.provider_reference == reference
            )
        ).scalars()
    )
    return any(oid != exclude_order_id for oid in payment_orders + proof_orders)


def anomaly_report(db: OrmSession, limit: int = 20) -> dict:
    """Tableau de bord admin : vendeurs suspects et commandes a risque."""
    shops = list(db.execute(select(Shop).order_by(Shop.risk_score.desc()).limit(limit)).scalars())
    flagged = list(
        db.execute(
            select(Order).where(Order.risk_score >= 30).order_by(Order.created_at.desc()).limit(limit)
        ).scalars()
    )
    rows = []
    for shop in shops:
        inquiries = int(db.execute(select(func.count(Inquiry.id)).where(Inquiry.shop_id == shop.id)).scalar_one())
        declared = int(
            db.execute(
                select(func.count(Order.id)).where(
                    Order.shop_id == shop.id, Order.origin.in_(["vendor_declaration", "whatsapp"])
                )
            ).scalar_one()
        )
        rows.append({
            "shop": shop,
            "inquiries": inquiries,
            "declared": declared,
            "conversion": round(declared / inquiries * 100, 1) if inquiries else 0.0,
        })
    return {"shops": rows, "flagged_orders": flagged}
