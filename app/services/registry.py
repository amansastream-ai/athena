"""Parametres plateforme modifies par l'administrateur (jamais par un vendeur)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..models import PlatformSetting, utcnow

DEFAULT_SETTINGS: dict[str, Any] = {
    "commission_bps": 1000,                       # 10 % par defaut
    "payment_fee_bps": 0,                         # frais PSP repercutables, 0 par defaut
    "commission_trigger": "payment_confirmed",   # payment_confirmed | delivered
    "manual_payment_enabled": True,
    "wave_checkout_enabled": False,
    "om_webpay_enabled": False,
    "platform_collection_number": "",            # compte plateforme pour le paiement manuel
    "platform_collection_label": "Compte plateforme Jaay",
    "min_declaration_amount_xof": 500,
    "fraud_review_threshold": 50,
    "currency": "XOF",
    "country": "SN",
}

NUMERIC_KEYS = {"commission_bps", "payment_fee_bps", "min_declaration_amount_xof", "fraud_review_threshold"}
BOOL_KEYS = {"manual_payment_enabled", "wave_checkout_enabled", "om_webpay_enabled"}


def get_setting(db: OrmSession, key: str, default: Any = None) -> Any:
    row = db.get(PlatformSetting, key)
    if not row:
        return DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row.value_json)
    except json.JSONDecodeError:
        return row.value_json


def all_settings(db: OrmSession) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for row in db.execute(select(PlatformSetting)).scalars():
        try:
            out[row.key] = json.loads(row.value_json)
        except json.JSONDecodeError:
            out[row.key] = row.value_json
    return out


def set_setting(db: OrmSession, key: str, value: Any, actor_id: str | None = None, label: str = "") -> PlatformSetting:
    if key.startswith("_"):
        raise ValueError("Cle reservee.")
    row = db.get(PlatformSetting, key) or PlatformSetting(key=key, value_json="null")
    row.value_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    row.updated_by = actor_id
    row.updated_at = utcnow()
    if label:
        row.label = label
    db.add(row)
    db.flush()
    return row


def parse_setting_value(key: str, raw: str):
    """Convertit une valeur saisie dans l'interface admin."""
    import json as _json
    try:
        value = _json.loads(raw)
    except _json.JSONDecodeError:
        value = raw
    if key in NUMERIC_KEYS:
        value = int(value)
    elif key in BOOL_KEYS:
        value = str(value).lower() in {"1", "true", "yes", "on"}
    return value
