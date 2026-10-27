"""Modele de donnees relationnel (SQLAlchemy 2.x, compatible SQLite et PostgreSQL).

Principes :
 - tous les montants sont des ENTIERS en francs CFA (XOF) : pas de virgule flottante ;
 - toutes les tables metier portent shop_id pour l'isolation multi-vendeurs ;
 - les prix et commissions sont recalcules cote serveur, jamais accepts du client ;
 - ledger_entries et audit_logs sont append-only (triggersSQL, voir db.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uid() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def in_hours(hours: int = 24) -> datetime:
    return utcnow() + timedelta(hours=hours)


class Role:
    ADMIN = "admin"
    VENDOR = "vendor"
    CUSTOMER = "customer"


class ShopStatus:
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ProductStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    MODERATED = "moderated"


class OrderStatus:
    PENDING = "pending"                    # cree, pas encore traite
    PENDING_VERIFICATION = "pending_verification"  # vente declaree, preuve a verifier
    CONFIRMED = "confirmed"
    PAID = "paid"
    PREPARING = "preparing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class PaymentStatus:
    UNPAID = "unpaid"
    PENDING = "pending"                    # intention de paiement creee
    PENDING_VERIFICATION = "pending_verification"  # preuve manuelle soumise
    PAID = "paid"                          # UNIQUEMENT apres verification serveur
    FAILED = "failed"
    REFUNDED = "refunded"


class CommissionStatus:
    PENDING = "pending"      # vente reconnue, echeance non atteinte
    DUE = "due"              # exigible
    PAID = "paid"            # reglee par le vendeur / compensee
    REVERSED = "reversed"    # annulee par remboursement/litige (ecriture compensatoire)
    CANCELLED = "cancelled"  # vente annulee avant validation


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.CUSTOMER, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (CheckConstraint("role IN ('admin','vendor','customer')", name="ck_user_role"),)


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(30), default="verify_phone")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 du jeton
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Shop(Base):
    __tablename__ = "shops"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    logo_url: Mapped[str] = mapped_column(String(500), default="")
    banner_url: Mapped[str] = mapped_column(String(500), default="")
    theme_color: Mapped[str] = mapped_column(String(9), default="#0f766e")
    whatsapp_e164: Mapped[str] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(100), default="Dakar")
    country: Mapped[str] = mapped_column(String(2), default="SN")
    status: Mapped[str] = mapped_column(String(20), default=ShopStatus.PENDING, index=True)
    commission_bps_override: Mapped[int | None] = mapped_column(Integer)  # admin seulement
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)


class PaymentMethod(Base):
    """Moyen de paiement declare par un vendeur (aucun secret de compte ici)."""
    __tablename__ = "payment_methods"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))       # wave | orange_money | free_money | bank | cash
    label: Mapped[str] = mapped_column(String(120))
    phone_e164: Mapped[str] = mapped_column(String(20), default="")
    account_ref: Mapped[str] = mapped_column(String(150), default="")  # code marchand / IBAN / wallet id
    routing: Mapped[str] = mapped_column(String(20), default="merchant_direct")  # platform | merchant_direct
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)   # valide par un admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), index=True)
    slug: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price_xof: Mapped[int] = mapped_column(Integer)                 # prix unitaire, whole XOF
    compare_at_price_xof: Mapped[int | None] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer, default=1)
    delivery_fee_xof: Mapped[int] = mapped_column(Integer, default=0)
    sku: Mapped[str] = mapped_column(String(80), default="")
    video_url: Mapped[str] = mapped_column(String(500), default="")
    attributes_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default=ProductStatus.ACTIVE, index=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("shop_id", "slug", name="uq_product_shop_slug"),
        CheckConstraint("price_xof >= 0 AND stock >= 0 AND delivery_fee_xof >= 0", name="ck_product_amounts"),
        Index("ix_products_shop_status", "shop_id", "status"),
        Index("ix_products_price", "price_xof"),
    )


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    alt: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    price_delta_xof: Mapped[int] = mapped_column(Integer, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    sku: Mapped[str] = mapped_column(String(80), default="")


class Favorite(Base):
    __tablename__ = "favorites"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Inquiry(Base):
    """Clic WhatsApp trace. Une conversation privee n'est JAMAIS lisible : on ne trace que le clic."""
    __tablename__ = "inquiries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="product")
    claim_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    session_hash: Mapped[str] = mapped_column(String(64), default="")
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(60), default="")
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="RESTRICT"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    customer_name: Mapped[str] = mapped_column(String(150))
    customer_phone_e164: Mapped[str] = mapped_column(String(20), index=True)
    customer_email: Mapped[str | None] = mapped_column(String(255))
    delivery_method: Mapped[str] = mapped_column(String(20), default="pickup")
    delivery_address: Mapped[str] = mapped_column(Text, default="")
    delivery_fee_xof: Mapped[int] = mapped_column(Integer, default=0)
    subtotal_xof: Mapped[int] = mapped_column(Integer)
    discount_xof: Mapped[int] = mapped_column(Integer, default=0)
    total_xof: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default=OrderStatus.PENDING, index=True)
    payment_status: Mapped[str] = mapped_column(String(30), default=PaymentStatus.UNPAID, index=True)
    origin: Mapped[str] = mapped_column(String(30), default="checkout")  # checkout | vendor_declaration | whatsapp
    claim_code: Mapped[str] = mapped_column(String(16), default="", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_signals_json: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)  # verrou optimiste
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (CheckConstraint("total_xof >= 0 AND subtotal_xof >= 0", name="ck_order_amounts"),)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    title_snapshot: Mapped[str] = mapped_column(String(200))
    unit_price_xof: Mapped[int] = mapped_column(Integer)  # prix serveur au moment de la commande
    quantity: Mapped[int] = mapped_column(Integer)
    line_total_xof: Mapped[int] = mapped_column(Integer)
    variant_info: Mapped[str] = mapped_column(String(200), default="")

    __table_args__ = (CheckConstraint("quantity > 0 AND unit_price_xof >= 0", name="ck_item_amounts"),)


