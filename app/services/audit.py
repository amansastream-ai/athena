"""Journal d'audit append-only, lui aussi chaine par hachage."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session as OrmSession

from ..models import AuditLog, User, utcnow


def _hash(payload: dict[str, Any], prev: str) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "|" + prev
    return hashlib.sha256(raw.encode()).hexdigest()


def log_action(
    db: OrmSession,
    *,
    action: str,
    actor: User | None = None,
    entity_type: str = "",
    entity_id: str = "",
    before: Any = None,
    after: Any = None,
    ip: str = "",
    flush: bool = True,
) -> AuditLog:
    last = db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(1)).scalar_one_or_none()
    prev = last.entry_hash if last else "0" * 64
    created_at = utcnow()
    payload = {
        "action": action,
        "actor": actor.id if actor else None,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before": before,
        "after": after,
        "ip": ip,
        "created_at": created_at.isoformat(timespec="microseconds"),
    }
    row = AuditLog(
        actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else "anonymous",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=json.dumps(before, ensure_ascii=False, default=str) if before is not None else "",
        after_json=json.dumps(after, ensure_ascii=False, default=str) if after is not None else "",
        ip=ip,
        prev_hash=prev,
        entry_hash=_hash(payload, prev),
        created_at=created_at,
    )
    db.add(row)
    if flush:
        db.flush()
    return row


def verify_chain(db: OrmSession) -> tuple[bool, str]:
    prev = "0" * 64
    for row in db.execute(select(AuditLog).order_by(AuditLog.id)).scalars():
        payload = {
            "action": row.action,
            "actor": row.actor_id,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "before": json.loads(row.before_json) if row.before_json else None,
            "after": json.loads(row.after_json) if row.after_json else None,
            "ip": row.ip,
            "created_at": row.created_at.isoformat(timespec="microseconds"),
        }
        if row.prev_hash != prev or _hash(payload, prev) != row.entry_hash:
            return False, f"Rupture de chaine a l'entree #{row.id}"
        prev = row.entry_hash
    return True, "ok"
