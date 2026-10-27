"""Commandes, declarations WhatsApp, transitions et isolation multi-vendeurs."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import (
    Commission, Inquiry, LedgerEntry, Notification, Order, OrderStatus, PaymentProof,
    PaymentStatus, Product, uid, utcnow,
)
from app.services import orders as order_service, payments
from tests.conftest import make_order, pay_order


def test_order_has_unique_reference_and_server_side_price(data):
    o1 = make_order(data, product=data.p10000)
    o2 = make_order(data, product=data.p10000)
    assert o1.reference != o2.reference
    assert o1.reference.startswith("JY-")
    assert o1.total_xof == 10000
    from app.models import OrderItem
    item = data.db.execute(select(OrderItem).where(OrderItem.order_id == o1.id)).scalar_one()
    assert item.unit_price_xof == 10000  # prix relu en base, jamais envoye par le client


def test_idempotency_key_prevents_duplicate_order_and_commission(data):
    a = make_order(data, key="IDEM-1")
    b = make_order(data, key="IDEM-1")
    assert a.id == b.id
    assert data.db.execute(select(func.count(Order.id))).scalar_one() == 1
    pay_order(data, a)
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 1


def test_invalid_transition_is_refused(data):
    o = make_order(data)
    with pytest.raises(order_service.OrderError):
        order_service.transition(data.db, o, OrderStatus.SHIPPED, actor=data.admin)
    with pytest.raises(order_service.OrderError):
        order_service.transition(data.db, o, OrderStatus.PAID, actor=data.admin)  # sans paiement verifie


def test_valid_transition_path_is_logged(data):
    o = make_order(data)
    pay_order(data, o)
    for status in [OrderStatus.PREPARING, OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
        order_service.transition(data.db, o, status, actor=data.admin)
    assert o.status == OrderStatus.DELIVERED
    assert o.delivered_at is not None
    from app.models import AuditLog
    logs = list(data.db.execute(select(AuditLog).where(AuditLog.action == "order.status_changed")).scalars())
    assert len(logs) == 3


def test_declaration_never_confirms_payment_and_notifies_admin(data):
    o = order_service.declare_sale(
        data.db, shop=data.s1, vendor=data.v1, product_id=data.p10000.id, quantity=1,
        sale_amount_xof=10000, customer_name="Acheteur", customer_phone="+221771234567",
        payment_provider="wave", payment_reference="OM-DECLARE-1",
    )
    assert o.reference.startswith("JY-")
    assert o.payment_status == PaymentStatus.PENDING_VERIFICATION
    assert o.status == OrderStatus.PENDING_VERIFICATION
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0
    assert data.db.execute(select(func.count(LedgerEntry.id))).scalar_one() == 0
    proof = data.db.execute(select(PaymentProof).where(PaymentProof.order_id == o.id)).scalar_one()
    assert proof.status == "pending"
    notif = data.db.execute(select(Notification).where(Notification.kind == "sale_declared")).scalar_one_or_none()
    assert notif is not None
    assert "Commission estimee" in notif.body and "1000" in notif.body


def test_vendor_cannot_confirm_his_own_payment(data):
    o = make_order(data)
    proof = payments.submit_proof(data.db, o, user=data.v1, provider="wave",
                                  provider_reference="W-X", amount_xof=o.total_xof)
    with pytest.raises(payments.PaymentError):
        payments.review_proof(data.db, proof, admin=data.v1, approve=True)
    assert o.payment_status == PaymentStatus.PENDING_VERIFICATION
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0


def test_duplicate_payment_reference_is_refused(data):
    o1 = make_order(data, key="D1")
    pay_order(data, o1, ref="W-SAME")
    o2 = make_order(data, key="D2")
    with pytest.raises(payments.PaymentError):
        payments.submit_proof(data.db, o2, user=data.customer, provider="wave",
                              provider_reference="W-SAME", amount_xof=o2.total_xof)


def test_self_dealing_is_flagged(data):
    o = order_service.declare_sale(
        data.db, shop=data.s1, vendor=data.v1, product_id=data.p1000.id, quantity=1,
        sale_amount_xof=1000, customer_name=data.v1.full_name,
        customer_phone=data.v1.phone_e164, claim_code="",
    )
    assert o.risk_score >= 40
    assert "auto-vente" in o.risk_signals_json


def test_claim_code_links_inquiry_to_declared_order(data):
    inq = Inquiry(id=uid(), shop_id=data.s1.id, product_id=data.p1000.id,
                  claim_code="JY-ABCDE", created_at=utcnow())
    data.db.add(inq)
    data.db.commit()
    o = order_service.declare_sale(
        data.db, shop=data.s1, vendor=data.v1, product_id=data.p1000.id, quantity=1,
        sale_amount_xof=1000, customer_name="Client", customer_phone="+221771234567",
        claim_code="JY-ABCDE",
    )
    data.db.refresh(inq)
    assert inq.order_id == o.id


def test_stock_decremented_and_restored(data):
    before = data.p1000.stock
    o = make_order(data, product=data.p1000, qty=2)
    data.db.refresh(data.p1000)
    assert data.p1000.stock == before - 2
    order_service.transition(data.db, o, OrderStatus.CANCELLED, actor=data.admin)
    data.db.refresh(data.p1000)
    assert data.p1000.stock == before


def test_vendor_cannot_read_another_vendors_order(data):
    o = make_order(data)
    with pytest.raises(order_service.OrderError):
        order_service.order_for_vendor(data.db, o.id, data.s2.id)


def test_commission_trigger_delivered(data):
    from app.services.registry import set_setting
    set_setting(data.db, "commission_trigger", "delivered", actor_id=data.admin.id)
    data.db.commit()
    o = make_order(data)
    pay_order(data, o)
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0
    order_service.transition(data.db, o, OrderStatus.PREPARING, actor=data.admin)
    order_service.transition(data.db, o, OrderStatus.SHIPPED, actor=data.admin)
    order_service.transition(data.db, o, OrderStatus.DELIVERED, actor=data.admin)
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 1
