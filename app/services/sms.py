"""Envoi OTP : console en developpement, adaptateur HTTP en production.

Aucun fournisseur SMS n'est integre par defaut. Definir SMS_API_URL et SMS_API_KEY
vers un fournisseur compatible : POST JSON {to, message, sender}.
Le code OTP n'est JAMAIS renvoye par une API publique en production.
"""
from __future__ import annotations

import httpx

from ..config import settings


def send_otp(phone: str, code: str) -> dict:
    message = f"Jaay : votre code de verification est {code}. Il expire dans 10 minutes. Ne le partagez pas."
    if settings.sms_api_url and settings.sms_api_key:
        try:
            resp = httpx.post(
                settings.sms_api_url,
                json={"to": phone, "message": message, "sender": settings.sms_sender},
                headers={"Authorization": f"Bearer {settings.sms_api_key}"},
                timeout=8,
            )
            resp.raise_for_status()
            return {"channel": "sms", "delivered": True}
        except Exception as exc:  # pragma: no cover
            return {"channel": "sms", "delivered": False, "error": str(exc)}
    print(f"[OTP/dev] {phone} -> {message}")
    return {"channel": "console", "delivered": True}
