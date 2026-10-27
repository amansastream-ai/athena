"""Amorcage : categories, comptes de demonstration, donnees realistes.

En production, ce module n'est JAMAIS appele automatiquement. Utiliser
`python -m scripts.seed --admin` avec JAAY_BOOTSTRAP_ADMIN_* dans l'environnement.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .config import settings
from .db import Base, SessionLocal, engine, ensure_append_only_protection
from .models import (
    Category,
    Order,
    PlatformSetting,
    Product,
    ProductImage,
    Role,
    Shop,
    ShopStatus,
    User,
    uid,
    utcnow,
)
from .security import hash_password, normalize_phone
from .services import orders as order_service
from .services import payments as payment_service
from .services.registry import DEFAULT_SETTINGS

DEMO_ADMIN = ("admin@jaay.local", "AdminJaay2026!", "+221771110001")
DEMO_VENDOR_1 = ("fatou@jaay.local", "VendeurJaay2026!", "+221771110002")
DEMO_VENDOR_2 = ("moussa@jaay.local", "VendeurJaay2026!", "+221771110003")
DEMO_CUSTOMER = ("client@jaay.local", "ClientJaay2026!", "+221771110004")

CATEGORIES = [
    ("mode", "Mode & vetements"),
    ("electronique", "Electronique"),
    ("maison", "Maison & decoration"),
    ("beaute", "Beaute & soins"),
    ("alimentation", "Alimentation"),
    ("artisanat", "Artisanat"),
    ("sport", "Sport & loisirs"),
    ("services", "Services"),
]


def init_db() -> None:
    Base.metadata.create_all(engine)
    ensure_append_only_protection()
    with SessionLocal() as db:
        for key, value in DEFAULT_SETTINGS.items():
            if not db.get(PlatformSetting, key):
                db.add(PlatformSetting(key=key, value_json=json.dumps(value), label=key))
        for slug, name in CATEGORIES:
            if not db.execute(select(Category).where(Category.slug == slug)).scalar_one_or_none():
                db.add(Category(id=uid(), slug=slug, name=name))
        db.commit()


def _user(db: OrmSession, email: str, password: str, phone: str, name: str, role: str) -> User:
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return existing
    user = User(
        id=uid(), email=email, phone_e164=normalize_phone(phone) or phone, full_name=name,
        password_hash=hash_password(password), role=role, phone_verified=True,
        is_active=True, created_at=utcnow(),
    )
    db.add(user)
    db.flush()
    return user


def _product(db: OrmSession, shop: Shop, *, title: str, price: int, cat: str,
             stock: int = 10, description: str = "", delivery: int = 1000, slug: str = "") -> Product:
    p = Product(
        id=uid(), shop_id=shop.id,
        category_id=db.execute(select(Category.id).where(Category.slug == cat)).scalar_one(),
        slug=slug or "-".join(title.lower().split())[:60], title=title,
        description=description or f"{title} — disponible a Dakar. Contactez la boutique sur WhatsApp pour les details.",
        price_xof=price, stock=stock, delivery_fee_xof=delivery, status="active",
        view_count=0, created_at=utcnow(),
    )
    db.add(p)
    db.flush()
    return p


def seed_demo() -> None:
    """Cree un jeu de donnees de demonstration (uniquement si la base est vide)."""
    with SessionLocal() as db:
        if db.execute(select(User.id).limit(1)).first():
            return
        admin = _user(db, *DEMO_ADMIN, "Administrateur Jaay", Role.ADMIN)
        v1 = _user(db, *DEMO_VENDOR_1, "Fatou Diop", Role.VENDOR)
        v2 = _user(db, *DEMO_VENDOR_2, "Moussa Fall", Role.VENDOR)
        customer = _user(db, *DEMO_CUSTOMER, "Awa Ndoye", Role.CUSTOMER)
        db.flush()

        s1 = Shop(
            id=uid(), owner_id=v1.id, slug="teranga-style", name="Teranga Style",
            description="Mode africaine, tissus wax et accessoires faits main a Dakar. Livraison partout au Senegal.",
            whatsapp_e164=DEMO_VENDOR_1[2], city="Dakar", country="SN", status=ShopStatus.ACTIVE,
            theme_color="#b45309", created_at=utcnow(), approved_at=utcnow(),
        )
        s2 = Shop(
            id=uid(), owner_id=v2.id, slug="dakar-tech", name="Dakar Tech",
            description="Telephones, accessoires et petit electromenager garantis. Prix grossiste.",
            whatsapp_e164=DEMO_VENDOR_2[2], city="Pikine", country="SN", status=ShopStatus.ACTIVE,
            theme_color="#1d4ed8", created_at=utcnow(), approved_at=utcnow(),
        )
        db.add_all([s1, s2])
        db.flush()

        _product(db, s1, title="Robe wax Africaine", price=1000, cat="mode", slug="robe-wax-africaine")
        _product(db, s1, title="Boubou brode premium", price=10000, cat="mode", slug="boubou-brode-premium")
        _product(db, s1, title="Sac en cuir fait main", price=5000, cat="artisanat", slug="sac-cuir-fait-main")
        _product(db, s1, title="Bijoux en perles", price=2500, cat="beaute", slug="bijoux-perles")
        p1 = _product(db, s2, title="Ecouteurs Bluetooth", price=50000, cat="electronique", slug="ecouteurs-bluetooth")
        _product(db, s2, title="Chargeur rapide 25W", price=3500, cat="electronique", slug="chargeur-rapide-25w")
        _product(db, s2, title="Montre connectee", price=25000, cat="electronique", slug="montre-connectee")
        _product(db, s2, title="Enceinte portable", price=15000, cat="maison", slug="enceinte-portable")
        db.commit()

        # Vente de demonstration passant par le VRAI parcours de verification.
        try:
            order = order_service.create_order(
                db, shop=s1,
                items=[{"product_id": db.execute(
                    select(Product.id).where(Product.slug == "boubou-brode-premium")
                ).scalar_one(), "quantity": 1}],
                customer_name="Awa Ndoye", customer_phone=DEMO_CUSTOMER[2],
                customer_email=DEMO_CUSTOMER[0], delivery_method="delivery",
                delivery_address="Pikine, Dakar", idempotency_key="demo-order-1", actor=customer,
            )
            proof = payment_service.submit_proof(
                db, order, user=customer, provider="wave",
                provider_reference="WAVE-DEMO-0001", amount_xof=order.total_xof,
                note="Paiement de demonstration",
            )
            payment_service.review_proof(db, proof, admin=admin, approve=True,
                                         review_note="Paiement verifie avec l'operateur (demo).")
        except Exception as exc:  # pragma: no cover - amortgage best effort
            print(f"[seed] commande demo ignoree : {exc}")
            db.rollback()
        print("[seed] donnees de demonstration creees.")


def bootstrap_admin() -> None:
    """Cree le compte administrateur initial depuis les variables d'environnement."""
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        raise RuntimeError("Definissez JAAY_BOOTSTRAP_ADMIN_EMAIL et JAAY_BOOTSTRAP_ADMIN_PASSWORD.")
    init_db()
    with SessionLocal() as db:
        if db.execute(select(User).where(User.email == settings.bootstrap_admin_email)).scalar_one_or_none():
            print("Compte administrateur deja existant.")
            return
        phone = normalize_phone(settings.bootstrap_admin_phone) if settings.bootstrap_admin_phone else None
        if not phone:
            raise RuntimeError("JAAY_BOOTSTRAP_ADMIN_PHONE doit etre un numero senegalais valide.")
        db.add(User(
            id=uid(), email=settings.bootstrap_admin_email, phone_e164=phone,
            full_name="Administrateur", password_hash=hash_password(settings.bootstrap_admin_password),
            role=Role.ADMIN, phone_verified=True, created_at=utcnow(),
        ))
        db.commit()
        print("Compte administrateur cree. Supprimez les variables de bootstrap de l'environnement.")
