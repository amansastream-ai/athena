"""Webhooks entrants : Wave, Orange Money, WhatsApp Cloud API.

Securite commune : corps BRUT (pas de re-serialisation), HMAC en temps constant,
fenetre de validite, anti-rejeu par identifiant d'evenement, verification serveur
de l'etat de la transaction avant toute confirmation.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session as OrmSession

from ..config import settings
from ..db import get_db
from ..models import User, Role
from ..services import audit, notifications, payments, whatsapp
from ..security import rate_limit
from sqlalchemy import select

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _headers(request: Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


@router.post("/wave")
async def wave_webhook(request: Request, db: OrmSession = Depends(get_db)):
    rate_limit(request, "webhook_wave", limit=180)
    if not settings.wave_enabled:
        raise HTTPException(503, "Integration Wave non configuree.")
    raw = await request.body()
    provider = payments.WaveCheckoutProvider()
    try:
        event = provider.verify_webhook(raw, _headers(request))
        result = provider.handle_event(db, event)
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Traitement impossible : {type(exc).__name__}")
    return {"received": True, "result": result}


@router.post("/orange-money")
async def orange_webhook(request: Request, db: OrmSession = Depends(get_db)):
    rate_limit(request, "webhook_om", limit=180)
    if not settings.om_enabled:
        raise HTTPException(503, "Integration Orange Money non configuree.")
    raw = await request.body()
    provider = payments.OrangeMoneyWebPayProvider()
    try:
        event = provider.verify_webhook(raw, _headers(request))
        result = provider.handle_event(db, event)
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Traitement impossible : {type(exc).__name__}")
    return {"received": True, "result": result}


@router.get("/whatsapp")
async def whatsapp_verify(request: Request, db: OrmSession = Depends(get_db)):
    """Verification d'abonnement Meta (hub.mode / hub.challenge / hub.verify_token)."""
    cloud = whatsapp.CloudApiProvider()
    value = cloud.verify_subscription(
        request.query_params.get("hub.mode", ""),
        request.query_params.get("hub.verify_token", ""),
        request.query_params.get("hub.challenge", ""),
    )
    if value is None:
        raise HTTPException(403, "Verification echouee.")
    return PlainTextResponse(value)


@router.post("/whatsapp")
async def whatsapp_inbound(request: Request, db: OrmSession = Depends(get_db)):
    """Messages entrants Cloud API. Jamais utilise pour confirmer une vente."""
    rate_limit(request, "webhook_wa", limit=180)
    raw = await request.body()
    cloud = whatsapp.CloudApiProvider()
    if not cloud.enabled:
        raise HTTPException(503, "WhatsApp Cloud API non configuree.")
    sig = request.headers.get("x-hub-signature-256", "")
    if not cloud.verify_signature(raw, sig):
        raise HTTPException(401, "Signature invalide.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON invalide.")
    messages = cloud.parse_inbound(payload)
    for msg in messages:
        text = (msg.get("text") or "")[:500]
        notifications.notify_admins(
            db, kind="whatsapp_inbound", title="Message WhatsApp entrant",
            body=f"Expediteur : +{msg.get('from')} — {text}",
            link="/admin/notifications", dedupe_key=f"wa_in:{msg.get('id')}",
        )
        audit.log_action(db, action="whatsapp.inbound", entity_type="message",
                         entity_id=str(msg.get("id") or ""), after={"from": msg.get("from")})
    db.commit()
    return {"received": True, "messages": len(messages),
            "note": "Un message WhatsApp ne confirme jamais un paiement."}
