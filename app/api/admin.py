"""API JSON administrateur : pilotage, parametres, validation, audit."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session as OrmSession

from .. import schemas
from ..db import get_db
from ..models import Commission, LedgerEntry, Order, PaymentProof, Shop, User, WebhookEvent
from ..security import api_admin, client_ip, verify_csrf
from ..services import audit, fraud, ledger, orders as order_service, payments, stats
from ..services.registry import all_settings, get_setting, parse_setting_value, set_setting

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats")
def admin_stats(db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    d = stats.admin_dashboard(db, user.id)
    return {k: v for k, v in d.items() if isinstance(v, (int, float, str, bool, dict))}


@router.get("/settings")
def settings_list(db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    return all_settings(db)


@router.put("/settings")
def settings_update(payload: schemas.SettingIn, request: Request, db: OrmSession = Depends(get_db),
                    user: User = Depends(api_admin)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    before = get_setting(db, payload.key)
    try:
        value = parse_setting_value(payload.key, payload.value)
        if payload.key == "commission_bps" and not 0 <= value <= 10000:
            raise ValueError("Taux hors bornes (0-10000 bps).")
        if payload.key == "commission_trigger" and value not in {"payment_confirmed", "delivered"}:
            raise ValueError("Declencheur invalide.")
        set_setting(db, payload.key, value, actor_id=user.id)
        audit.log_action(db, action="setting.changed", actor=user, entity_type="setting",
                         entity_id=payload.key, before={"value": before}, after={"value": value},
                         ip=client_ip(request))
        db.commit()
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    return {"key": payload.key, "value": value, "previous": before}


@router.post("/proofs/{proof_id}/review")
def review_proof(proof_id: str, approve: bool, note: str = "", request: Request = None,
                 db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    proof = db.get(PaymentProof, proof_id)
    if not proof:
        raise HTTPException(404, "Preuve introuvable.")
    try:
        payments.review_proof(db, proof, admin=user, approve=approve, review_note=note,
                              ip=client_ip(request))
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    order = db.get(Order, proof.order_id)
    return {"order": order.reference, "payment_status": order.payment_status,
            "commission": bool(db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none())}


@router.post("/settlements", status_code=201)
def settlement(shop_id: str, payload: schemas.CommissionSettlementIn, request: Request,
               db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    shop = db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(404, "Boutique inconnue.")
    try:
        row = ledger.record_settlement(db, shop, amount_xof=payload.amount_xof, method=payload.method,
                                       reference=payload.reference, note=payload.note, actor=user)
        audit.log_action(db, action="commission.settlement", actor=user, entity_type="shop",
                         entity_id=shop.id, after=payload.model_dump(), ip=client_ip(request))
        db.commit()
    except ledger.LedgerError as exc:
        raise HTTPException(400, str(exc))
    return {"id": row.id, "balance_xof": ledger.commission_balance(db, shop.id)}


@router.post("/shops/{shop_id}/status")
def shop_status(shop_id: str, status: str, request: Request, db: OrmSession = Depends(get_db),
                user: User = Depends(api_admin)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    shop = db.get(Shop, shop_id)
    if not shop or status not in {"active", "pending", "suspended"}:
        raise HTTPException(400, "Donnees invalides.")
    before = shop.status
    shop.status = status
    audit.log_action(db, action="shop.status_changed", actor=user, entity_type="shop", entity_id=shop.id,
                     before={"status": before}, after={"status": status}, ip=client_ip(request))
    db.commit()
    return {"status": status}


@router.post("/shops/{shop_id}/commission-bps")
def shop_rate(shop_id: str, bps: int | None, request: Request, db: OrmSession = Depends(get_db),
              user: User = Depends(api_admin)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    shop = db.get(Shop, shop_id)
    if not shop or (bps is not None and not 0 <= bps <= 10000):
        raise HTTPException(400, "Donnees invalides.")
    before = shop.commission_bps_override
    shop.commission_bps_override = bps
    audit.log_action(db, action="shop.commission_override", actor=user, entity_type="shop",
                     entity_id=shop.id, before={"bps": before}, after={"bps": bps}, ip=client_ip(request))
    db.commit()
    return {"commission_bps_override": bps}


@router.get("/ledger/verify")
def verify_ledger(db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    ok, msg = ledger.verify_chain(db)
    aok, amsg = audit.verify_chain(db)
    return {"ledger_chain_valid": ok, "ledger_message": msg, "audit_chain_valid": aok,
            "audit_message": amsg, "financial_stats": ledger.financial_stats(db)}


@router.get("/ledger")
def ledger_entries(limit: int = 100, db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    rows = list(db.execute(select(LedgerEntry).order_by(desc(LedgerEntry.id)).limit(min(limit, 500))).scalars())
    return [{"id": e.id, "entry_type": e.entry_type, "account": e.account,
             "amount_xof": e.amount_xof, "order_id": e.order_id, "shop_id": e.shop_id,
             "description": e.description, "entry_hash": e.entry_hash,
             "prev_hash": e.prev_hash, "created_at": e.created_at.isoformat()} for e in rows]


@router.get("/fraud")
def fraud_report(db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    rep = fraud.anomaly_report(db)
    return {
        "shops": [{"id": r["shop"].id, "name": r["shop"].name, "risk_score": r["shop"].risk_score,
                   "inquiries": r["inquiries"], "declared": r["declared"],
                   "conversion_pct": r["conversion"]} for r in rep["shops"]],
        "flagged_orders": [{"id": o.id, "reference": o.reference, "risk_score": o.risk_score,
                            "signals": o.risk_signals_json, "status": o.status}
                           for o in rep["flagged_orders"]],
    }


@router.get("/webhooks")
def webhook_events(db: OrmSession = Depends(get_db), user: User = Depends(api_admin)):
    rows = list(db.execute(select(WebhookEvent).order_by(desc(WebhookEvent.received_at)).limit(100)).scalars())
    return [{"provider": e.provider, "event_id": e.event_id, "event_type": e.event_type,
             "status": e.status, "signature_valid": e.signature_valid, "error": e.error,
             "received_at": e.received_at.isoformat()} for e in rows]
