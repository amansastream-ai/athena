"""API JSON publique : catalogue, boutiques, recherche, assistance IA, favoris."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import schemas
from ..db import get_db
from ..models import Category, Favorite, Inquiry, Product, ProductImage, Review, Shop, ShopStatus, uid, utcnow
from ..security import api_current_user, verify_csrf
from ..services import ai, stats, whatsapp

router = APIRouter(prefix="/api", tags=["public"])


@router.get("/products")
def products(q: str = "", category: str = "", min_price: int | None = None, max_price: int | None = None,
             in_stock: bool = False, shop: str = "", sort: str = "recent", page: int = 1,
             per_page: int = Query(12, le=60), db: OrmSession = Depends(get_db)):
    shop_id = None
    if shop:
        s = db.execute(select(Shop).where(Shop.slug == shop)).scalar_one_or_none()
        shop_id = s.id if s else "__none__"
    data = stats.catalog(db, q=q, category=category, min_price=min_price, max_price=max_price,
                         in_stock=in_stock, shop_id=shop_id, sort=sort, page=page, per_page=per_page)
    return {
        "total": data["total"], "page": data["page"], "pages": data["pages"],
        "results": [
            {
                "id": p.id, "title": p.title, "slug": p.slug, "price_xof": p.price_xof,
                "stock": p.stock, "delivery_fee_xof": p.delivery_fee_xof,
                "image": data["images"].get(p.id), "shop_id": p.shop_id,
                "shop_slug": data["shop_slugs"].get(p.shop_id, ""),
                "url": f"/boutique/{data['shop_slugs'].get(p.shop_id, '')}/produit/{p.slug}",
            }
            for p in data["items"]
        ],
    }


@router.get("/categories")
def categories(db: OrmSession = Depends(get_db)):
    return [{"id": c.id, "slug": c.slug, "name": c.name}
            for c in db.execute(select(Category).order_by(Category.name)).scalars()]


@router.get("/shops/{slug}")
def shop_detail(slug: str, db: OrmSession = Depends(get_db)):
    shop = db.execute(select(Shop).where(Shop.slug == slug, Shop.status == ShopStatus.ACTIVE)).scalar_one_or_none()
    if not shop:
        raise HTTPException(404, "Boutique introuvable.")
    products = stats.catalog(db, shop_id=shop.id, per_page=60)
    return {
        "id": shop.id, "slug": shop.slug, "name": shop.name, "description": shop.description,
        "whatsapp_e164": shop.whatsapp_e164, "city": shop.city, "theme_color": shop.theme_color,
        "products": [{"id": p.id, "title": p.title, "slug": p.slug, "price_xof": p.price_xof,
                      "image": products["images"].get(p.id)} for p in products["items"]],
    }


@router.get("/shops/{slug}/whatsapp-link")
def shop_whatsapp_link(slug: str, request: Request, db: OrmSession = Depends(get_db)):
    shop = db.execute(select(Shop).where(Shop.slug == slug)).scalar_one_or_none()
    if not shop:
        raise HTTPException(404)
    token = whatsapp.shop_token(shop)
    return {"tracked_url": whatsapp.absolute_url(request, f"/wa/{token}"),
            "note": "Le lien trace le clic puis redirige vers wa.me ; la conversation reste privee."}


@router.post("/ai/assist")
def ai_assist(payload: schemas.AiAssistIn, request: Request, db: OrmSession = Depends(get_db)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    result = ai.assist_product(payload.title, keywords=payload.keywords,
                               category=payload.category, language=payload.language)
    result["price_hint"] = ai.suggest_price(db, title=payload.title)
    return result


@router.post("/favorites/{product_id}")
def favorite_toggle(product_id: str, request: Request, db: OrmSession = Depends(get_db),
                    user=Depends(api_current_user)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    fav = db.get(Favorite, (user.id, product_id))
    if fav:
        db.delete(fav)
        active = False
    else:
        db.add(Favorite(user_id=user.id, product_id=product_id, created_at=utcnow()))
        active = True
    db.commit()
    return {"active": active}
