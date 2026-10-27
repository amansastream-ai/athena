"""Moteur de commissions et journal financier.

REGLES (source de verite) :
 1. Une vente n'est reconnue QUE si un paiement a ete VERIFIE cote serveur
    (webhook signe + re-verification distante, OU preuve validee par un administrateur).
 2. Une seule commission par commande : contrainte UNIQUE(commissions.order_id) + savepoint.
 3. Calcul en entiers XOF : commission = (gross * bps + 5000) // 10000.
 4. L'assiette est le montant des articles (hors frais de livraison), documente.
 5. Toute annulation/remboursement apres reconnaissance produit des ecritures
    compensatoires negatives. Aucune ecriture n'est jamais supprimee ou modifiee.
 6. Chaque ecriture est chainee par SHA-256 ; la chaine est verifiable.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..models import (
    Commission,
    CommissionSettlement,
    CommissionStatus,
    LedgerEntry,
    Order,
    OrderStatus,
    PaymentStatus,
    Shop,
    User,
    uid,
    utcnow,
)
from .registry import get_setting


class LedgerError(Exception):
    pass


# Serialise la construction de la chaine dans un processus. En multi-instances,
# remplacer par un verrou advisory PostgreSQL (pg_advisory_xact_lock) - voir docs/02.
_LEDGER_LOCK = threading.RLock()


# --------------------------------------------------------------------------- ecriture
def _entry_hash(payload: dict[str, Any], prev: str) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "|" + prev
    return hashlib.sha256(raw.encode()).hexdigest()


def _append_impl(
    db: OrmSession,
    *,
    entry_type: str,
    account: str,
    amount_xof: int,
    order_id: str | None = None,
    shop_id: str | None = None,
    description: str = "",
    actor: str = "system",
    created_at=None,
) -> LedgerEntry:
    if not isinstance(amount_xof, int) or amount_xof == 0:
        raise LedgerError("Le montant d'une ecriture doit etre un entier non nul.")
    last = db.execute(select(LedgerEntry).order_by(desc(LedgerEntry.id)).limit(1)).scalar_one_or_none()
    prev = last.entry_hash if last else "0" * 64
    ts = created_at or utcnow()
    payload = {
        "entry_type": entry_type,
        "account": account,
        "amount_xof": amount_xof,
        "order_id": order_id,
        "shop_id": shop_id,
        "description": description,
        "actor": actor,
        "created_at": ts.isoformat(timespec="microseconds"),
    }
    row = LedgerEntry(
        entry_type=entry_type,
        account=account,
        amount_xof=amount_xof,
        order_id=order_id,
        shop_id=shop_id,
        debit_xof=max(amount_xof, 0),
        credit_xof=max(-amount_xof, 0),
        description=description[:400],
        actor=actor,
        prev_hash=prev,
        entry_hash=_entry_hash(payload, prev),
        created_at=ts,
    )
    db.add(row)
    db.flush()
    return row


def append(db: OrmSession, **kwargs) -> LedgerEntry:
    with _LEDGER_LOCK:
        return _append_impl(db, **kwargs)


def verify_chain(db: OrmSession) -> tuple[bool, str]:
    prev = "0" * 64
    for row in db.execute(select(LedgerEntry).order_by(LedgerEntry.id)).scalars():
        payload = {
            "entry_type": row.entry_type,
            "account": row.account,
            "amount_xof": row.amount_xof,
            "order_id": row.order_id,
            "shop_id": row.shop_id,
            "description": row.description,
            "actor": row.actor,
            "created_at": row.created_at.isoformat(timespec="microseconds"),
        }
        if row.prev_hash != prev or _entry_hash(payload, prev) != row.entry_hash:
            return False, f"Rupture de chaine a l'ecriture #{row.id}"
        prev = row.entry_hash
    return True, "ok"


# --------------------------------------------------------------------------- calcul
def compute_commission(gross_xof: int, rate_bps: int, payment_fee_bps: int = 0) -> dict[str, int]:
    """Exemples : 1 000 -> 100 ; 5 000 -> 500 ; 10 000 -> 1 000 ; 100 000 -> 10 000."""
    if gross_xof < 0 or not 0 <= rate_bps <= 10_000 or not 0 <= payment_fee_bps <= 10_000:
        raise LedgerError("Parametres de commission invalides.")
    platform_fee = (gross_xof * rate_bps + 5000) // 10_000
    payment_fee = (gross_xof * payment_fee_bps + 5000) // 10_000
    return {
        "gross_xof": gross_xof,
        "rate_bps": rate_bps,
        "platform_fee_xof": platform_fee,
        "payment_fee_xof": payment_fee,
        "net_vendor_xof": gross_xof - platform_fee - payment_fee,
    }


def effective_rate_bps(db: OrmSession, shop: Shop) -> int:
    override = shop.commission_bps_override
    return int(override if override is not None else get_setting(db, "commission_bps", 1000))


# --------------------------------------------------------------------------- reconnaissance
def maybe_recognize_sale(
    db: OrmSession,
    order: Order,
    *,
    actor: str = "system",
    verification: str = "",
) -> Commission | None:
    """Cree la commission si (et seulement si) toutes les conditions sont reunies.

    Idempotent : un doublon de webhook, de clic ou d'appel ne cree jamais deux commissions.
    """
    existing = db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none()
    if existing:
        return existing
    if order.payment_status != PaymentStatus.PAID:
        return None
    if order.status in {OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.DISPUTED}:
        return None
    trigger = get_setting(db, "commission_trigger", "payment_confirmed")
    if trigger == "delivered" and order.status not in {OrderStatus.DELIVERED}:
        return None
    if not verification and not any(
        p.verification for p in order_payments(db, order.id)
    ):
        return None

    shop = db.get(Shop, order.shop_id)
    if not shop:
        raise LedgerError("Boutique introuvable pour cette commande.")

    gross = order.subtotal_xof - order.discount_xof
    if gross <= 0:
        raise LedgerError("Assiette de commission invalide.")
    rate_bps = effective_rate_bps(db, shop)
    fee_bps = int(get_setting(db, "payment_fee_bps", 0))
    calc = compute_commission(gross, rate_bps, fee_bps)

    try:
        with db.begin_nested():
            commission = Commission(
                id=uid(),
                order_id=order.id,
                shop_id=shop.id,
                gross_xof=gross,
                rate_bps=rate_bps,
                platform_fee_xof=calc["platform_fee_xof"],
                payment_fee_xof=calc["payment_fee_xof"],
                net_vendor_xof=calc["net_vendor_xof"],
                status=CommissionStatus.DUE,
                calculated_at=utcnow(),
                due_at=utcnow() + timedelta(days=7),
            )
            db.add(commission)
            db.flush()
    except IntegrityError:
        # begin_nested() a deja annule le SAVEPOINT ; la transaction externe reste valide.
        return db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none()

    tag = f"Commande {order.reference}"
    append(db, entry_type="sale", account="gmv", amount_xof=order.total_xof,
           order_id=order.id, shop_id=shop.id, description=tag, actor=actor)
    append(db, entry_type="commission", account="platform_revenue",
           amount_xof=calc["platform_fee_xof"], order_id=order.id, shop_id=shop.id,
           description=f"Commission {rate_bps / 100:g} % - {tag}", actor=actor)
    append(db, entry_type="commission", account="commission_receivable",
           amount_xof=calc["platform_fee_xof"], order_id=order.id, shop_id=shop.id,
           description=f"A recevoir du vendeur - {tag}", actor=actor)
    append(db, entry_type="vendor_share", account="vendor_payable",
           amount_xof=calc["net_vendor_xof"], order_id=order.id, shop_id=shop.id,
           description=f"Part vendeur - {tag}", actor=actor)
    if order.delivery_fee_xof:
        append(db, entry_type="delivery_fee", account="delivery_payable",
               amount_xof=order.delivery_fee_xof, order_id=order.id, shop_id=shop.id,
               description=f"Frais livraison - {tag}", actor=actor)
    if calc["payment_fee_xof"]:
        append(db, entry_type="payment_fee", account="payment_expense",
               amount_xof=calc["payment_fee_xof"], order_id=order.id, shop_id=shop.id,
               description=f"Frais PSP - {tag}", actor=actor)
    return commission


def order_payments(db: OrmSession, order_id: str) -> list:
    from ..models import Payment
    return list(db.execute(select(Payment).where(Payment.order_id == order_id)).scalars())


def reverse_sale(db: OrmSession, order: Order, *, reason: str, actor: str = "system",
                 mark_refunded: bool = True) -> list[LedgerEntry]:
    """Remboursement / annulation apres reconnaissance : ecritures compensatoires."""
    commission = db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none()
    if not commission or commission.status in {CommissionStatus.REVERSED, CommissionStatus.CANCELLED}:
        return []
    tag = f"Commande {order.reference} - {reason}"
    written = [
        append(db, entry_type="refund", account="gmv", amount_xof=-order.total_xof,
               order_id=order.id, shop_id=order.shop_id, description=tag, actor=actor),
        append(db, entry_type="reversal", account="platform_revenue",
               amount_xof=-commission.platform_fee_xof, order_id=order.id, shop_id=order.shop_id,
               description=tag, actor=actor),
    ]
    # Si la commission a deja ete encaissee, on annule la tresorerie ; sinon la creance.
    settled = commission.status == CommissionStatus.PAID
    written.append(
        append(db, entry_type="reversal", account="cash" if settled else "commission_receivable",
               amount_xof=-commission.platform_fee_xof, order_id=order.id, shop_id=order.shop_id,
               description=tag, actor=actor)
    )
    written.append(
        append(db, entry_type="reversal", account="vendor_payable",
               amount_xof=-commission.net_vendor_xof, order_id=order.id, shop_id=order.shop_id,
               description=tag, actor=actor)
    )
    if order.delivery_fee_xof:
        written.append(
            append(db, entry_type="reversal", account="delivery_payable",
                   amount_xof=-order.delivery_fee_xof, order_id=order.id, shop_id=order.shop_id,
                   description=tag, actor=actor)
        )
    if commission.payment_fee_xof:
        written.append(
            append(db, entry_type="reversal", account="payment_expense",
                   amount_xof=-commission.payment_fee_xof, order_id=order.id, shop_id=order.shop_id,
                   description=tag, actor=actor)
        )
    commission.status = CommissionStatus.REVERSED
    commission.reversed_at = utcnow()
    if mark_refunded and order.payment_status != PaymentStatus.REFUNDED:
        order.payment_status = PaymentStatus.REFUNDED
    elif not mark_refunded and order.payment_status == PaymentStatus.PAID:
        order.payment_status = PaymentStatus.UNPAID
    db.flush()
    return written


# --------------------------------------------------------------------------- encaissement
def record_settlement(
    db: OrmSession,
    shop: Shop,
    *,
    amount_xof: int,
    method: str = "wave",
    reference: str = "",
    note: str = "",
    actor: User | None = None,
) -> CommissionSettlement:
    outstanding = commission_balance(db, shop.id)
    if amount_xof > outstanding:
        raise LedgerError(
            f"Montant superieur au solde du ({outstanding} F CFA). Ajustez le montant."
        )
    row = CommissionSettlement(
        id=uid(), shop_id=shop.id, amount_xof=amount_xof, method=method,
        reference=reference, note=note, recorded_by=actor.id if actor else None,
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    append(db, entry_type="settlement", account="cash", amount_xof=amount_xof,
           shop_id=shop.id, description=f"Encaissement commission {reference or row.id}",
           actor=actor.id if actor else "system")
    append(db, entry_type="settlement", account="commission_receivable", amount_xof=-amount_xof,
           shop_id=shop.id, description=f"Reglement recu {reference or row.id}",
           actor=actor.id if actor else "system")

    # Affectation FIFO aux commissions dues.
    remaining = amount_xof
    dues = db.execute(
        select(Commission).where(
            Commission.shop_id == shop.id, Commission.status == CommissionStatus.DUE
        ).order_by(Commission.calculated_at, Commission.id)
    ).scalars()
    for c in dues:
        if remaining <= 0:
            break
        if c.platform_fee_xof <= remaining:
            c.status = CommissionStatus.PAID
            c.paid_at = utcnow()
            remaining -= c.platform_fee_xof
    db.flush()
    return row


# --------------------------------------------------------------------------- statistiques
def _sum(db: OrmSession, account: str, shop_id: str | None = None, entry_type: str | None = None) -> int:
    q = select(func.coalesce(func.sum(LedgerEntry.amount_xof), 0)).where(LedgerEntry.account == account)
    if shop_id:
        q = q.where(LedgerEntry.shop_id == shop_id)
    if entry_type:
        q = q.where(LedgerEntry.entry_type == entry_type)
    return int(db.execute(q).scalar_one())


def commission_balance(db: OrmSession, shop_id: str | None = None) -> int:
    return _sum(db, "commission_receivable", shop_id)


def financial_stats(db: OrmSession, shop_id: str | None = None) -> dict[str, int]:
    """Toutes les statistiques financieres sont DERIVEES du journal (source unique)."""
    return {
        "gmv_xof": _sum(db, "gmv", shop_id),
        "commission_generated_xof": _sum(db, "platform_revenue", shop_id),
        "commission_collected_xof": _sum(db, "cash", shop_id, "settlement"),
        "commission_collected_net_xof": _sum(db, "cash", shop_id),
        "commission_outstanding_xof": _sum(db, "commission_receivable", shop_id),
        "vendor_payable_xof": _sum(db, "vendor_payable", shop_id),
        "payment_fees_xof": _sum(db, "payment_expense", shop_id),
        "delivery_payable_xof": _sum(db, "delivery_payable", shop_id),
        "cash_xof": _sum(db, "cash", shop_id),
        "ledger_entries": int(
            db.execute(
                select(func.count(LedgerEntry.id)).where(LedgerEntry.shop_id == shop_id)
                if shop_id else select(func.count(LedgerEntry.id))
            ).scalar_one()
        ),
    }
