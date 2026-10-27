"""Schemas Pydantic de validation des entrees (API JSON)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .security import is_valid_email, normalize_phone


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    phone: str
    email: str | None = None
    password: str = Field(min_length=10, max_length=200)
    role: Literal["vendor", "customer"] = "vendor"

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        n = normalize_phone(v)
        if not n:
            raise ValueError("Numero de telephone senegalais invalide (ex : 77 123 45 67).")
        return n

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v and not is_valid_email(v):
            raise ValueError("Adresse e-mail invalide.")
        return v.lower().strip() if v else None


class LoginIn(BaseModel):
    identifier: str  # telephone ou email
    password: str


class OtpVerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class OtpVerifyApiIn(BaseModel):
    phone: str
    code: str = Field(min_length=6, max_length=6)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        n = normalize_phone(v)
        if not n:
            raise ValueError("Numero invalide.")
        return n


class ShopIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=4000)
    whatsapp: str | None = None
    city: str = "Dakar"
    theme_color: str = "#0f766e"


class ProductIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=8000)
    price_xof: int = Field(ge=50, le=100_000_000)
    stock: int = Field(default=1, ge=0, le=10_000_000)
    delivery_fee_xof: int = Field(default=0, ge=0, le=1_000_000)
    category_id: str | None = None
    compare_at_price_xof: int | None = Field(default=None, ge=0)
    sku: str = ""
    video_url: str = ""


class OrderItemIn(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=999)


class OrderIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1, max_length=30)
    customer_name: str = Field(min_length=2, max_length=150)
    customer_phone: str
    customer_email: str | None = None
    delivery_method: Literal["pickup", "delivery"] = "pickup"
    delivery_address: str = ""
    claim_code: str = ""
    coupon_code: str = ""
    idempotency_key: str | None = None
    csrf_token: str | None = None

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        n = normalize_phone(v)
        if not n:
            raise ValueError("Numero de telephone invalide.")
        return n


class DeclarationIn(BaseModel):
    """Vente conclue hors plateforme (WhatsApp). Ne confirme JAMAIS un paiement."""
    product_id: str
    quantity: int = Field(default=1, ge=1, le=999)
    sale_amount_xof: int = Field(ge=50, le=100_000_000)
    customer_name: str = Field(min_length=2, max_length=150)
    customer_phone: str
    claim_code: str = ""
    payment_provider: str = "wave"
    payment_reference: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)

    @field_validator("customer_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        n = normalize_phone(v)
        if not n:
            raise ValueError("Numero de telephone invalide.")
        return n


class ProofIn(BaseModel):
    provider: str = "wave"
    provider_reference: str = Field(min_length=3, max_length=200)
    amount_xof: int = Field(ge=1)
    note: str = ""


class SettingIn(BaseModel):
    key: str
    value: str  # JSON (ex : 1000, "texte", {"a":1})


class CommissionSettlementIn(BaseModel):
    amount_xof: int = Field(ge=1)
    method: str = "wave"
    reference: str = ""
    note: str = ""


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class AiAssistIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    category: str = ""
    keywords: str = ""
    language: Literal["fr", "en", "wo"] = "fr"


class DisputeIn(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)


# Les champs supplementaires sont ignores par defaut : aucun prix, taux ou statut
# fourni par le client n est jamais utilise (recalcul serveur dans services/orders.py).
