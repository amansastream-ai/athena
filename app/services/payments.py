"""Paiements : adaptateurs fournisseurs + parcours manuel de secours.

REGLE ABSOLUE : aucun clic, aucune redirection client et aucune declaration vendeur
ne peut marquer un paiement comme confirme. Seuls :
  - un webhook signe (HMAC, horodatage, anti-rejeu) + RE-VERIFICATION serveur de la
    session chez le fournisseur, ou
  - la validation explicite d'une preuve par un ADMINISTRATEUR
peuvent creer une ligne Payment verifiee et declencher la commission.

Les adaptateurs Wave / Orange Money sont DESACTIVES tant que les acces marchands,
les cles et les tests sandbox reels n'ont pas ete obtenus. Le code des appels HTTP
est reel (aucun stub), mais aucune transaction reelle n'a ete testee ici.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from ..config import settings
from ..models import (
    Order,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentProof,
    PaymentStatus,
    Shop,
    User,
    WebhookEvent,
    uid,
    utcnow,
)
from . import audit, fraud, ledger, notifications
from .registry import get_setting


class PaymentError(Exception):
    pass


# --------------------------------------------------------------------------- etat des capacites
def provider_capabilities(db: OrmSession) -> list[dict[str, Any]]:
    cfg = get_setting(db, "wave_checkout_enabled", False)
    om = get_setting(db, "om_webpay_enabled", False)
    return [
        {
            "code": "manual",
            "name": "Paiement mobile hors plateforme + preuve",
            "state": "operational" if get_setting(db, "manual_payment_enabled", True) else "disabled",
            "dynamic_link": False,
            "webhook": False,
            "auto_confirmation": False,
            "note": "Reference de commande + preuve + validation administrateur. Disponible des aujourd'hui.",
        },
        {
            "code": "wave",
            "name": "Wave Business Checkout",
            "state": "live" if (settings.wave_enabled and cfg and settings.wave_mode == "live")
                     else "sandbox" if (settings.wave_enabled and cfg) else "not_configured",
            "dynamic_link": bool(settings.wave_enabled and cfg),
            "webhook": bool(settings.wave_enabled and cfg),
            "auto_confirmation": bool(settings.wave_enabled and cfg),
            "note": "Necessite un compte Wave Business valide (KYB), une cle API et un secret webhook. "
                    "Sessions de checkout, signature Wave-Signature, aggregated_merchant_id pour le multi-vendeurs.",
        },
        {
            "code": "orange_money",
            "name": "Orange Money Web Payment",
            "state": "live" if (settings.om_enabled and om and settings.om_mode == "live")
                     else "sandbox" if (settings.om_enabled and om) else "not_configured",
            "dynamic_link": bool(settings.om_enabled and om),
            "webhook": bool(settings.om_enabled and om),
            "auto_confirmation": bool(settings.om_enabled and om),
            "note": "Necessite un contrat marchand Orange/Sonatel (KYB) et la confirmation des URLs "
                    "d'API Senegal sur le portail developpeur. Statut verifie cote serveur avant confirmation.",
        },
    ]


# --------------------------------------------------------------------------- paiement manuel
def manual_instructions(db: OrmSession, order: Order, shop: Shop) -> dict[str, Any]:
    methods = list(
        db.execute(
            select(PaymentMethod).where(
                PaymentMethod.shop_id == shop.id, PaymentMethod.is_active.is_(True)
            ).order_by(PaymentMethod.priority)
        ).scalars()
    )
    platform_number = get_setting(db, "platform_collection_number", "")
    return {
        "amount_xof": order.total_xof,
        "reference": order.reference,
        "currency": "XOF",
        "platform_number": platform_number,
        "platform_label": get_setting(db, "platform_collection_label", "Compte plateforme Jaay"),
        "methods": [m for m in methods if m.is_verified],
        "steps": [
            "1. Effectuez le virement Wave / Orange Money du montant exact indique.",
            f"2. Indiquez imperativement la reference {order.reference} dans le libelle.",
            "3. Conservez l'identifiant de transaction fourni par l'operateur.",
            "4. Soumettez la preuve ci-dessous ; un administrateur verifie avant confirmation.",
        ],
        "warning": "Le statut reste 'en attente de verification' tant qu'un administrateur n'a pas valide. "
                   "La plateforme ne voit pas les conversations WhatsApp privees ni les transferts directs.",
    }


def submit_proof(
    db: OrmSession,
    order: Order,
    *,
    user: User | None,
    provider: str,
    provider_reference: str,
    amount_xof: int,
    note: str = "",
    evidence_url: str = "",
    ip: str = "",
) -> PaymentProof:
    if order.payment_status == PaymentStatus.PAID:
        raise PaymentError("Cette commande est deja payee.")
    if amount_xof != order.total_xof:
        raise PaymentError(f"Montant incorrect : attendu {order.total_xof} F CFA.")
    if fraud.duplicate_reference(db, provider, provider_reference, order.id):
        raise PaymentError("Cette reference de paiement a deja ete soumise.")
    proof = PaymentProof(
        id=uid(), order_id=order.id, submitted_by=user.id if user else None,
        provider=provider, provider_reference=provider_reference, amount_xof=amount_xof,
        note=note, evidence_url=evidence_url, status="pending", created_at=utcnow(),
    )
    db.add(proof)
    order.payment_status = PaymentStatus.PENDING_VERIFICATION
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PENDING_VERIFICATION
    db.flush()
    shop = db.get(Shop, order.shop_id)
    order._shop_cache = shop
    title, body = notifications.order_admin_notification(order, event="Preuve de paiement soumise")
    body += f"\nFournisseur : {provider}\nReference : {provider_reference}\nMontant : {amount_xof} F CFA"
    notifications.notify_admins(db, kind="payment_proof", title=title, body=body,
                                link=f"/admin/commandes/{order.id}")
    audit.log_action(db, action="payment.proof_submitted", actor=user, entity_type="payment_proof",
                     entity_id=proof.id, after={"order": order.reference, "provider": provider,
                                                "reference": provider_reference, "amount": amount_xof}, ip=ip)
    db.commit()
    return proof


def review_proof(
    db: OrmSession,
    proof: PaymentProof,
    *,
    admin: User,
    approve: bool,
    review_note: str = "",
    ip: str = "",
) -> Payment | None:
    if admin.role != "admin":
        raise PaymentError("Seul un administrateur peut valider une preuve de paiement.")
    if proof.status != "pending":
        raise PaymentError("Cette preuve a deja ete traitee.")
    order = db.get(Order, proof.order_id)
    if not order:
        raise PaymentError("Commande introuvable.")
    proof.reviewed_by = admin.id
    proof.reviewed_at = utcnow()
    proof.review_note = review_note
    if not approve:
        proof.status = "rejected"
        other = db.execute(
            select(PaymentProof).where(
                PaymentProof.order_id == order.id, PaymentProof.status == "pending"
            ).limit(1)
        ).first()
        if not other and order.payment_status != PaymentStatus.PAID:
            order.payment_status = PaymentStatus.UNPAID
        db.commit()
        audit.log_action(db, action="payment.proof_rejected", actor=admin, entity_type="payment_proof",
                         entity_id=proof.id, after={"order": order.reference}, ip=ip)
        db.commit()
        return None

    # On exclut les preuves de cette commande elle-meme, mais pas celles des autres commandes.
    if fraud.duplicate_reference(db, proof.provider, proof.provider_reference, order.id):
        raise PaymentError("Cette reference de paiement est deja associee a une autre commande.")
    proof.status = "accepted"
    db.flush()
    payment = mark_paid(
        db, order, provider=proof.provider, provider_reference=proof.provider_reference,
        amount_xof=proof.amount_xof, verification="admin:proof", verified_by=admin,
        evidence_url=proof.evidence_url,
    )
    audit.log_action(db, action="payment.proof_accepted", actor=admin, entity_type="payment_proof",
                     entity_id=proof.id, after={"order": order.reference, "amount": proof.amount_xof}, ip=ip)
    db.commit()
    return payment


# --------------------------------------------------------------------------- confirmation centrale
def mark_paid(
    db: OrmSession,
    order: Order,
    *,
    provider: str,
    provider_reference: str,
    amount_xof: int,
    verification: str,
    verified_by: User | None = None,
    evidence_url: str = "",
    raw_payload: dict | None = None,
) -> Payment:
    """Unique point d'entree qui transforme une commande en vente reconnue."""
    if amount_xof != order.total_xof:
        raise PaymentError(f"Montant incoherent : recu {amount_xof}, attendu {order.total_xof}.")
    if not verification or verification == "client_click":
        raise PaymentError("Verification serveur obligatoire.")
    if order.status in {OrderStatus.CANCELLED, OrderStatus.REFUNDED}:
        raise PaymentError("Commande annulee ou rembourse : paiement refuse.")
    if not provider_reference:
        raise PaymentError("Reference de transaction fournisseur manquante.")

    existing = db.execute(
        select(Payment).where(
            Payment.provider == provider, Payment.provider_reference == provider_reference
        ).limit(1)
    ).scalar_one_or_none()
    if existing:
        if existing.order_id != order.id:
            raise PaymentError("Reference de paiement deja utilisee pour une autre commande.")
        return existing  # idempotence : un webhook renvoye ne refacture pas

    payment = Payment(
        id=uid(), order_id=order.id, provider=provider,
        provider_reference=provider_reference, amount_xof=amount_xof, currency="XOF",
        status=PaymentStatus.PAID, verification=verification,
        verified_by=verified_by.id if verified_by else None, verified_at=utcnow(),
        evidence_url=evidence_url, raw_payload_json=json.dumps(raw_payload or {}, ensure_ascii=False),
        created_at=utcnow(),
    )
    db.add(payment)
    order.payment_status = PaymentStatus.PAID
    order.paid_at = utcnow()
    if order.status in {OrderStatus.PENDING, OrderStatus.PENDING_VERIFICATION, OrderStatus.CONFIRMED}:
        order.status = OrderStatus.PAID
    order.version += 1
    db.flush()

    commission = ledger.maybe_recognize_sale(db, order, actor=verification, verification=verification)
    shop = db.get(Shop, order.shop_id)
    order._shop_cache = shop
    title, body = notifications.order_admin_notification(order, event="Paiement confirme")
    body += f"\nFournisseur : {provider}\nReference transaction : {provider_reference}"
    body += f"\nMontant : {amount_xof} F CFA"
    body += f"\nCommission plateforme : {commission.platform_fee_xof if commission else 0} F CFA"
    body += f"\nVerification : {verification}"
    notifications.notify_admins(db, kind="payment_confirmed", title=title, body=body,
                                link=f"/admin/commandes/{order.id}",
                                dedupe_key=f"payment_confirmed:{order.id}")
    notifications.notify_user(db, shop.owner_id, kind="payment_confirmed",
                              title=f"Paiement confirme - {order.reference}",
                              body=f"Montant {amount_xof} F CFA. Commission due : "
                                   f"{commission.platform_fee_xof if commission else 0} F CFA.",
                              link=f"/vendeur/commandes/{order.id}",
                              dedupe_key=f"vendor_payment:{order.id}")
    audit.log_action(db, action="payment.confirmed", actor=verified_by, entity_type="payment",
                     entity_id=payment.id, after={"order": order.reference, "provider": provider,
                                                  "amount": amount_xof, "verification": verification,
                                                  "commission": commission.platform_fee_xof if commission else 0})
    db.commit()
    return payment


