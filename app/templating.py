"""Rendu Jinja2 sans dependance externe au CDN (tout est inline/local)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from .config import settings
from .models import Shop
from .security import apply_cookie, cookie_options, csrf_for, current_user_optional

TPL_DIR = Path(__file__).resolve().parent / "templates"

env = Environment(
    loader=FileSystemLoader(str(TPL_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    auto_reload=not settings.is_production,
)


def money(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ") + " F"
    except (ValueError, TypeError):
        return "0 F"


def shortnum(value) -> str:
    try:
        n = int(value)
    except (ValueError, TypeError):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1_000:
        return f"{n / 1_000:.1f} k"
    return str(n)


def from_json(value: str):
    try:
        return json.loads(value or "[]")
    except json.JSONDecodeError:
        return []


env.filters["money"] = money
env.filters["shortnum"] = shortnum
env.filters["from_json"] = from_json
env.globals["app_name"] = settings.app_name
env.globals["app_env"] = settings.env
env.globals["public_base_url"] = settings.public_base_url


def render(request: Request, db: OrmSession, name: str, *, status_code: int = 200, **context) -> HTMLResponse:
    user = current_user_optional(request, db)
    shop = None
    if user and user.role == "vendor":
        shop = db.execute(select(Shop).where(Shop.owner_id == user.id)).scalar_one_or_none()
    ctx = {
        "request": request,
        "user": user,
        "my_shop": shop,
        "csrf_token": csrf_for(request, db),
        "path": request.url.path,
        "query": dict(request.query_params),
        "flash_success": request.cookies.get("jaay_flash_ok", ""),
        "flash_error": request.cookies.get("jaay_flash_err", ""),
        "settings": settings,
        **context,
    }
    html = env.get_template(name).render(**ctx)
    response = HTMLResponse(html, status_code=status_code)
    # Les messages flash sont a usage unique.
    if ctx["flash_success"]:
        response.delete_cookie("jaay_flash_ok", path="/")
    if ctx["flash_error"]:
        response.delete_cookie("jaay_flash_err", path="/")
    return response


def flash_redirect(url: str, *, ok: str = "", error: str = "", status_code: int = 303,
                   request: Request | None = None):
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(url, status_code=status_code)
    opts = cookie_options(request)
    if ok:
        apply_cookie(resp, "jaay_flash_ok", ok[:250], max_age=30, **opts)
    if error:
        apply_cookie(resp, "jaay_flash_err", error[:250], max_age=30, **opts)
    return resp
