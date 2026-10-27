"""Authentification, sessions, CSRF, jetons signes, limitation de debit et permissions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .config import settings
from .db import get_db
from .models import Role, Session as DbSession, User, in_hours, uid, utcnow

# --------------------------------------------------------------------------- mots de passe
_PBKDF2_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = settings.bcrypt_rounds
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"{_PBKDF2_ALGO}${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = stored.split("$", 3)
        if algo != _PBKDF2_ALGO:
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> str | None:
    if len(password) < 10:
        return "Le mot de passe doit contenir au moins 10 caracteres."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Le mot de passe doit contenir des lettres et des chiffres."
    return None


# --------------------------------------------------------------------------- numeros
def normalize_phone(raw: str, default_cc: str = "221") -> str | None:
    """Retourne un numero E.164 (+221XXXXXXXXX) ou None. Senegal par defaut."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith(default_cc) and len(digits) == len(default_cc) + 9:
        digits = digits[len(default_cc):]
    if len(digits) == 9 and digits[0] in "37":
        return f"+{default_cc}{digits}"
    if 8 <= len(digits) <= 15 and raw.strip().startswith("+"):
        return "+" + digits
    return None


def is_valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", email or ""))


# --------------------------------------------------------------------------- jetons signes
def sign_payload(payload: dict[str, Any], ttl_seconds: int = 3600) -> str:
    data = dict(payload)
    data["exp"] = int(time.time()) + ttl_seconds
    data["nonce"] = secrets.token_hex(6)
    body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def unsign_payload(token: str) -> dict[str, Any] | None:
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padded = body + "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_pii(value: str) -> str:
    return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- sessions
SESSION_COOKIE = "jaay_sid"


def create_session(db: OrmSession, user: User, request: Request) -> tuple[str, DbSession]:
    raw = secrets.token_urlsafe(32)
    row = DbSession(
        id=hash_token(raw),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent", ""))[:300],
        expires_at=in_hours(settings.session_ttl_hours),
    )
    db.add(row)
    user.last_login_at = utcnow()
    user.failed_logins = 0
    user.locked_until = None
    db.commit()
    return raw, row


def session_from_request(request: Request, db: OrmSession) -> DbSession | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    row = db.get(DbSession, hash_token(raw))
    if not row or row.expires_at < utcnow():
        return None
    return row


def current_user_optional(request: Request, db: OrmSession = Depends(get_db)) -> User | None:
    sess = session_from_request(request, db)
    if not sess:
        return None
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        return None
    return user


def current_user(request: Request, db: OrmSession = Depends(get_db)) -> User:
    user = current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/connexion"})
    return user


def require_role(*roles: str):
    def _dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Acces refuse : permissions insuffisantes.")
        return user
    return _dep


require_admin = require_role(Role.ADMIN)
require_vendor = require_role(Role.VENDOR)
require_customer = require_role(Role.CUSTOMER, Role.ADMIN, Role.VENDOR)


def api_current_user(request: Request, db: OrmSession = Depends(get_db)) -> User:
    user = current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentification requise.")
    return user


