"""API JSON : commandes, preuves de paiement, transitions, avis."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import schemas
from ..db import get_db
from ..models import Order, OrderStatus, PaymentProof, Product, Review, Shop, User, uid, utcnow
from ..security import api_current_user, api_role, client_ip, current_user_optional, verify_csrf
from ..services import orders as order_service, payments, ledger
from ..models import Role

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_by_ref(db: OrmSession, reference: str) -> Order:
    order = db.execute(select(Order).where(Order.reference == reference)).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Commande introuvable.")
    return order


@router.post("", status_code=201)
def create_order(payload: schemas.OrderIn, request: Request, db: OrmSession = Depends(get_db)):
    verify_csrf(request, db, payload.csrf_token or request.headers.get("x-csrf-token"))
    first = db.get(Product, payload.items[0].product_id)
    if not first:
        raise HTTPException(404, "Produit introuvable.")
    shop = db.get(Shop, first.shop_id)
    if not shop:
        raise HTTPException(404, "Boutique introuvable.")
    try:
        order = order_service.create_order(
            db, shop=shop, items=[i.model_dump() for i in payload.items],
            customer_name=payload.customer_name, customer_phone=payload.customer_phone,
            customer_email=payload.customer_email, delivery_method=payload.delivery_method,
            delivery_address=payload.delivery_address, claim_code=payload.claim_code,
            coupon_code=payload.coupon_code, idempotency_key=payload.idempotency_key,
            actor=current_user_optional(request, db), ip=client_ip(request),
        )
    except order_service.OrderError as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": order.id, "reference": order.reference, "total_xof": order.total_xof,
        "status": order.status, "payment_status": order.payment_status,
        "url": f"/commandes/{order.reference}",
        "warning": "Commande creee. Le paiement n'est PAS confirme.",
    }


@router.get("/{reference}")
def order_detail(reference: str, request: Request, db: OrmSession = Depends(get_db)):
    order = _get_by_ref(db, reference)
    view = order_service.order_view(db, order)
    return {
        "reference": order.reference,
        "status": order.status,
        "payment_status": order.payment_status,
        "total_xof": order.total_xof,
        "subtotal_xof": order.subtotal_xof,
        "delivery_fee_xof": order.delivery_fee_xof,
        "created_at": order.created_at.isoformat(),
        "items": [{"title": i.title_snapshot, "quantity": i.quantity,
                   "unit_price_xof": i.unit_price_xof} for i in view["items"]],
        "commission": ({
            "rate_bps": view["commission"].rate_bps,
            "platform_fee_xof": view["commission"].platform_fee_xof,
            "payment_fee_xof": view["commission"].payment_fee_xof,
            "net_vendor_xof": view["commission"].net_vendor_xof,
            "status": view["commission"].status,
        } if view["commission"] else None),
        "risk_score": order.risk_score,
        "risk_signals": view["risk_signals"],
    }


@router.post("/{reference}/proof")
def submit_proof(reference: str, payload: schemas.ProofIn, request: Request,
                 db: OrmSession = Depends(get_db)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    order = _get_by_ref(db, reference)
    try:
        proof = payments.submit_proof(
            db, order, user=current_user_optional(request, db), provider=payload.provider,
            provider_reference=payload.provider_reference, amount_xof=payload.amount_xof,
            note=payload.note, ip=client_ip(request),
        )
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    return {"proof_id": proof.id, "status": proof.status,
            "message": "Preuve enregistree. Validation administrateur requise."}


@router.post("/{order_id}/transition")
def transition(request: Request, order_id: str, status: str, csrf_token: str = "", note: str = "",
               db: OrmSession = Depends(get_db), user: User = Depends(api_current_user)):
    verify_csrf(request, db, csrf_token or request.headers.get("x-csrf-token"))
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Commande introuvable.")
    shop = db.get(Shop, order.shop_id)
    if user.role == Role.VENDOR:
        if not shop or shop.owner_id != user.id:
            raise HTTPException(403, "Cette commande appartient a une autre boutique.")
        if status not in {"confirmed", "preparing", "shipped", "delivered", "cancelled"}:
            raise HTTPException(403, "Transition non autorisee pour un vendeur.")
    elif user.role != Role.ADMIN:
        raise HTTPException(403)
    try:
        order_service.transition(db, order, status, actor=user, note=note, ip=client_ip(request))
    except order_service.OrderError as exc:
        raise HTTPException(400, str(exc))
    return {"status": order.status, "payment_status": order.payment_status}


@router.post("/{reference}/review")
def add_review(reference: str, payload: schemas.ReviewIn, request: Request,
               db: OrmSession = Depends(get_db), user: User = Depends(api_current_user)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    order = _get_by_ref(db, reference)
    if order.customer_id != user.id:
        raise HTTPException(403, "Seul l'acheteur peut noter cette commande.")
    if order.payment_status != "paid":
        raise HTTPException(400, "Avis reserve aux achats confirmes.")
    from ..models import OrderItem
    item = db.execute(select(OrderItem).where(OrderItem.order_id == order.id).limit(1)).scalar_one_or_none()
    if not item:
        raise HTTPException(400, "Aucun article.")
    if db.execute(select(Review).where(Review.order_id == order.id, Review.product_id == item.product_id)).first():
        raise HTTPException(409, "Avis deja depose.")
    db.add(Review(id=uid(), product_id=item.product_id, shop_id=order.shop_id, order_id=order.id,
                  user_id=user.id, rating=payload.rating, comment=payload.comment,
                  status="approved", created_at=utcnow()))
    db.commit()
    return {"status": "approved"}