# --------------------------------------------------------------------------- utilitaires webhook
def register_webhook_event(db: OrmSession, provider: str, event_id: str, event_type: str,
                           payload: dict, signature_valid: bool) -> WebhookEvent:
    """Anti-rejeu : contrainte UNIQUE(provider, event_id)."""
    try:
        with db.begin_nested():
            row = WebhookEvent(
                id=uid(), provider=provider, event_id=event_id, event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
                signature_valid=signature_valid, status="received", received_at=utcnow(),
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return db.execute(
            select(WebhookEvent).where(
                WebhookEvent.provider == provider, WebhookEvent.event_id == event_id
            )
        ).scalar_one()


# --------------------------------------------------------------------------- Wave
class WaveCheckoutProvider:
    """https://docs.wave.com/checkout , /business , /webhook , /aggregated-merchants"""

    code = "wave"
    EVENT_OK = "checkout.session.completed"

    def __init__(self, client: Any | None = None):
        self._client = client

    def _http(self):
        return self._client or httpx.Client(timeout=15)

    def _signature(self, timestamp: str, body: str) -> str:
        secret = settings.wave_signing_secret or settings.wave_webhook_secret
        return hmac.new(secret.encode(), (timestamp + body).encode(), hashlib.sha256).hexdigest()

    def create_session(self, db: OrmSession, order: Order, *, public_base_url: str,
                       aggregated_merchant_id: str = "") -> dict[str, Any]:
        if not settings.wave_enabled or not get_setting(db, "wave_checkout_enabled", False):
            raise PaymentError("Wave Checkout n'est pas active (cles ou activation manquantes).")
        body_dict: dict[str, Any] = {
            "amount": str(order.total_xof),
            "currency": "XOF",
            "client_reference": order.reference,
            "success_url": f"{public_base_url}/commandes/{order.reference}?paiement=retour",
            "error_url": f"{public_base_url}/commandes/{order.reference}?paiement=echec",
        }
        am_id = aggregated_merchant_id or settings.wave_aggregated_merchant_id
        if am_id:
            body_dict["aggregated_merchant_id"] = am_id
        body = json.dumps(body_dict, separators=(",", ":"))
        ts = str(int(time.time()))
        headers = {
            "Authorization": f"Bearer {settings.wave_api_key}",
            "Content-Type": "application/json",
            "Wave-Signature": f"t={ts},v1={self._signature(ts, body)}",
        }
        resp = self._http().post(f"{settings.wave_base_url}/v1/checkout/sessions",
                                 content=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        launch = data.get("wave_launch_url")
        session_id = data.get("id")
        if not launch or not session_id:
            raise PaymentError("Reponse Wave invalide : wave_launch_url ou id manquant.")
        db.add(Payment(
            id=uid(), order_id=order.id, provider=self.code, provider_reference=session_id,
            amount_xof=order.total_xof, status=PaymentStatus.PENDING,
            raw_payload_json=json.dumps(data), created_at=utcnow(),
        ))
        db.commit()
        return {"launch_url": launch, "session_id": session_id, "raw": data}

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Wave-Signature: t=<unix>,v1=<hmac_sha256(timestamp + corps_brut)>. Rejete > 5 min."""
        header = headers.get("wave-signature", "")
        parts = dict(
            p.split("=", 1) for p in header.split(",") if "=" in p
        )
        ts, sig = parts.get("t", ""), parts.get("v1", "")
        if not ts or not sig:
            raise PaymentError("Signature Wave absente.")
        try:
            age = abs(int(time.time()) - int(ts))
        except ValueError:
            raise PaymentError("Horodatage Wave invalide.")
        if age > 300:
            raise PaymentError("Webhook Wave expire (anti-rejeu, > 5 min).")
        expected = hmac.new(settings.wave_webhook_secret.encode(),
                            ts.encode() + raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise PaymentError("Signature Wave invalide.")
        try:
            event = json.loads(raw_body)
        except json.JSONDecodeError:
            raise PaymentError("Corps JSON Wave invalide.")
        if not event.get("id") or not event.get("type"):
            raise PaymentError("Enveloppe d'evenement Wave invalide.")
        return event

    def remote_status(self, session_id: str) -> dict[str, Any]:
        """Verification cote serveur : on ne se fie jamais au seul corps du webhook."""
        resp = self._http().get(
            f"{settings.wave_base_url}/v1/checkout/sessions/{session_id}",
            headers={"Authorization": f"Bearer {settings.wave_api_key}"},
        )
        resp.raise_for_status()
        return resp.json()

    def handle_event(self, db: OrmSession, event: dict[str, Any], *, recheck: bool = True) -> str:
        event_id, event_type = event["id"], event["type"]
        row = register_webhook_event(db, self.code, event_id, event_type, event, True)
        if row.status == "processed":
            return "duplicate"
        if event_type != self.EVENT_OK:
            row.status = "ignored"
            row.processed_at = utcnow()
            db.commit()
            return "ignored"
        data = event.get("data", {})
        session_id = data.get("id", "")
        ref = data.get("client_reference", "")
        order = db.execute(select(Order).where(Order.reference == ref)).scalar_one_or_none()
        if not order:
            row.status, row.error = "rejected", "commande inconnue"
            db.commit()
            return "unknown_order"
        if str(data.get("amount")) != str(order.total_xof) or data.get("currency") != "XOF":
            row.status, row.error = "rejected", "montant ou devise incoherent"
            db.commit()
            return "amount_mismatch"
        if data.get("payment_status") != "succeeded" or data.get("checkout_status") != "complete":
            row.status, row.error = "rejected", "session non complete"
            db.commit()
            return "not_complete"

        if recheck:
            remote = self.remote_status(session_id)
            if (remote.get("payment_status") != "succeeded"
                    or remote.get("checkout_status") != "complete"
                    or str(remote.get("amount")) != str(order.total_xof)
                    or remote.get("client_reference") != order.reference):
                row.status, row.error = "rejected", "verification distante echouee"
                db.commit()
                return "remote_mismatch"
            data = remote

        mark_paid(db, order, provider=self.code, provider_reference=session_id,
                  amount_xof=order.total_xof, verification="webhook:wave", raw_payload=data)
        row.status, row.processed_at = "processed", utcnow()
        db.commit()
        return "processed"


# --------------------------------------------------------------------------- Orange Money
class OrangeMoneyWebPayProvider:
    """Orange Money Web Payment : OAuth + initiation + notif_url.

    Les chemins d'API, la forme exacte du callback et les statuts du marche Senegal
    doivent etre confirmes sur le portail Orange Sonatel apres signature du contrat
    marchand. Toutes les URLs sont donc configurables par variable d'environnement ;
    l'adaptateur refuse de confirmer un paiement si la verification distante n'est
    pas configurable (fail-closed).
    """

    code = "orange_money"

    def __init__(self, client: Any | None = None):
        self._client = client

    def _http(self):
        return self._client or httpx.Client(timeout=15)

    def _token(self) -> str:
        resp = self._http().post(
            settings.om_token_url,
            data={"grant_type": "client_credentials"},
            auth=(settings.om_client_id, settings.om_client_secret),
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def create_session(self, db: OrmSession, order: Order, *, public_base_url: str) -> dict[str, Any]:
        if not settings.om_enabled or not get_setting(db, "om_webpay_enabled", False):
            raise PaymentError("Orange Money Web Payment n'est pas active.")
        token = self._token()
        currency = "XOF" if settings.om_mode == "live" else "OUV"
        body = {
            "merchant_key": settings.om_merchant_key,
            "currency": currency,
            "order_id": order.reference,
            "amount": order.total_xof,
            "return_url": f"{public_base_url}/commandes/{order.reference}?paiement=retour",
            "cancel_url": f"{public_base_url}/commandes/{order.reference}?paiement=echec",
            "notif_url": f"{public_base_url}/webhooks/orange-money",
            "lang": "fr",
            "reference": order.reference,
        }
        resp = self._http().post(settings.om_webpayment_url, json=body,
                                 headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        data = resp.json()
        pay_token = data.get("pay_token") or data.get("payment_token") or ""
        payment_url = data.get("payment_url") or data.get("url") or ""
        if not pay_token or not payment_url:
            raise PaymentError("Reponse Orange Money invalide (pay_token / payment_url manquants).")
        db.add(Payment(
            id=uid(), order_id=order.id, provider=self.code, provider_reference=pay_token,
            amount_xof=order.total_xof, status=PaymentStatus.PENDING,
            raw_payload_json=json.dumps(data), created_at=utcnow(),
        ))
        db.commit()
        return {"launch_url": payment_url, "session_id": pay_token, "raw": data}

    def verify_webhook(self, raw_body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        sig = headers.get(settings.om_webhook_header.lower(), "")
        if not sig:
            raise PaymentError("Signature Orange Money absente.")
        expected = hmac.new(settings.om_webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        candidate = sig.split("=", 1)[-1].strip()
        if not hmac.compare_digest(expected, candidate):
            raise PaymentError("Signature Orange Money invalide.")
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError:
            raise PaymentError("Corps JSON Orange Money invalide.")

    def remote_status(self, pay_token: str) -> dict[str, Any]:
        if not settings.om_status_url:
            raise PaymentError("OM_STATUS_URL non configure : verification distante impossible (fail-closed).")
        token = self._token()
        resp = self._http().get(
            f"{settings.om_status_url.rstrip('/')}/{pay_token}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()

    def handle_event(self, db: OrmSession, event: dict[str, Any], *, recheck: bool = True) -> str:
        event_id = str(event.get("txnid") or event.get("transaction_id") or event.get("id") or "")
        if not event_id:
            raise PaymentError("Identifiant d'evenement Orange Money manquant.")
        row = register_webhook_event(db, self.code, event_id, str(event.get("status", "")), event, True)
        if row.status == "processed":
            return "duplicate"
        ref = str(event.get("order_id") or event.get("reference") or "")
        order = db.execute(select(Order).where(Order.reference == ref)).scalar_one_or_none()
        if not order:
            row.status, row.error = "rejected", "commande inconnue"
            db.commit()
            return "unknown_order"
        pay_token = str(event.get("pay_token") or event.get("payment_token") or event_id)
        if recheck:
            remote = self.remote_status(pay_token)
            status = str(remote.get("status", "")).upper()
            if status not in {"SUCCESS", "SUCCESSFUL", "COMPLETED", "PAID", "OK"}:
                row.status, row.error = "rejected", f"statut distant non final : {status}"
                db.commit()
                return "remote_not_success"
            amount = int(remote.get("amount", 0))
            if amount and amount != order.total_xof:
                row.status, row.error = "rejected", "montant distant incoherent"
                db.commit()
                return "amount_mismatch"
            event = remote
        mark_paid(db, order, provider=self.code, provider_reference=pay_token,
                  amount_xof=order.total_xof, verification="webhook:orange_money", raw_payload=event)
        row.status, row.processed_at = "processed", utcnow()
        db.commit()
        return "processed"
