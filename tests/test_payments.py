"""Paiements : webhooks signes, anti-rejeu, re-verification distante, parcours manuel."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import Commission, Payment, PaymentStatus, WebhookEvent
from app.services import payments
from tests.conftest import make_order


class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, *, remote=None, post_response=None):
        self.remote = remote or {}
        self.post_response = post_response or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResp(self.remote)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResp(self.post_response)


@pytest.fixture
def wave_config():
    keys = ("wave_mode", "wave_api_key", "wave_webhook_secret", "wave_signing_secret", "wave_base_url")
    originals = {k: getattr(settings, k) for k in keys}
    object.__setattr__(settings, "wave_mode", "sandbox")
    object.__setattr__(settings, "wave_api_key", "wave_sn_test_key")
    object.__setattr__(settings, "wave_webhook_secret", "whsec_test")
    object.__setattr__(settings, "wave_signing_secret", "sign_test")
    object.__setattr__(settings, "wave_base_url", "https://api.wave.com")
    yield
    for k, v in originals.items():
        object.__setattr__(settings, k, v)


def wave_event(order, *, amount=None, event_id="EV_001", status="succeeded", checkout="complete", ref=None):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "id": "cos_TEST123", "amount": str(amount if amount is not None else order.total_xof),
            "currency": "XOF", "client_reference": ref or order.reference,
            "payment_status": status, "checkout_status": checkout,
            "when_completed": "2026-10-25T00:00:00Z",
        },
    }


def signed(raw: bytes, *, ts=None, secret=b"whsec_test") -> dict:
    stamp = ts or str(int(time.time()))
    sig = hmac.new(secret, stamp.encode() + raw, hashlib.sha256).hexdigest()
    return {"wave-signature": f"t={stamp},v1={sig}"}


def test_wave_webhook_signature_invalid_and_expired(wave_config, data):
    p = payments.WaveCheckoutProvider(client=FakeClient())
    raw = b'{"id":"EV_X","type":"test"}'
    with pytest.raises(payments.PaymentError):
        p.verify_webhook(raw, {"wave-signature": "t=1,v1=deadbeef"})
    old = str(int(time.time()) - 600)
    with pytest.raises(payments.PaymentError):
        p.verify_webhook(raw, signed(raw, ts=old))
    assert p.verify_webhook(raw, signed(raw))["id"] == "EV_X"


def test_wave_webhook_confirms_payment_and_commission(wave_config, data):
    order = make_order(data, product=data.p1000)
    event = wave_event(order)
    raw = json.dumps(event, separators=(",", ":")).encode()
    remote = dict(event["data"])
    p = payments.WaveCheckoutProvider(client=FakeClient(remote=remote))
    verified = p.verify_webhook(raw, signed(raw))
    assert verified["id"] == "EV_001"
    assert p.handle_event(data.db, verified) == "processed"
    assert order.payment_status == PaymentStatus.PAID
    c = data.db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one()
    assert c.platform_fee_xof == 100
    assert data.db.execute(select(func.count(Payment.id))).scalar_one() == 1
    assert data.db.execute(select(WebhookEvent).where(WebhookEvent.event_id == "EV_001")).scalar_one().status == "processed"


def test_wave_webhook_replay_does_not_double_commission(wave_config, data):
    order = make_order(data, product=data.p10000)
    event = wave_event(order)
    remote = dict(event["data"])
    p = payments.WaveCheckoutProvider(client=FakeClient(remote=remote))
    assert p.handle_event(data.db, event) == "processed"
    assert p.handle_event(data.db, event) == "duplicate"
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 1
    assert data.db.execute(select(func.count(WebhookEvent.id))).scalar_one() == 1


def test_wave_amount_mismatch_is_rejected(wave_config, data):
    order = make_order(data, product=data.p1000)
    p = payments.WaveCheckoutProvider(client=FakeClient(remote={}))
    assert p.handle_event(data.db, wave_event(order, amount=999)) == "amount_mismatch"
    assert order.payment_status != PaymentStatus.PAID
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0


def test_wave_remote_recheck_failure_is_rejected(wave_config, data):
    order = make_order(data, product=data.p1000)
    remote = dict(wave_event(order)["data"])
    remote["payment_status"] = "processing"
    p = payments.WaveCheckoutProvider(client=FakeClient(remote=remote))
    assert p.handle_event(data.db, wave_event(order)) == "remote_mismatch"
    assert order.payment_status != PaymentStatus.PAID
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0


def test_wave_unknown_order_is_rejected(wave_config, data):
    order = make_order(data)
    event = wave_event(order, ref="JY-INCONNU")
    p = payments.WaveCheckoutProvider(client=FakeClient(remote={}))
    assert p.handle_event(data.db, event) == "unknown_order"
    assert order.payment_status != PaymentStatus.PAID


def test_wave_checkout_session_creation_with_fake_transport(wave_config, data):
    from app.services.registry import set_setting
    set_setting(data.db, "wave_checkout_enabled", True, actor_id=data.admin.id)
    data.db.commit()
    order = make_order(data)
    client = FakeClient(post_response={"id": "cos_FAKE", "wave_launch_url": "https://pay.wave.com/c/cos_FAKE"})
    result = payments.WaveCheckoutProvider(client=client).create_session(
        data.db, order, public_base_url="https://jaay.test")
    assert result["launch_url"].startswith("https://pay.wave.com/")
    method, url, kwargs = client.calls[0]
    assert method == "POST" and url.endswith("/v1/checkout/sessions")
    assert "Wave-Signature" in kwargs["headers"]
    assert json.loads(kwargs["content"])["client_reference"] == order.reference
    # La creation d'une session ne confirme RIEN.
    assert order.payment_status != PaymentStatus.PAID
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0


def test_manual_instructions_never_claim_dynamic_link(data):
    order = make_order(data)
    info = payments.manual_instructions(data.db, order, data.s1)
    assert info["amount_xof"] == order.total_xof
    assert info["reference"] == order.reference
    assert "verification" in info["warning"].lower()
    caps = {c["code"]: c for c in payments.provider_capabilities(data.db)}
    assert caps["manual"]["state"] == "operational"
    assert caps["wave"]["dynamic_link"] is False
    assert caps["orange_money"]["state"] == "not_configured"


def test_orange_money_is_fail_closed_without_status_url(data, monkeypatch):
    keys = ("om_mode", "om_client_id", "om_client_secret", "om_webpayment_url",
            "om_webhook_secret", "om_status_url", "om_merchant_key", "om_token_url")
    originals = {k: getattr(settings, k) for k in keys}
    object.__setattr__(settings, "om_mode", "sandbox")
    object.__setattr__(settings, "om_client_id", "id")
    object.__setattr__(settings, "om_client_secret", "secret")
    object.__setattr__(settings, "om_webpayment_url", "https://om.test/webpayment")
    object.__setattr__(settings, "om_webhook_secret", "omsecret")
    object.__setattr__(settings, "om_status_url", "")  # verification distante impossible
    object.__setattr__(settings, "om_merchant_key", "mk")
    object.__setattr__(settings, "om_token_url", "https://om.test/token")
    try:
        order = make_order(data)
        raw = json.dumps({"order_id": order.reference, "status": "SUCCESS",
                          "txnid": "OM-1", "pay_token": "pt-1", "amount": order.total_xof}).encode()
        sig = "sha256=" + hmac.new(b"omsecret", raw, hashlib.sha256).hexdigest()
        p = payments.OrangeMoneyWebPayProvider(client=FakeClient())
        assert p.verify_webhook(raw, {"x-om-signature": sig})["txnid"] == "OM-1"
        with pytest.raises(payments.PaymentError):
            p.handle_event(data.db, {"order_id": order.reference, "status": "SUCCESS",
                                    "txnid": "OM-1", "pay_token": "pt-1"})
        assert order.payment_status != PaymentStatus.PAID
        assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0
    finally:
        for k, v in originals.items():
            object.__setattr__(settings, k, v)
