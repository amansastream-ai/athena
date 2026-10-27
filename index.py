"""Point d'entree ASGI pour Vercel Functions.

Vercel analyse ce fichier STATIQUEMENT : il exige un appel literal
``app = FastAPI(...)`` au niveau du module (erreur sinon :
FASTAPI_ENTRYPOINT_NOT_FOUND). On cree donc l'objet ici, puis on le configure
avec ``configure_app()``. Voir
https://vercel.com/docs/frameworks/backend/fastapi#exporting-the-fastapi-application

Une sonde ``/__diag`` (stdlib uniquement) est enregistree EN PREMIERE position :
elle repond MEME quand l'import de l'application a echoue, et indique l'erreur
Python exacte ainsi que la presence des variables (jamais leur valeur).
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

# NE PAS remplacer cette ligne par un import : le builder Vercel la cherche.
app = FastAPI(
    title="Jaay — Marketplace multi-vendeurs",
    version="0.1.0",
    description="Boutiques gratuites, WhatsApp-first, commission tracable. Voir /api/docs.",
    docs_url="/api/docs",
    redoc_url=None,
)


def _safe_error(text: str) -> str:
    """Retire les eventuels mots de passe d'URI et limite la taille."""
    url = os.environ.get("JAAY_DATABASE_URL", "")
    if url and url in text:
        text = text.replace(url, re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", url))
    return re.sub(r"//([^:/@\s]+):[^@\s]*@", r"//\1:***@", text)[:1500]


WATCHED_KEYS = (
    "JAAY_ENV", "JAAY_SECRET_KEY", "JAAY_DATABASE_URL", "JAAY_PUBLIC_BASE_URL",
    "JAAY_COOKIE_SECURE", "JAAY_DEV_SHOW_OTP", "JAAY_DEMO_MODE",
    "JAAY_AUTO_INIT_DB", "VERCEL",
)

try:
    from app.application import configure_app, lifespan  # noqa: E402

    # Le lifespan ne peut pas etre passe au constructeur ci-dessus sans importer
    # la configuration trop tot : on l'attache au routeur juste apres.
    app.router.lifespan_context = lifespan
    configure_app(app)
    _config_error = None
except Exception as exc:  # pragma: no cover - configuration invalide uniquement
    _config_error = f"{type(exc).__name__}: {_safe_error(str(exc))}"
    try:
        from app.diagnostic import configure_diagnostic  # noqa: E402

        configure_diagnostic(app, exc)
    except Exception as diag_exc:  # dernier filet de securite
        _config_error += f" | diagnostic indisponible: {type(diag_exc).__name__}"


async def _diag(request: Request):  # noqa: ARG001
    """Sonde : ne depend d'aucun import metier, donc repond meme en panne."""
    import importlib
    import traceback

    presence = {key: bool(os.environ.get(key)) for key in WATCHED_KEYS}
    out = {
        "status": "diagnostic",
        "python": sys.version.split()[0],
        "app_title": app.title,
        "routes_count": len(app.router.routes),
        "config_error_at_import": _config_error,
        "env_presence": presence,
        "database_is_postgres": os.environ.get("JAAY_DATABASE_URL", "").startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg2://")
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
        if os.environ.get("JAAY_DEBUG_ERRORS") == "1":
            body["traceback"] = _safe_error(traceback.format_exc())
        return JSONResponse(body, status_code=500)


__all__ = ["app"]
