"""API JSON vendeur : boutique, produits, declarations, statistiques, paiements."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session as OrmSession

from .. import schemas
from ..db import get_db
from ..models import (
    Commission,
    Notification,
    PaymentMethod,
    Product,
    ProductImage,
    ProductStatus,
    Shop,
    User,
    uid,
    utcnow,
)
from ..security import api_vendor, client_ip, verify_csrf
from ..services import audit, ledger, orders as order_service, stats

router = APIRouter(prefix="/api/vendor", tags=["vendor"])


def _shop(db: OrmSession, user: User) -> Shop:
    shop = db.execute(select(Shop).where(Shop.owner_id == user.id)).scalar_one_or_none()
    if not shop:
        raise HTTPException(404, "Boutique non creee.")
    return shop


@router.get("/shop")
def get_shop(db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    s = _shop(db, user)
    return {"id": s.id, "slug": s.slug, "name": s.name, "status": s.status,
            "whatsapp_e164": s.whatsapp_e164, "description": s.description,
            "theme_color": s.theme_color, "public_url": f"/boutique/{s.slug}",
            "commission_bps_override": s.commission_bps_override}


@router.put("/shop")
def update_shop(payload: schemas.ShopIn, request: Request, db: OrmSession = Depends(get_db),
                user: User = Depends(api_vendor)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    s = _shop(db, user)
    conflict = db.execute(select(Shop).where(Shop.slug == payload.slug, Shop.id != s.id)).scalar_one_or_none()
    if conflict:
        raise HTTPException(409, "Slug deja utilise.")
    before = {"name": s.name, "slug": s.slug}
    s.name, s.slug, s.description = payload.name, payload.slug, payload.description
    s.city, s.theme_color = payload.city, payload.theme_color
    if payload.whatsapp:
        from ..security import normalize_phone
        s.whatsapp_e164 = normalize_phone(payload.whatsapp) or s.whatsapp_e164
    audit.log_action(db, action="shop.updated", actor=user, entity_type="shop", entity_id=s.id,
                     before=before, after={"name": s.name, "slug": s.slug}, ip=client_ip(request))
    db.commit()
    return {"status": "updated", "public_url": f"/boutique/{s.slug}"}


@router.get("/products")
def list_products(db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    s = _shop(db, user)
    rows = list(db.execute(select(Product).where(Product.shop_id == s.id)
                           .order_by(desc(Product.created_at))).scalars())
    return [{"id": p.id, "title": p.title, "slug": p.slug, "price_xof": p.price_xof,
             "stock": p.stock, "status": p.status, "view_count": p.view_count} for p in rows]


@router.post("/products", status_code=201)
def create_product(payload: schemas.ProductIn, request: Request, db: OrmSession = Depends(get_db),
                   user: User = Depends(api_vendor)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    s = _shop(db, user)
    slug = re.sub(r"[^a-z0-9]+", "-", payload.title.lower()).strip("-")[:60]
    base, n = slug, 1
    while db.execute(select(Product).where(Product.shop_id == s.id, Product.slug == slug)).scalar_one_or_none():
        n += 1
        slug = f"{base}-{n}"
    p = Product(id=uid(), shop_id=s.id, category_id=payload.category_id, slug=slug,
                title=payload.title, description=payload.description, price_xof=payload.price_xof,
                stock=payload.stock, delivery_fee_xof=payload.delivery_fee_xof,
                compare_at_price_xof=payload.compare_at_price_xof, sku=payload.sku,
                video_url=payload.video_url, status=ProductStatus.ACTIVE, created_at=utcnow())
    db.add(p)
    audit.log_action(db, action="product.created", actor=user, entity_type="product", entity_id=p.id,
                     after={"title": p.title, "price": p.price_xof}, ip=client_ip(request))
    db.commit()
    return {"id": p.id, "slug": p.slug, "public_url": f"/boutique/{s.slug}/produit/{p.slug}"}


@router.put("/products/{product_id}")
def update_product(product_id: str, payload: schemas.ProductIn, request: Request,
                   db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    s = _shop(db, user)
    p = db.get(Product, product_id)
    if not p or p.shop_id != s.id:
        raise HTTPException(404, "Produit introuvable.")
    before = {"title": p.title, "price": p.price_xof}
    for field in ("title", "description", "price_xof", "stock", "delivery_fee_xof",
                  "category_id", "compare_at_price_xof", "sku", "video_url"):
        setattr(p, field, getattr(payload, field))
    audit.log_action(db, action="product.updated", actor=user, entity_type="product", entity_id=p.id,
                     before=before, after={"title": p.title, "price": p.price_xof}, ip=client_ip(request))
    db.commit()
    return {"status": "updated"}


@router.get("/stats")
def vendor_stats(db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    s = _shop(db, user)
    d = stats.shop_dashboard(db, s.id)
    return {k: v for k, v in d.items() if k in {"fin", "products", "orders", "orders_paid",
                                               "inquiries", "views", "commission_due",
                                               "commission_paid", "by_status", "rate_bps"}}


@router.get("/commissions")
def commissions(db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    s = _shop(db, user)
    rows = list(db.execute(select(Commission).where(Commission.shop_id == s.id)
                           .order_by(desc(Commission.calculated_at))).scalars())
    return {"balance_xof": ledger.commission_balance(db, s.id),
            "fin": ledger.financial_stats(db, s.id),
            "items": [{"order_id": c.order_id, "gross_xof": c.gross_xof, "rate_bps": c.rate_bps,
                       "platform_fee_xof": c.platform_fee_xof, "payment_fee_xof": c.payment_fee_xof,
                       "net_vendor_xof": c.net_vendor_xof, "status": c.status,
                       "calculated_at": c.calculated_at.isoformat()} for c in rows]}


@router.post("/declarations", status_code=201)
def declare(payload: schemas.DeclarationIn, request: Request, db: OrmSession = Depends(get_db),
            user: User = Depends(api_vendor)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    s = _shop(db, user)
    try:
        order = order_service.declare_sale(db, shop=s, vendor=user, **payload.model_dump(),
                                           ip=client_ip(request))
    except order_service.OrderError as exc:
        raise HTTPException(400, str(exc))
    return {"reference": order.reference, "status": order.status,
            "payment_status": order.payment_status,
            "message": "Vente declaree, paiement NON confirme. Verification admin requise."}


@router.get("/payment-methods")
def payment_methods(db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    s = _shop(db, user)
    return [{"id": m.id, "provider": m.provider, "label": m.label, "phone": m.phone_e164,
             "account_ref": m.account_ref, "is_verified": m.is_verified, "is_active": m.is_active}
            for m in db.execute(select(PaymentMethod).where(PaymentMethod.shop_id == s.id)).scalars()]


@router.post("/payment-methods", status_code=201)
def add_payment_method(request: Request, provider: str, label: str, phone: str = "",
                       account_ref: str = "", db: OrmSession = Depends(get_db),
                       user: User = Depends(api_vendor)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    if provider not in {"wave", "orange_money", "free_money", "bank", "cash"}:
        raise HTTPException(400, "Fournisseur invalide.")
    s = _shop(db, user)
    from ..security import normalize_phone
    normalized = normalize_phone(phone) if phone else ""
    if phone and not normalized:
        raise HTTPException(400, "Numero invalide.")
    m = PaymentMethod(id=uid(), shop_id=s.id, provider=provider, label=label[:120],
                      phone_e164=normalized, account_ref=account_ref[:150], is_verified=False,
                      created_at=utcnow())
    db.add(m)
    db.commit()
    return {"id": m.id, "status": "pending_verification"}


@router.get("/notifications")
def notifications_list(db: OrmSession = Depends(get_db), user: User = Depends(api_vendor)):
    rows = list(db.execute(select(Notification).where(Notification.user_id == user.id)
                           .order_by(desc(Notification.created_at)).limit(50)).scalars())
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body,
             "link": n.link, "is_read": n.is_read, "created_at": n.created_at.isoformat()} for n in rows]
