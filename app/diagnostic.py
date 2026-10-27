"""Application de secours quand la configuration d'un deploiement est invalide.

Sur Vercel, une variable d'environnement absente fait planter l'import de
`app.config`, ce qui produit un `500 FUNCTION_INVOCATION_FAILED` totalement
opaque. Ce module fabrique alors une mini-application qui repond 503 avec la
cause exacte et la liste des variables manquantes — sans jamais exposer leur
valeur.
"""
from __future__ import annotations

import os
import re
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .db_network import POOLER_HINT, is_direct_supabase, network_hint

REQUIRED = {
    "JAAY_SECRET_KEY": "cle de signature (32+ caracteres)",
    "JAAY_DATABASE_URL": "URI PostgreSQL (Neon / Supabase)",
    "JAAY_ENV": "production",
    "JAAY_PUBLIC_BASE_URL": "https://votre-projet.vercel.app",
}

FIX_STEPS = [
    "Vercel > Settings > Environment Variables : renseignez au minimum JAAY_SECRET_KEY, JAAY_DATABASE_URL, JAAY_ENV et JAAY_PUBLIC_BASE_URL (cochez Production).",
    "JAAY_SECRET_KEY : python3 -c \"import secrets; print(secrets.token_hex(32))\" — utilisez une valeur FIXE, ne la regenerez pas a chaque deploiement.",
    "JAAY_DATABASE_URL : postgresql+psycopg2://user:password@host/db?sslmode=require",
    "SUPABASE : sur Vercel, n'utilisez PAS db.<ref>.supabase.co:5432 (IPv6 seul). " + POOLER_HINT,
    "JAAY_DEMO_MODE=1 si vous voulez les comptes de demonstration, sinon 0.",
    "REDEPLOYEZ ensuite (vercel --prod) : Vercel n'injecte les variables qu'au moment du deploiement.",
    "Logs d'execution : vercel logs <projet> --follow",
]


def _redact(message: str) -> str:
    """Retire le mot de passe d'une eventuelle URL presente dans le message."""
    url = os.environ.get("JAAY_DATABASE_URL", "")
    if url and url in message:
        message = message.replace(url, re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", url))
    return re.sub(r"//([^:/@\s]+):[^@\s]*@", r"//\1:***@", message)


def environment_report() -> dict[str, Any]:
    present = {key: bool(os.environ.get(key)) for key in REQUIRED}
    return {
        "variables_present": present,
        "missing_variables": [key for key, ok in present.items() if not ok],
        "runtime": "vercel" if os.environ.get("VERCEL") else "local",
        "env": os.environ.get("JAAY_ENV", "(non defini)"),
        "database_is_postgres": os.environ.get("JAAY_DATABASE_URL", "").startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg2://")
        ),
    }


def build_diagnostic_app(exc: Exception) -> FastAPI:
    """Cree une application qui explique pourquoi Jaay n'a pas demarre."""
    app = FastAPI(
        title="Jaay — configuration a corriger",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    return configure_diagnostic(app, exc)


def configure_diagnostic(app: FastAPI, exc: Exception) -> FastAPI:
    """Transforme une app deja creee en reponse de diagnostic (503 explicite).

    Utilise par ``index.py`` : Vercel exige un ``app = FastAPI(...)`` litteral
    au niveau du module, donc on ne peut pas remplacer l'objet. On remplace
    ses routes et son titre a la place.
    """
    report = environment_report()
    reason = _redact(f"{type(exc).__name__}: {exc}")
    hint = network_hint(exc)
    if hint:
        report["network_hint"] = hint
    if is_direct_supabase(os.environ.get("JAAY_DATABASE_URL", "")):
        report["database_mode"] = "supabase-direct-ipv6-only"
    app.title = "Jaay — configuration a corriger"
    app.version = "0.1.0"
    app.docs_url = None
    app.redoc_url = None
    app.openapi_url = None
    # configure_app() a pu enregistrer une partie des routes avant l'echec.
    app.router.routes.clear()

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def _explain(path: str = "") -> JSONResponse:
        return JSONResponse(
            {
                "status": "misconfigured",
                "message": (
                    "Jaay n'a pas pu demarrer. Ce n'est pas une erreur de build : "
                    "la configuration d'environnement est absente ou invalide, "
                    "et elle est lue des l'import de l'application."
                ),
                "error": reason,
                **report,
                "how_to_fix": FIX_STEPS,
                "diagnostic_command": "python3 -m scripts.vercel_diagnose <url>",
            },
            status_code=503,
        )

    return app
