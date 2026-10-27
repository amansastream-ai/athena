"""Notifications internes + adaptateurs email / webhook (best effort)."""
from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..config import settings
from ..models import Notification, Role, User, uid, utcnow


def _create(db: OrmSession, user_id: str, kind: str, title: str, body: str, link: str, dedupe: str) -> Notification | None:
    if dedupe:
        exists = db.execute(
            select(Notification.id).where(Notification.user_id == user_id, Notification.dedupe_key == dedupe).limit(1)
        ).first()
        if exists:
            return None
    row = Notification(
        id=uid(), user_id=user_id, kind=kind, title=title, body=body,
        link=link, dedupe_key=dedupe, created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def notify_user(db: OrmSession, user_id: str, *, kind: str, title: str, body: str = "",
                link: str = "", dedupe_key: str = "") -> Notification | None:
    return _create(db, user_id, kind, title, body, link, dedupe_key)


def notify_admins(db: OrmSession, *, kind: str, title: str, body: str = "",
                  link: str = "", dedupe_key: str = "") -> list[Notification]:
    admins = list(db.execute(select(User).where(User.role == Role.ADMIN, User.is_active.is_(True))).scalars())
    out = []
    for a in admins:
        row = _create(db, a.id, kind, title, body, link, dedupe_key)
        if row:
            out.append(row)
    _send_email(title, body, [a.email for a in admins if a.email])
    _send_webhook({"kind": kind, "title": title, "body": body, "link": link})
    return out


def order_admin_notification(order, *, event: str) -> tuple[str, str]:
    """Contenu standardise : vendeur, produit, prix, commission, statuts, date, reference."""
    from ..models import Commission
    shop = getattr(order, "_shop_cache", None)
    title = f"[{event}] {order.reference}"
    lines = [
        f"Evenement : {event}",
        f"Commande : {order.reference}",
        f"Boutique : {shop.name if shop else order.shop_id}",
        f"Client : {order.customer_name} ({order.customer_phone_e164})",
        f"Montant : {order.total_xof:,} F CFA".replace(",", " "),
        f"Statut commande : {order.status}",
        f"Statut paiement : {order.payment_status}",
        f"Date : {utcnow():%Y-%m-%d %H:%M:%S} UTC",
        f"Origine : {order.origin}",
    ]
    return title, "\n".join(lines)


def _send_email(subject: str, body: str, recipients: list[str]) -> None:
    if not (settings.smtp_host and recipients):
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = settings.smtp_user or "no-reply@jaay.local"
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as s:
            s.starttls()
            if settings.smtp_user and settings.smtp_password:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception as exc:  # pragma: no cover - best effort
        print(f"[notification] SMTP indisponible : {exc}")


def _send_webhook(payload: dict) -> None:
    if not settings.alert_webhook_url:
        return
    try:
        httpx.post(settings.alert_webhook_url, json=payload, timeout=4)
    except Exception as exc:  # pragma: no cover - best effort
        print(f"[notification] webhook indisponible : {exc}")
