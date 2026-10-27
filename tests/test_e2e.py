"""Parcours complet : inscription -> boutique -> produit -> WhatsApp -> commande -> paiement verifie."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Commission, Inquiry, Notification, Order, PaymentProof, PaymentStatus, Product, Shop
from tests.conftest import form_csrf, login, page_csrf


def test_full_seller_and_buyer_journey(client, data):
    # 1. Inscription d'un nouveau vendeur
    client.get("/inscription")
    r = client.post("/inscription", data={
        "nom": "Nouveau Vendeur", "telephone": "78 111 22 33",
        "email": "nouveau@test.sn", "mot_de_passe": "NouveauTest2026!",
        "nom_boutique": "Boutique E2E", "slug_boutique": "boutique-e2e",
        "whatsapp": "78 111 22 33", "csrf_token": client.cookies.get("jaay_csrf", ""),
    }, follow_redirects=False)
    assert r.status_code == 303 and "/verifier-telephone" in r.headers["location"]
    otp = client.cookies.get("jaay_dev_otp")
    assert otp and len(otp) == 6

    # 2. Verification du telephone -> session -> boutique active
    r = client.post("/verifier-telephone", data={
        "code": otp, "csrf_token": client.cookies.get("jaay_csrf", ""),
    }, follow_redirects=False)
    assert r.status_code == 303 and "/vendeur/boutique" in r.headers["location"]
    assert client.get("/vendeur/boutique").status_code == 200

    with SessionLocal() as s:
        shop = s.execute(select(Shop).where(Shop.slug == "boutique-e2e")).scalar_one()
        assert shop.status == "active"
        assert shop.whatsapp_e164 == "+221781112233"

    # 3. Publication d'un produit
    csrf = form_csrf(client, "/vendeur/produits")
    r = client.post("/vendeur/produits", data={
        "titre": "Sandales artisanales", "prix": "5000", "stock": "4",
        "description": "Cuir naturel, fait main.", "categorie": data.cat.id,
        "livraison": "0", "csrf_token": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        product = s.execute(select(Product).where(Product.title == "Sandales artisanales")).scalar_one()
        assert product.price_xof == 5000 and product.shop_id == shop.id

    # 4. Pages publiques
    assert client.get("/boutique/boutique-e2e").status_code == 200
    detail = client.get(f"/boutique/boutique-e2e/produit/{product.slug}")
    assert detail.status_code == 200
    assert "Renseignements sur WhatsApp" in detail.text

    # 5. Clic WhatsApp : suivi + message pre-rempli
    match = re.search(r'href="/wa/([^"]+)"', detail.text)
    assert match, "lien WhatsApp trace absent"
    wa = client.get(f"/wa/{match.group(1)}", follow_redirects=False)
    assert wa.status_code == 302 and "wa.me/221781112233" in wa.headers["location"]
    with SessionLocal() as s:
        inq = s.execute(select(Inquiry).where(Inquiry.shop_id == shop.id)).scalar_one()
        assert inq.claim_code.startswith("JY-")

    # 6. Commande via l'API (reference unique)
    token = page_csrf(client, "/")
    r = client.post("/api/orders", json={
        "items": [{"product_id": product.id, "quantity": 1}],
        "customer_name": "Awa", "customer_phone": "77 222 33 44",
        "delivery_method": "pickup", "csrf_token": token,
    }, headers={"X-CSRF-Token": token})
    assert r.status_code == 201, r.text[:400]
    ref = r.json()["reference"]
    assert ref.startswith("JY-") and r.json()["total_xof"] == 5000
    order_page = client.get(f"/commandes/{ref}")
    assert order_page.status_code == 200 and "Paiement non confirm" in order_page.text
    with SessionLocal() as s:
        order = s.execute(select(Order).where(Order.reference == ref)).scalar_one()
        assert order.payment_status == PaymentStatus.UNPAID
        assert s.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none() is None

    # 7. Preuve de paiement soumise par le client : toujours non confirme
    csrf = form_csrf(client, f"/commandes/{ref}")
    r = client.post(f"/commandes/{ref}/preuve", data={
        "fournisseur": "wave", "reference_paiement": "E2E-WAVE-1",
        "montant": "5000", "note": "Virement test", "csrf_token": csrf,
    }, follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        order = s.execute(select(Order).where(Order.reference == ref)).scalar_one()
        assert order.payment_status == PaymentStatus.PENDING_VERIFICATION
        assert s.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one_or_none() is None
        proof = s.execute(select(PaymentProof).where(PaymentProof.order_id == order.id)).scalar_one()

    # 8. Validation par l'administrateur -> paiement confirme + commission 500 F
    admin_client = TestClient(app)
    admin_csrf = login(admin_client, "admin@test.sn", "AdminTest2026!")
    assert admin_client.get("/admin").status_code == 200
    r = admin_client.post(f"/admin/preuves/{proof.id}", data={
        "decision": "accept", "note": "Verifie aupres de l'operateur", "csrf_token": admin_csrf,
    }, follow_redirects=False)
    assert r.status_code == 303

    with SessionLocal() as s:
        order = s.execute(select(Order).where(Order.reference == ref)).scalar_one()
        commission = s.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one()
        assert order.payment_status == PaymentStatus.PAID
        assert order.status == "paid"
        assert commission.platform_fee_xof == 500
        assert commission.net_vendor_xof == 4500
        assert commission.rate_bps == 1000
        notif = s.execute(select(Notification).where(Notification.kind == "payment_confirmed")).first()
        assert notif is not None

    final_page = client.get(f"/commandes/{ref}")
    assert final_page.status_code == 200
    assert "Paiement v" in final_page.text  # "Paiement vérifié"
    assert "500 F" in final_page.text
