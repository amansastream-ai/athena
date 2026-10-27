"""Agregations pour le catalogue et les tableaux de bord (SQL, pas de calcul client)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ..models import (
    Category,
    Commission,
    CommissionStatus,
    Inquiry,
    Notification,
    Order,
    OrderStatus,
    PaymentProof,
    PaymentStatus,
    Product,
    ProductImage,
    Review,
    Shop,
    User,
    utcnow,
)
from . import ledger
from .registry import all_settings


def catalog(
    db: OrmSession,
    *,
    q: str = "",
    category: str = "",
    min_price: int | None = None,
    max_price: int | None = None,
    in_stock: bool = False,
    shop_id: str | None = None,
    sort: str = "recent",
    page: int = 1,
    per_page: int = 12,
) -> dict:
    stmt = select(Product).join(Shop, Shop.id == Product.shop_id).where(
        Product.status == "active", Shop.status == "active"
    )
    if q:
        stmt = stmt.where(Product.title.ilike(f"%{q[:80]}%") | Product.description.ilike(f"%{q[:80]}%"))
    if category:
        cat = db.execute(select(Category).where(Category.slug == category)).scalar_one_or_none()
        if cat:
            stmt = stmt.where(Product.category_id == cat.id)
    if min_price is not None:
        stmt = stmt.where(Product.price_xof >= min_price)
    if max_price is not None:
        stmt = stmt.where(Product.price_xof <= max_price)
    if in_stock:
        stmt = stmt.where(Product.stock > 0)
    if shop_id:
        stmt = stmt.where(Product.shop_id == shop_id)

    if sort == "price_asc":
        stmt = stmt.order_by(Product.price_xof)
    elif sort == "price_desc":
        stmt = stmt.order_by(Product.price_xof.desc())
    elif sort == "popular":
        stmt = stmt.order_by(Product.view_count.desc())
    else:
        stmt = stmt.order_by(Product.created_at.desc())

    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    items = list(db.execute(stmt.offset((max(page, 1) - 1) * per_page).limit(per_page)).scalars())
    images: dict[str, str] = {}
    shop_slugs: dict[str, str] = {}
    if items:
        rows = db.execute(
            select(ProductImage).where(ProductImage.product_id.in_([p.id for p in items]))
            .order_by(ProductImage.position)
        ).scalars()
        for im in rows:
            images.setdefault(im.product_id, im.url)
        for sid, slug in db.execute(
            select(Shop.id, Shop.slug).where(Shop.id.in_({p.shop_id for p in items}))
        ).all():
            shop_slugs[sid] = slug
    return {
        "items": items,
        "images": images,
        "shop_slugs": shop_slugs,
        "total": total,
        "page": page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


def shop_dashboard(db: OrmSession, shop_id: str) -> dict:
    fin = ledger.financial_stats(db, shop_id)
    orders = list(
        db.execute(select(Order).where(Order.shop_id == shop_id).order_by(Order.created_at.desc()).limit(8)).scalars()
    )
    by_status = dict(
        db.execute(
            select(Order.status, func.count(Order.id)).where(Order.shop_id == shop_id).group_by(Order.status)
        ).all()
    )
    top = list(
        db.execute(
            select(Product).where(Product.shop_id == shop_id)
            .order_by(Product.view_count.desc()).limit(5)
        ).scalars()
    )
    since = utcnow() - timedelta(days=14)
    daily = dict(
        db.execute(
            select(func.date(Order.created_at), func.count(Order.id))
            .where(Order.shop_id == shop_id, Order.created_at >= since)
            .group_by(func.date(Order.created_at))
        ).all()
    )
    labels = [(utcnow() - timedelta(days=13 - i)).strftime("%Y-%m-%d") for i in range(14)]
    chart = [int(daily.get(label, 0)) for label in labels]

    return {
        "fin": fin,
        "chart": chart,
        "chart_labels": labels,
        "products": int(db.execute(select(func.count(Product.id)).where(Product.shop_id == shop_id)).scalar_one()),
        "orders": int(db.execute(select(func.count(Order.id)).where(Order.shop_id == shop_id)).scalar_one()),
        "orders_paid": int(
            db.execute(select(func.count(Order.id)).where(
                Order.shop_id == shop_id, Order.payment_status == PaymentStatus.PAID
            )).scalar_one()
        ),
        "inquiries": int(db.execute(select(func.count(Inquiry.id)).where(Inquiry.shop_id == shop_id)).scalar_one()),
        "views": sum(p.view_count for p in top),
        "commission_due": int(
            db.execute(select(func.coalesce(func.sum(Commission.platform_fee_xof), 0)).where(
                Commission.shop_id == shop_id, Commission.status == CommissionStatus.DUE
            )).scalar_one()
        ),
        "commission_paid": int(
            db.execute(select(func.coalesce(func.sum(Commission.platform_fee_xof), 0)).where(
                Commission.shop_id == shop_id, Commission.status == CommissionStatus.PAID
            )).scalar_one()
        ),
        "by_status": by_status,
        "recent_orders": orders,
        "top_products": top,
        "daily": daily,
        "rate_bps": all_settings(db).get("commission_bps", 1000),
    }


def admin_dashboard(db: OrmSession, admin_id: str | None = None) -> dict:
    fin = ledger.financial_stats(db)
    pending_proofs = list(
        db.execute(
            select(PaymentProof).where(PaymentProof.status == "pending").order_by(PaymentProof.created_at.desc())
        ).scalars()
    )
    declarations = list(
        db.execute(
            select(Order).where(Order.origin == "vendor_declaration",
                                Order.status == OrderStatus.PENDING_VERIFICATION)
            .order_by(Order.created_at.desc()).limit(20)
        ).scalars()
    )
    per_shop = list(
        db.execute(
            select(Shop.name, Shop.id, func.coalesce(func.sum(Commission.platform_fee_xof), 0),
                   func.count(Commission.id))
            .outerjoin(Commission, Commission.shop_id == Shop.id)
            .group_by(Shop.id, Shop.name)
            .order_by(func.coalesce(func.sum(Commission.platform_fee_xof), 0).desc()).limit(10)
        ).all()
    )
    notif_query = select(Notification).order_by(Notification.created_at.desc()).limit(12)
    if admin_id:
        notif_query = notif_query.where(Notification.user_id == admin_id)
    chain_ok, chain_msg = ledger.verify_chain(db)
    from .audit import verify_chain as audit_chain
    audit_ok, audit_msg = audit_chain(db)
    return {
        "fin": fin,
        "shops": int(db.execute(select(func.count(Shop.id))).scalar_one()),
        "shops_pending": int(
            db.execute(select(func.count(Shop.id)).where(Shop.status == "pending")).scalar_one()
        ),
        "vendors": int(db.execute(select(func.count(User.id)).where(User.role == "vendor")).scalar_one()),
        "customers": int(db.execute(select(func.count(User.id)).where(User.role == "customer")).scalar_one()),
        "products": int(db.execute(select(func.count(Product.id))).scalar_one()),
        "orders": int(db.execute(select(func.count(Order.id))).scalar_one()),
        "inquiries": int(db.execute(select(func.count(Inquiry.id))).scalar_one()),
        "pending_proofs": pending_proofs,
        "declarations": declarations,
        "per_shop": per_shop,
        "notifications": list(db.execute(notif_query).scalars()),
        "ledger_chain_ok": chain_ok,
        "audit_chain_ok": audit_ok,
        "chain_message": chain_msg if not chain_ok else audit_msg,
        "settings": all_settings(db),
    }