class Payment(Base):
    """Trace de paiement. Un ordre n'est 'paid' que si une ligne verifiee existe."""
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(30))        # manual | wave | orange_money
    provider_reference: Mapped[str] = mapped_column(String(200), default="")
    amount_xof: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="XOF")
    status: Mapped[str] = mapped_column(String(30), default=PaymentStatus.PENDING)
    verification: Mapped[str] = mapped_column(String(40), default="")  # webhook:wave | admin:proof | ...
    verified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    evidence_url: Mapped[str] = mapped_column(String(500), default="")
    raw_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("provider", "provider_reference", name="uq_payment_provider_ref"),
        Index("ix_payment_order_status", "order_id", "status"),
    )


class PaymentProof(Base):
    __tablename__ = "payment_proofs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    submitted_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(30), default="manual")
    provider_reference: Mapped[str] = mapped_column(String(200), default="")
    amount_xof: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    evidence_url: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WebhookEvent(Base):
    """Anti-rejeu : un identifiant d'evenement fournisseur n'est traite qu'une seule fois."""
    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    provider: Mapped[str] = mapped_column(String(30))
    event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(100), default="")
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="received")
    error: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_event"),)


class Commission(Base):
    """UNE commission par vente, garantie par la contrainte d'unicite sur order_id."""
    __tablename__ = "commissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), unique=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="RESTRICT"), index=True)
    gross_xof: Mapped[int] = mapped_column(Integer)         # montant paye par le client
    rate_bps: Mapped[int] = mapped_column(Integer)          # 1000 = 10 %, fige a la vente
    platform_fee_xof: Mapped[int] = mapped_column(Integer)
    payment_fee_xof: Mapped[int] = mapped_column(Integer)
    net_vendor_xof: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=CommissionStatus.PENDING, index=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (CheckConstraint("platform_fee_xof >= 0 AND rate_bps BETWEEN 0 AND 10000", name="ck_commission"),)


class LedgerEntry(Base):
    """Journal financier en ecriture seule, chaine de hachage (avant/apres).

    amount_xof est SIGNE : positif = mouvement entrant, negatif = ecriture compensatoire.
    account identifie le compte logique impacte (gmv, platform_revenue, vendor_payable,
    commission_receivable, cash, payment_expense, delivery_payable).
    debit_xof / credit_xof sont la projection comptable de amount_xof (>0 dans une seule colonne).
    """
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_type: Mapped[str] = mapped_column(String(40), index=True)  # sale | commission | vendor_share | payment_fee | delivery_fee | settlement | refund | reversal | adjustment
    account: Mapped[str] = mapped_column(String(40), default="gmv", index=True)
    amount_xof: Mapped[int] = mapped_column(Integer, default=0)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    shop_id: Mapped[str | None] = mapped_column(ForeignKey("shops.id", ondelete="RESTRICT"), index=True)
    debit_xof: Mapped[int] = mapped_column(Integer, default=0)
    credit_xof: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(String(400), default="")
    actor: Mapped[str] = mapped_column(String(80), default="system")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class CommissionSettlement(Base):
    """Encaissement effectif de la commission due par un vendeur."""
    __tablename__ = "commission_settlements"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="RESTRICT"), index=True)
    amount_xof: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(40), default="wave")
    reference: Mapped[str] = mapped_column(String(200), default="")
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_role: Mapped[str] = mapped_column(String(20), default="anonymous")
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(60), default="")
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    before_json: Mapped[str] = mapped_column(Text, default="")
    after_json: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(200), default="")
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("product_id", "order_id", "user_id", name="uq_review_per_purchase"),
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating"),
    )


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    shop_id: Mapped[str] = mapped_column(ForeignKey("shops.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="percent")  # percent | fixed
    value: Mapped[int] = mapped_column(Integer)
    max_uses: Mapped[int] = mapped_column(Integer, default=100)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    min_order_xof: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("shop_id", "code", name="uq_coupon_shop_code"),)


class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), index=True)
    opened_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
