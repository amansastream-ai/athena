"""Point d'entree ASGI pour Vercel Functions.

Deux contraintes Vercel, confirmees par les logs d'execution :

1. Le builder analyse ce fichier STATIQUEMENT et exige un appel literal
   ``app = FastAPI(...)`` au niveau du module (sinon FASTAPI_ENTRYPOINT_NOT_FOUND).
2. Le bundler suit les imports statiques pour decidre quels fichiers mettre dans
   la fonction. Un ``from app.application import ...`` place dans un bloc
   ``try/except`` n'etait PAS trace -> ``ModuleNotFoundError: No module named 'app'``
   dans /var/task. L'import est donc au NIVEAU DU MODULE, sans try.

La configuration ne leve plus d'exception a l'import (voir app/config.py) : elle
expose ``settings.config_error`` et ``configure_app()`` produit un 503 explicite.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

# IMPORT STATIQUE AU NIVEAU DU MODULE : indispensable pour que Vercel inclue
# le package app/ (code, templates, CSS) dans le bundle de la fonction.
from app.application import configure_app, lifespan  # noqa: E402
from app.config import settings  # noqa: E402
from app.db_network import diagnostics as database_diagnostics  # noqa: E402
from app.db_network import network_hint  # noqa: E402
from app.diagnostic import configure_diagnostic  # noqa: E402

# NE PAS remplacer cette ligne par un import : le builder Vercel la cherche.
app = FastAPI(
    title="Jaay — Marketplace multi-vendeurs",
    version="0.1.0",
    description="Boutiques gratuites, WhatsApp-first, commission tracable. Voir /api/docs.",
    docs_url="/api/docs",
    redoc_url=None,
)

app.router.lifespan_context = lifespan
try:
    configure_app(app)
except Exception as exc:  # pragma: no cover - erreur inattendue de configuration
    configure_diagnostic(app, exc)


def _safe_error(text: str) -> str:
    url = os.environ.get("JAAY_DATABASE_URL", "")
    if url and url in text:
        text = text.replace(url, re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", url))
    return re.sub(r"//([^:/@\s]+):[^@\s]*@", r"//\1:***@", text)[:1500]


WATCHED_KEYS = (
    "JAAY_ENV", "JAAY_SECRET_KEY", "JAAY_DATABASE_URL", "JAAY_PUBLIC_BASE_URL",
    "JAAY_COOKIE_SECURE", "JAAY_DEV_SHOW_OTP", "JAAY_DEMO_MODE",
    "JAAY_AUTO_INIT_DB", "JAAY_ALLOW_IPV6_DB", "VERCEL",
)


async def _diag(request: Request):  # noqa: ARG001
    """Sonde : indique si le package app/ est present dans le bundle."""
    import importlib
    import traceback

    package_dir = ROOT / "app"
    out = {
        "status": "diagnostic",
        "python": sys.version.split()[0],
        "root": str(ROOT),
        "cwd": os.getcwd(),
        "package_present": package_dir.is_dir(),
        "package_files": sorted(p.name for p in package_dir.iterdir())[:25] if package_dir.is_dir() else [],
        "app_title": app.title,
        "routes_count": len(app.router.routes),
        "config_error": _safe_error(settings.config_error) or None,
        "config_warnings": _safe_error(settings.config_warnings) or None,
        "env_presence": {key: bool(os.environ.get(key)) for key in WATCHED_KEYS},
        "database_is_postgres": os.environ.get("JAAY_DATABASE_URL", "").startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg2://")
        ),
        # Rapport reseau SANS secret : hote, port, adresses IPv4/IPv6 resolues
        # et conseil. C'est ce qui permet de voir le probleme Supabase/Vercel.
        "database": database_diagnostics(
            os.environ.get("JAAY_DATABASE_URL", "") or settings.database_url
        ),
        "jaay_env_keys": sorted(k for k in os.environ if k.startswith("JAAY_")),
    }
    try:
        importlib.import_module("app.application")
        out["application_import"] = "ok"
    except Exception as exc:
        out["application_import"] = "error"
        out["error"] = _safe_error(f"{type(exc).__name__}: {exc}")
        out["traceback"] = (_safe_error(traceback.format_exc())
                            if os.environ.get("JAAY_DEBUG_ERRORS") == "1"
                            else "activez JAAY_DEBUG_ERRORS=1 pour la trace complete")
    return JSONResponse(out)


# Enregistree EN PREMIERE position pour passer avant le catch-all de diagnostic.
app.add_api_route("/__diag", _diag, methods=["GET"], include_in_schema=False)
_diag_route = app.router.routes.pop()
app.router.routes.insert(0, _diag_route)


@app.middleware("http")
async def _runtime_error_middleware(request, call_next):
    """Dernier filet : renvoie un JSON lisible au lieu d'un 500 Vercel muet."""
    import traceback

    try:
        return await call_next(request)
    except Exception as exc:
        body = {
            "status": "runtime_error",
            "error": _safe_error(f"{type(exc).__name__}: {exc}"),
            "hint": "Consultez aussi /__diag et les Runtime Logs Vercel.",
        }
        advice = network_hint(exc)
        if advice:
            body["network_hint"] = advice
            body["diagnosis"] = "ipv6_egress_unavailable"
        if os.environ.get("JAAY_DEBUG_ERRORS") == "1":
            body["traceback"] = _safe_error(traceback.format_exc())
        return JSONResponse(body, status_code=500)


__all__ = ["app"]
