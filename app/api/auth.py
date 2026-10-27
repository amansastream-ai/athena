"""API JSON : inscription, connexion, verification du telephone, session."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .. import schemas
from ..config import settings
from ..db import get_db
from ..models import Role, Shop, ShopStatus, User, uid, utcnow
from ..security import (
    SESSION_COOKIE,
    api_current_user,
    apply_cookie,
    client_ip,
    cookie_options,
    csrf_for,
    consume_otp,
    create_session,
    hash_password,
    new_otp,
    rate_limiter,
    rate_limit,
    sign_payload,
    validate_password,
    verify_csrf,
    verify_password,
)
from ..services import audit
from ..services.sms import send_otp

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", status_code=201)
def register(payload: schemas.RegisterIn, request: Request, db: OrmSession = Depends(get_db)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    rate_limit(request, "api_register", limit=6)
    problem = validate_password(payload.password)
    if problem:
        raise HTTPException(422, problem)
    exists = db.execute(select(User).where(User.phone_e164 == payload.phone)).scalar_one_or_none()
    if exists or (payload.email and db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()):
        raise HTTPException(409, "Numero ou e-mail deja inscrit.")
    user = User(
        id=uid(), email=payload.email, phone_e164=payload.phone, full_name=payload.full_name,
        password_hash=hash_password(payload.password), role=payload.role,
        phone_verified=False, created_at=utcnow(),
    )
    db.add(user)
    db.flush()
    code = new_otp(db, payload.phone)
    delivery = send_otp(payload.phone, code)
    audit.log_action(db, action="user.registered", actor=user, entity_type="user", entity_id=user.id,
                     after={"role": user.role}, ip=client_ip(request))
    db.commit()
    out = {
        "user_id": user.id, "status": "otp_required", "phone": payload.phone,
        "otp_delivery": delivery["channel"], "next": "/verifier-telephone",
    }
    if settings.dev_show_otp and not settings.is_production:
        out["dev_otp"] = code
    return out


@router.post("/verify-phone")
def verify_phone(payload: schemas.OtpVerifyApiIn, request: Request, db: OrmSession = Depends(get_db)):
    """Verifie un code OTP pour un numero donne et active le compte correspondant."""
    rate_limit(request, "api_otp", limit=8)
    if not consume_otp(db, payload.phone, payload.code):
        raise HTTPException(400, "Code incorrect ou expire.")
    user = db.execute(select(User).where(User.phone_e164 == payload.phone)).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Compte introuvable.")
    user.phone_verified = True
    shop = db.execute(select(Shop).where(Shop.owner_id == user.id)).scalar_one_or_none()
    if shop:
        shop.status = ShopStatus.ACTIVE
        shop.approved_at = utcnow()
    audit.log_action(db, action="user.phone_verified", actor=user, entity_type="user",
                     entity_id=user.id, ip=client_ip(request))
    db.commit()
    return {"status": "verified", "role": user.role, "shop_slug": shop.slug if shop else None,
            "next": "/connexion"}


@router.post("/login")
def login(payload: schemas.LoginIn, request: Request, db: OrmSession = Depends(get_db)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    rate_limit(request, "api_login", limit=10)
    from ..security import normalize_phone
    ident = payload.identifier.strip().lower()
    user = db.execute(
        select(User).where((User.email == ident) | (User.phone_e164 == (normalize_phone(payload.identifier) or "__")))
    ).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        user and setattr(user, "failed_logins", user.failed_logins + 1)
        db.commit()
        raise HTTPException(401, "Identifiants incorrects.")
    if not user.phone_verified:
        raise HTTPException(403, "Numero de telephone non verifie.")
    raw, _ = create_session(db, user, request)
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"id": user.id, "role": user.role, "name": user.full_name,
                         "csrf": csrf_for(request, db)})
    apply_cookie(resp, SESSION_COOKIE, raw, max_age=settings.session_ttl_hours * 3600,
                 **cookie_options(request, httponly=True))
    return resp


@router.post("/logout")
def logout(request: Request, db: OrmSession = Depends(get_db)):
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    from ..security import session_from_request
    from fastapi.responses import JSONResponse
    sess = session_from_request(request, db)
    if sess:
        db.delete(sess)
        db.commit()
    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/me")
def me(user: User = Depends(api_current_user)):
    return {"id": user.id, "role": user.role, "name": user.full_name,
            "email": user.email, "phone": user.phone_e164, "phone_verified": user.phone_verified}
