"""Configuration pytest : base SQLite isolee, jeu de donnees minimal, helpers HTTP."""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

TEST_DB = Path(__file__).resolve().parent.parent / "var" / "test_jaay.db"
os.environ["JAAY_DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["JAAY_ENV"] = "test"
os.environ["JAAY_SECRET_KEY"] = "test-secret-key-do-not-use-in-production-0123456789"
os.environ["JAAY_DEV_SHOW_OTP"] = "1"
os.environ["JAAY_COOKIE_SECURE"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine, ensure_append_only_protection  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Category, Product, ProductStatus, Role, Shop, ShopStatus, User, uid, utcnow,
)
from app.security import hash_password, normalize_phone  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    from app.security import rate_limiter
    rate_limiter.reset()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ensure_append_only_protection()
    with SessionLocal() as db:
        yield db


@pytest.fixture
def data(clean_db):
    db = clean_db
    cat = Category(id=uid(), slug="mode", name="Mode")
    admin = User(id=uid(), email="admin@test.sn", phone_e164="+221770000001", full_name="Admin",
                 password_hash=hash_password("AdminTest2026!"), role=Role.ADMIN,
                 phone_verified=True, created_at=utcnow())
    v1 = User(id=uid(), email="v1@test.sn", phone_e164="+221770000002", full_name="Vendeur Un",
              password_hash=hash_password("VendeurTest2026!"), role=Role.VENDOR,
              phone_verified=True, created_at=utcnow())
    v2 = User(id=uid(), email="v2@test.sn", phone_e164="+221770000003", full_name="Vendeur Deux",
              password_hash=hash_password("VendeurTest2026!"), role=Role.VENDOR,
              phone_verified=True, created_at=utcnow())
    customer = User(id=uid(), email="c@test.sn", phone_e164="+221770000004", full_name="Client",
                    password_hash=hash_password("ClientTest2026!"), role=Role.CUSTOMER,
                    phone_verified=True, created_at=utcnow())
    db.add_all([cat, admin, v1, v2, customer])
    db.flush()
    s1 = Shop(id=uid(), owner_id=v1.id, slug="boutique-un", name="Boutique Un",
              whatsapp_e164="+221770000002", status=ShopStatus.ACTIVE, created_at=utcnow())
    s2 = Shop(id=uid(), owner_id=v2.id, slug="boutique-deux", name="Boutique Deux",
              whatsapp_e164="+221770000003", status=ShopStatus.ACTIVE, created_at=utcnow())
    db.add_all([s1, s2])
    db.flush()
    p1000 = Product(id=uid(), shop_id=s1.id, category_id=cat.id, slug="article-1000",
                    title="Article a 1000 F", price_xof=1000, stock=10, delivery_fee_xof=0,
                    status=ProductStatus.ACTIVE, created_at=utcnow())
    p10000 = Product(id=uid(), shop_id=s1.id, category_id=cat.id, slug="article-10000",
                     title="Article a 10000 F", price_xof=10000, stock=10, delivery_fee_xof=0,
                     status=ProductStatus.ACTIVE, created_at=utcnow())
    p_other = Product(id=uid(), shop_id=s2.id, category_id=cat.id, slug="article-autre",
                      title="Article autre boutique", price_xof=7000, stock=5,
                      delivery_fee_xof=0, status=ProductStatus.ACTIVE, created_at=utcnow())
    db.add_all([p1000, p10000, p_other])
    db.commit()
    return SimpleNamespace(db=db, admin=admin, v1=v1, v2=v2, customer=customer,
                           s1=s1, s2=s2, p1000=p1000, p10000=p10000, p_other=p_other,
                           cat=cat)


@pytest.fixture
def client():
    return TestClient(app)


def anon_csrf(client: TestClient) -> str:
    client.get("/")
    return client.cookies.get("jaay_csrf", "")


def page_csrf(client: TestClient, path: str = "/") -> str:
    html = client.get(path).text
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert m, f"jeton CSRF absent de {path}"
    return m.group(1)


def form_csrf(client: TestClient, path: str) -> str:
    html = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, f"champ CSRF absent de {path}"
    return m.group(1)


def login(client: TestClient, email: str, password: str) -> str:
    client.get("/connexion")
    r = client.post("/connexion", data={
        "identifiant": email, "mot_de_passe": password,
        "csrf_token": client.cookies.get("jaay_csrf", ""),
    }, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    return page_csrf(client, "/")


def make_order(data, *, product=None, qty=1, key=None, delivery="pickup"):
    from app.services import orders as order_service
    p = product or data.p1000
    return order_service.create_order(
        data.db, shop=data.s1, items=[{"product_id": p.id, "quantity": qty}],
        customer_name="Client Test", customer_phone="+221770000004",
        delivery_method=delivery, idempotency_key=key, actor=data.customer,
    )


def pay_order(data, order, ref="WAVE-TEST-001"):
    from app.services import payments
    proof = payments.submit_proof(
        data.db, order, user=data.customer, provider="wave", provider_reference=ref,
        amount_xof=order.total_xof,
    )
    payments.review_proof(data.db, proof, admin=data.admin, approve=True,
                          review_note="Verifie en test")
    return proof
