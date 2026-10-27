"""Construction et configuration de l'application FastAPI Jaay.

Deux points d'entree utilisent ce module :
  * ``index.py`` (racine) : cree litteralement ``app = FastAPI(...)`` puis appelle
    ``configure_app(app)`` — Vercel exige ce motif pour reconnaitre l'entree.
  * ``app/main.py`` : shim ``uvicorn app.main:app`` pour le local et Docker,
    qui re-exporte ``build_app()``.
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import settings
from .db import engine
from .db_network import network_hint
from .diagnostic import configure_diagnostic  # import statique : tracable par le bundler Vercel
from .seed import init_db, seed_demo
from .security import set_csrf_cookie

APP_TITLE = "Jaay — Marketplace multi-vendeurs"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = "Boutiques gratuites, WhatsApp-first, commission tracable. Voir /api/docs."
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.config_error:
        yield
        return
    # Sur serverless, le schema est cree au premier demarrage si JAAY_AUTO_INIT_DB=1.
    # En exploitation etablie, mettre JAAY_AUTO_INIT_DB=0 et utiliser Alembic.
    if settings.auto_init_db:
        try:
            init_db()
        except Exception as exc:  # pragma: no cover - depend de la disponibilite de la DB
            print(f"[startup] init_db ignore : {type(exc).__name__}: {exc}")
    if settings.demo_mode:
        try:
            seed_demo()
        except Exception as exc:  # pragma: no cover
            print(f"[startup] seed_demo ignore : {type(exc).__name__}: {exc}")
    yield


def new_app() -> FastAPI:
    """Fabrique une instance FastAPI nue, avec le lifespan Jaay."""
    return FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )


def configure_app(app: FastAPI) -> FastAPI:
    """Ajoute mounts, middlewares, gestionnaires d'erreurs, /health et routers.

    Appelee aussi bien par l'entree Vercel (``index.py``) que par ``build_app()``.

    Si la configuration est invalide, on transforme l'app en reponse 503
    explicite : l'import du module a reellement reussi, donc le diagnostic est
    disponible (contrairement a un `raise` dans config.py).
    """
    if settings.config_error:
        return configure_diagnostic(app, RuntimeError(settings.config_error))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
    # Le dossier media peut etre en lecture seule sur serverless : check_dir=False
    # evite de faire echouer l'import (les uploads doivent alors utiliser S3/R2).
    app.mount("/media", StaticFiles(directory=str(settings.media_dir), check_dir=False), name="media")

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        # Jeton anonyme historique (double soumission). Le vrai jeton CSRF est signe.
        request.state.anon_csrf = request.cookies.get("jaay_csrf") or secrets.token_urlsafe(32)
        request.state.request_id = secrets.token_hex(8)
        response = await call_next(request)
        if not request.cookies.get("jaay_csrf"):
            set_csrf_cookie(response, request.state.anon_csrf, request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Request-Id", request.state.request_id)
        # CSP : autorise les ressources locales/inline (aucun CDN). frame-ancestors large
        # pour le previsualiseur ; durcir en production (voir docs/07-securite.md).
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; form-action 'self' https://wa.me https://api.whatsapp.com; frame-ancestors *",
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.errors(), "message": "Donnees invalides."}, status_code=422)
        messages = "; ".join(e.get("msg", "") for e in exc.errors()[:2])
        return RedirectResponse(f"{request.url.path}?erreur={messages[:120]}", status_code=303)

    @app.exception_handler(403)
    async def forbidden(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": getattr(exc, "detail", "Acces refuse.")}, status_code=403)
        # Renvoyer vers la page d'origine avec un message clair plutot que d'afficher
        # un code brut "acces_refuse" sur l'accueil.
        referer = request.headers.get("referer", "")
        target = "/connexion"
        if referer:
            ref = urlparse(referer)
            if ref.netloc == request.url.netloc and ref.path.startswith("/"):
                target = ref.path + (("?" + ref.query) if ref.query else "")
        sep = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{sep}erreur=session_expiree", status_code=303)

    @app.get("/health", tags=["system"])
    def health():
        """Sonde de sante : verifie aussi la connexion a la base (sans exponer de secret)."""
        import os

        db_status = "ok"
        db_hint = None
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover
            db_status = f"error:{type(exc).__name__}"
            db_hint = network_hint(exc)
        # Booleens de PRESENCE uniquement : aucune valeur, aucun secret n'est expose.
        # Indispensable pour diagnostiquer un deploiement Vercel depuis l'exterieur,
        # car les variables de type Secret sont invisibles dans le dashboard.
        env_presence = {
            "JAAY_SECRET_KEY": bool(os.environ.get("JAAY_SECRET_KEY")),
            "JAAY_DATABASE_URL": bool(os.environ.get("JAAY_DATABASE_URL")),
            "JAAY_PUBLIC_BASE_URL": bool(os.environ.get("JAAY_PUBLIC_BASE_URL")),
            "JAAY_ENV": bool(os.environ.get("JAAY_ENV")),
            "VERCEL": bool(os.environ.get("VERCEL")),
        }
        return {
            "status": "ok" if db_status == "ok" else "degraded",
            "app": settings.app_name,
            "env": settings.env,
            "version": APP_VERSION,
            "database": db_status,
            "database_backend": "postgresql" if not settings.database_url.startswith("sqlite") else "sqlite",
            "media_backend": "s3" if settings.media_is_object_storage else settings.media_backend,
            "demo_mode": settings.demo_mode,
            "runtime": "vercel" if settings.on_vercel else "local",
            "env_presence": env_presence,
            "database_hint": db_hint,
            "config_warnings": settings.config_warnings or None,
        }

    # Routers HTML et API
    from . import web  # noqa: E402
    from .api import admin, auth, orders as api_orders, payments as api_payments, public, vendor, webhooks  # noqa: E402

    for router in (web.router, auth.router, public.router, api_orders.router,
                   vendor.router, admin.router, api_payments.router, webhooks.router):
        app.include_router(router)
    return app


def build_app() -> FastAPI:
    """Application complete autonome (utile pour les scripts et les tests).

    L'entree Vercel ``index.py`` n'utilise pas cette fonction : elle doit creer
    elle-meme ``app = FastAPI(...)`` pour que le builder Vercel la detecte.
    """
    return configure_app(new_app())
