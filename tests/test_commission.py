"""Moteur de commissions : calcul, unicite, journal, statistiques, reversements."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Commission, CommissionStatus, LedgerEntry, OrderStatus, PaymentStatus
from app.services import ledger, orders as order_service
from app.services.registry import set_setting
from tests.conftest import make_order, pay_order


@pytest.mark.parametrize("gross,expected", [
    (1000, 100), (5000, 500), (10000, 1000), (50000, 5000), (100000, 10000),
    (999, 100), (1, 0), (1500, 150), (0, 0),
])
def test_commission_10_percent_examples(gross, expected):
    c = ledger.compute_commission(gross, 1000)
    assert c["platform_fee_xof"] == expected
    assert c["net_vendor_xof"] == gross - expected


def test_10000_gives_9000_to_vendor():
    c = ledger.compute_commission(10000, 1000)
    assert (c["platform_fee_xof"], c["net_vendor_xof"], c["payment_fee_xof"]) == (1000, 9000, 0)


def test_rate_is_configurable_and_snapshotted(data):
    set_setting(data.db, "commission_bps", 1250, actor_id=data.admin.id)
    data.db.commit()
    order = make_order(data, product=data.p10000)
    pay_order(data, order)
    c = data.db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one()
    assert c.rate_bps == 1250
    assert c.platform_fee_xof == 1250
    set_setting(data.db, "commission_bps", 2000, actor_id=data.admin.id)
    data.db.commit()
    assert c.platform_fee_xof == 1250  # fige, jamais recalcule


def test_payment_fee_separate_from_commission(data):
    set_setting(data.db, "payment_fee_bps", 100, actor_id=data.admin.id)
    data.db.commit()
    order = make_order(data, product=data.p10000)
    pay_order(data, order)
    c = data.db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one()
    assert c.platform_fee_xof == 1000
    assert c.payment_fee_xof == 100
    assert c.net_vendor_xof == 8900


def test_sale_not_paid_creates_no_commission(data):
    order = make_order(data)
    assert order.payment_status != PaymentStatus.PAID
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0
    assert data.db.execute(select(func.count(LedgerEntry.id))).scalar_one() == 0


def test_exactly_one_commission_per_order_even_on_replay(data):
    order = make_order(data)
    pay_order(data, order)
    from app.services import payments
    # Rejeu du meme webhook / du meme appel : aucune seconde commission.
    p2 = payments.mark_paid(data.db, order, provider="wave", provider_reference="WAVE-TEST-001",
                            amount_xof=order.total_xof, verification="webhook:wave")
    assert p2 is not None
    ledger.maybe_recognize_sale(data.db, order, actor="replay", verification="webhook:wave")
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 1
    assert data.db.execute(select(func.count(LedgerEntry.id)).where(
        LedgerEntry.entry_type == "sale")).scalar_one() == 1


def test_delivery_fee_is_not_commissioned(data):
    data.p10000.delivery_fee_xof = 1000
    data.db.commit()
    order = make_order(data, product=data.p10000, delivery="delivery")
    assert order.total_xof == 11000
    assert order.delivery_fee_xof == 1000
    pay_order(data, order)
    c = data.db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one()
    assert c.gross_xof == 10000
    assert c.platform_fee_xof == 1000
    assert ledger.financial_stats(data.db)["delivery_payable_xof"] == 1000


def test_refund_creates_compensating_entries_and_cancels_commission(data):
    order = make_order(data, product=data.p10000)
    pay_order(data, order)
    before = ledger.financial_stats(data.db)
    assert before["commission_generated_xof"] == 1000
    order_service.transition(data.db, order, OrderStatus.REFUNDED, actor=data.admin, note="Remboursement test")
    c = data.db.execute(select(Commission).where(Commission.order_id == order.id)).scalar_one()
    after = ledger.financial_stats(data.db)
    assert c.status == CommissionStatus.REVERSED
    assert order.payment_status == PaymentStatus.REFUNDED
    assert after["gmv_xof"] == 0
    assert after["commission_generated_xof"] == 0
    assert after["commission_outstanding_xof"] == 0
    assert after["vendor_payable_xof"] == 0
    assert any(e.amount_xof < 0 for e in data.db.execute(select(LedgerEntry)).scalars())
    ok, msg = ledger.verify_chain(data.db)
    assert ok, msg


def test_cancel_before_payment_creates_no_commission(data):
    order = make_order(data)
    order_service.transition(data.db, order, OrderStatus.CANCELLED, actor=data.admin)
    assert data.db.execute(select(func.count(Commission.id))).scalar_one() == 0
    assert data.db.execute(select(func.count(LedgerEntry.id))).scalar_one() == 0


def test_settlement_then_reversal_keeps_stats_consistent(data):
    o1 = make_order(data, product=data.p10000, key="k1")
    pay_order(data, o1, ref="W-1")
    ledger.record_settlement(data.db, data.s1, amount_xof=1000, actor=data.admin)
    data.db.commit()
    mid = ledger.financial_stats(data.db, data.s1.id)
    assert mid["commission_outstanding_xof"] == 0
    assert mid["commission_collected_xof"] == 1000
    order_service.transition(data.db, o1, OrderStatus.REFUNDED, actor=data.admin)
    end = ledger.financial_stats(data.db, data.s1.id)
    assert end["commission_collected_net_xof"] == 0
    assert end["commission_generated_xof"] == 0
    assert end["gmv_xof"] == 0
    assert ledger.verify_chain(data.db)[0]


def test_financial_stats_equal_sum_of_ledger_entries(data):
    for i, p in enumerate([data.p1000, data.p10000, data.p1000]):
        o = make_order(data, product=p, key=f"key-{i}")
        pay_order(data, o, ref=f"W-{i}")
    stats = ledger.financial_stats(data.db)
    rows = list(data.db.execute(select(LedgerEntry)).scalars())
    assert stats["gmv_xof"] == sum(e.amount_xof for e in rows if e.account == "gmv")
    assert stats["commission_generated_xof"] == sum(e.amount_xof for e in rows if e.account == "platform_revenue")
    assert stats["commission_outstanding_xof"] == sum(e.amount_xof for e in rows if e.account == "commission_receivable")
    assert stats["vendor_payable_xof"] == sum(e.amount_xof for e in rows if e.account == "vendor_payable")
    assert stats["gmv_xof"] == 12000
    assert stats["commission_generated_xof"] == 1200
    assert ledger.verify_chain(data.db)[0]
