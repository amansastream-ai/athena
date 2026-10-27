"""WhatsApp : liens traces, messages pre-remplis, limites de la plateforme."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.db import SessionLocal
from app.models import Inquiry, Product
from app.services import whatsapp


def test_product_whatsapp_redirect_is_tracked(client, data):
    token = whatsapp.product_token(data.p1000)
    r = client.get(f"/wa/{token}?source=test", follow_redirects=False)
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    assert url.netloc == "wa.me"
    assert url.path == "/221770000002"
    text = parse_qs(url.query)["text"][0]
    assert data.p1000.title in text
    assert "affiche a 1 000 F CFA" in text
    assert "Reference :" in text
    assert "Lien :" in text
    assert "Est-il disponible" in text
    with SessionLocal() as s:
        inq = s.query(Inquiry).one()
        assert inq.shop_id == data.s1.id
        assert inq.product_id == data.p1000.id
        assert inq.claim_code in text
        assert inq.ip_hash  # IP hachee, jamais en clair


def test_shop_whatsapp_redirect_is_tracked(client, data):
    token = whatsapp.shop_token(data.s1)
    r = client.get(f"/wa/{token}", follow_redirects=False)
    assert r.status_code == 302
    text = parse_qs(urlparse(r.headers["location"]).query)["text"][0]
    assert data.s1.name in text and "Boutique :" in text
    with SessionLocal() as s:
        assert s.query(Inquiry).count() == 1


def test_invalid_or_tampered_token_never_redirects_to_attacker(client):
    r = client.get("/wa/token.bidule", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/?wa=")
    assert "wa.me" not in r.headers["location"]


def test_whatsapp_url_rejects_invalid_number():
    with pytest.raises(ValueError):
        whatsapp.whatsapp_url("123", "bonjour")


def test_cloud_api_is_disabled_without_meta_credentials():
    cloud = whatsapp.CloudApiProvider()
    assert cloud.enabled is False
    with pytest.raises(RuntimeError):
        cloud.send_template(to_e164="+221770000001", template_name="order_update")


def test_cloud_api_signature_verification_requires_secret():
    cloud = whatsapp.CloudApiProvider()
    assert cloud.verify_signature(b'{"a":1}', "sha256=deadbeef") is False


def test_cloud_api_parse_inbound_messages():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "221771234567", "id": "wamid.1", "type": "text",
         "text": {"body": "Bonjour"}, "timestamp": "123"}]}}]}]}
    msgs = whatsapp.CloudApiProvider.parse_inbound(payload)
    assert msgs == [{"from": "221771234567", "id": "wamid.1", "type": "text",
                     "text": "Bonjour", "timestamp": "123"}]
