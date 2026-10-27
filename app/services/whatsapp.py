"""WhatsApp : liens click-to-chat traces, messages pre-remplis, adaptateur Cloud API.

LIMITE FONDAMENTALE : wa.me ouvre une conversation PRIVEE. La plateforme ne recoit
ni le contenu des messages, ni la confirmation d'une vente. On ne trace donc que le
clic sortant, et on relye la vente par un CODE DE DEMANDE (claim code) que le vendeur
doit fournir lors de sa declaration. L'automatisation totale exige la WhatsApp
Business Platform (Cloud API) avec un numero dedie a la plateforme, verification Meta,
templates approuves et webhooks : voir docs/06.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import string
from urllib.parse import quote, urlencode

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as OrmSession

from ..config import settings
from ..models import Inquiry, Product, Shop, uid, utcnow
from ..security import client_ip, hash_pii, sign_payload, unsign_payload


def claim_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "JY-" + "".join(secrets.choice(alphabet) for _ in range(5))


def absolute_url(request: Request, path: str) -> str:
    base = settings.public_base_url.rstrip("/") if settings.public_base_url else str(request.base_url).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def product_token(product: Product) -> str:
    return sign_payload({"k": "product", "p": product.id, "s": product.shop_id}, ttl_seconds=7 * 24 * 3600)


def shop_token(shop: Shop) -> str:
    return sign_payload({"k": "shop", "s": shop.id}, ttl_seconds=7 * 24 * 3600)


def whatsapp_url(e164: str, text: str) -> str:
    """https://wa.me/<digits>?text=<encode> — format documente par WhatsApp."""
    digits = "".join(c for c in e164 if c.isdigit())
    if not 8 <= len(digits) <= 15:
        raise ValueError("Numero WhatsApp invalide.")
    return f"https://wa.me/{digits}?{urlencode({'text': text})}"


def product_message(*, product: Product, shop: Shop, url: str, code: str) -> str:
    price = f"{product.price_xof:,} F CFA".replace(",", " ")
    return (
        f"Bonjour, je suis interesse par votre produit {product.title}, affiche a {price}. "
        f"Reference : {code}. Boutique : {shop.name} (/{shop.slug}). Lien : {url}. "
        f"Est-il disponible ?"
    )


def shop_message(*, shop: Shop, url: str, code: str) -> str:
    return (
        f"Bonjour {shop.name}, j'ai une question sur vos produits. "
        f"Reference : {code}. Boutique : {url}. Pouvez-vous me renseigner ?"
    )


def decode_token(token: str) -> dict | None:
    data = unsign_payload(token)
    if not data or data.get("k") not in {"product", "shop"}:
        return None
    return data


def tracked_redirect(
    request: Request,
    db: OrmSession,
    token: str,
    *,
    source: str = "",
) -> RedirectResponse:
    """Enregistre l'evenement de demande PUIS redirige vers wa.me.

    L'URL de destination est reconstruite cote serveur a partir du numero verifie
    du vendeur : aucune redirection ouverte n'est possible.
    """
    data = decode_token(token)
    if not data:
        return RedirectResponse("/?wa=lien_invalide", status_code=302)
    shop = db.get(Shop, data.get("s"))
    if not shop or shop.status != "active":
        return RedirectResponse("/?wa=boutique_indisponible", status_code=302)
    product = db.get(Product, data.get("p")) if data.get("p") else None
    if product and product.shop_id != shop.id:
        return RedirectResponse("/?wa=produit_invalide", status_code=302)

    code = claim_code()
    ip = client_ip(request)
    inquiry = Inquiry(
        id=uid(), shop_id=shop.id, product_id=product.id if product else None,
        kind=data["k"], claim_code=code, session_hash=hash_pii(request.cookies.get("jaay_sid", "")),
        ip_hash=hash_pii(ip), user_agent=request.headers.get("user-agent", "")[:300],
        source=(source or request.headers.get("referer", ""))[:60], created_at=utcnow(),
    )
    db.add(inquiry)
    if product:
        product.view_count += 1
        url = absolute_url(request, f"/boutique/{shop.slug}/produit/{product.slug}")
        text = product_message(product=product, shop=shop, url=url, code=code)
    else:
        shop.view_count += 1
        url = absolute_url(request, f"/boutique/{shop.slug}")
        text = shop_message(shop=shop, url=url, code=code)
    db.commit()
    return RedirectResponse(whatsapp_url(shop.whatsapp_e164, text), status_code=302)


def share_links(request: Request, *, url: str, text: str) -> dict[str, str]:
    enc_url, enc_text = quote(url, safe=""), quote(text, safe="")
    return {
        "whatsapp": f"https://wa.me/?text={quote(text + ' ' + url, safe='')}",
        "facebook": f"https://www.facebook.com/sharer/sharer.php?u={enc_url}",
        "x": f"https://twitter.com/intent/tweet?text={enc_text}&url={enc_url}",
        "copy": url,
    }


class CloudApiProvider:
    """WhatsApp Business Platform (Cloud API). Optionnel : exige verification Meta.

    Aucun message n'est envoye sans WA_CLOUD_TOKEN, WA_CLOUD_PHONE_NUMBER_ID et
    WA_CLOUD_APP_SECRET. Les conversations restent soumises a la fenetre de 24 h
    et les messages hors fenetre exigent un template approuve.
    """

    def __init__(self, client=None):
        self._client = client

    def _http(self):
        return self._client or httpx.Client(timeout=15)

    @property
    def enabled(self) -> bool:
        return settings.wa_cloud_enabled

    def verify_signature(self, raw_body: bytes, header: str) -> bool:
        if not settings.wa_cloud_app_secret:
            return False
        expected = "sha256=" + hmac.new(
            settings.wa_cloud_app_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, header or "")

    def verify_subscription(self, mode: str, token: str, challenge: str) -> str | None:
        if (self.enabled and settings.wa_cloud_verify_token
                and mode == "subscribe" and hmac.compare_digest(token, settings.wa_cloud_verify_token)):
            return challenge
        return None

    def send_template(self, *, to_e164: str, template_name: str, language: str = "fr",
                      components: list | None = None) -> dict:
        if not self.enabled:
            raise RuntimeError("WhatsApp Cloud API non configuree (verification Meta requise).")
        payload = {
            "messaging_product": "whatsapp",
            "to": "".join(c for c in to_e164 if c.isdigit()),
            "type": "template",
            "template": {"name": template_name, "language": {"code": language},
                         "components": components or []},
        }
        resp = self._http().post(
            f"{settings.wa_cloud_api_url}/{settings.wa_cloud_phone_number_id}/messages",
            json=payload, headers={"Authorization": f"Bearer {settings.wa_cloud_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_inbound(payload: dict) -> list[dict]:
        """Extrait les messages entrants d'une notification webhook Cloud API."""
        out = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []) or []:
                    out.append({
                        "from": msg.get("from"),
                        "id": msg.get("id"),
                        "type": msg.get("type"),
                        "text": (msg.get("text") or {}).get("body", ""),
                        "timestamp": msg.get("timestamp"),
                    })
        return out