def api_role(*roles: str):
    def _dep(user: User = Depends(api_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Permissions insuffisantes.")
        return user
    return _dep


api_admin = api_role(Role.ADMIN)
api_vendor = api_role(Role.VENDOR)


def request_is_https(request: Request) -> bool:
    """Derriere Vercel / un reverse proxy, le schema est dans X-Forwarded-Proto."""
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" or request.url.scheme == "https"


def cookie_options(request: Request | None, *, httponly: bool = False) -> dict:
    """Options de cookie compatibles avec une application servie en HTTPS.

    En iframe inter-sites (apercu, integraton), SameSite=Lax empeche le navigateur
    d'envoyer les cookies sur un POST : il faut SameSite=None + Secure (+ Partitioned,
    CHIPS, pour les navigateurs qui bloquent les cookies tiers). En HTTP local on
    reste en Lax, plus strict.
    """
    https = request_is_https(request) if request is not None else False
    # Sans Request (ex. flash_redirect historique), on se fie a la configuration :
    # sur Vercel tout est en HTTPS, donc SameSite=None est necessaire.
    if https or (request is None and (settings.cookie_secure or settings.on_vercel)):
        return {"httponly": httponly, "secure": True, "samesite": "none",
                "partitioned": True, "path": "/"}
    return {"httponly": httponly, "secure": settings.cookie_secure,
            "samesite": "lax", "path": "/"}


CSRF_TTL_SECONDS = 12 * 3600


def _csrf_scope(request: Request, db: OrmSession) -> str:
    sess = session_from_request(request, db)
    return f"session:{sess.id}" if sess else "anon"


def csrf_for(request: Request, db: OrmSession) -> str:
    """Jeton CSRF signe HMAC : verifiable SANS cookie (iframe, cookies tiers bloques).

    Il est lie a la session courante (ou a 'anon'), ce qui empeche de rejouer le
    jeton d'une autre session. La protection ne repose donc plus uniquement sur le
    cookie de double soumission.
    """
    return sign_payload({"k": "csrf", "s": _csrf_scope(request, db)},
                        ttl_seconds=CSRF_TTL_SECONDS)


def verify_csrf(request: Request, db: OrmSession, token: str | None) -> None:
    """Toute mutation POST exige un jeton CSRF valide.

    Deux formats acceptes :
      * historique : valeur du cookie jaay_csrf / du jeton de session (double soumission) ;
      * nouveau : jeton signe HMAC verifiable cote serveur sans aucun cookie.
    """
    provided = token or request.headers.get("x-csrf-token", "")
    if not provided:
        raise HTTPException(status_code=403, detail="Jeton CSRF invalide ou absent.")
    sess = session_from_request(request, db)
    anon_cookie = request.cookies.get("jaay_csrf", "")
    legacy = sess.csrf_token if sess else anon_cookie
    if legacy and hmac.compare_digest(str(legacy), str(provided)):
        return
    data = unsign_payload(str(provided))
    if data and data.get("k") == "csrf":
        expected = f"session:{sess.id}" if sess else "anon"
        if hmac.compare_digest(str(data.get("s", "")), expected):
            return
    raise HTTPException(status_code=403, detail="Jeton CSRF invalide ou absent.")


_PARTITIONED_NATIVE = sys.version_info >= (3, 14)


def apply_cookie(response, key: str, value: str, *, max_age: int | None = None, **opts) -> None:
    """Pose un cookie avec les bonnes options, CHIPS inclus sur Python < 3.14.

    Starlette leve ValueError pour `partitioned=True` avant Python 3.14. Or
    l'attribut `Partitioned` (CHIPS) est indispensable pour qu'un cookie tiers
    soit accepte dans une iframe inter-sites par Chrome. On l'ajoute donc
    directement dans l'en-tete Set-Cookie quand le runtime ne le gere pas.
    """
    partitioned = bool(opts.pop("partitioned", False))
    if partitioned and _PARTITIONED_NATIVE:
        opts["partitioned"] = True
    response.set_cookie(key, value, max_age=max_age, **opts)
    if partitioned and not _PARTITIONED_NATIVE:
        needle = key.encode("latin-1") + b"="
        rebuilt, done = [], False
        for name, raw in reversed(response.raw_headers):
            if not done and name == b"set-cookie" and raw.startswith(needle):
                raw = raw + b"; Partitioned"
                done = True
            rebuilt.append((name, raw))
        response.raw_headers = list(reversed(rebuilt))


def set_csrf_cookie(response, token: str, request: Request | None = None) -> None:
    apply_cookie(
        response, "jaay_csrf", token,
        max_age=settings.session_ttl_hours * 3600, **cookie_options(request)
    )


def client_ip(request: Request) -> str:
    # Derriere un reverse proxy de confiance uniquement (X-Forwarded-For ecrase sinon).
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd and settings.is_production:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "0.0.0.0")[:64]


# --------------------------------------------------------------------------- limitation de debit
class _SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] < now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = _SlidingWindowLimiter()


def rate_limit(request: Request, scope: str, limit: int | None = None) -> None:
    key = f"{scope}:{client_ip(request)}"
    if not rate_limiter.hit(key, limit or settings.rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Trop de requetes. Reessayez dans une minute.")


# --------------------------------------------------------------------------- OTP
def new_otp(db: OrmSession, phone: str, purpose: str = "verify_phone") -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    from .models import OtpCode

    db.add(
        OtpCode(
            id=uid(), phone_e164=phone, code_hash=hash_token(code + purpose + phone),
            purpose=purpose, expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    db.commit()
    return code


def consume_otp(db: OrmSession, phone: str, code: str, purpose: str = "verify_phone") -> bool:
    from .models import OtpCode
    rows = db.execute(
        select(OtpCode).where(
            OtpCode.phone_e164 == phone, OtpCode.purpose == purpose, OtpCode.consumed_at.is_(None)
        ).order_by(OtpCode.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if not rows or rows.expires_at < utcnow() or rows.attempts >= 5:
        return False
    rows.attempts += 1
    ok = hmac.compare_digest(rows.code_hash, hash_token(code + purpose + phone))
    if ok:
        rows.consumed_at = utcnow()
    db.commit()
    return ok
