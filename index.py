"""Point d'entree ASGI pour Vercel Functions.

Vercel analyse ce fichier STATIQUEMENT : il exige un appel literal
``app = FastAPI(...)`` au niveau du module (erreur sinon :
FASTAPI_ENTRYPOINT_NOT_FOUND). On cree donc l'objet ici, puis on le configure
avec ``configure_app()``. Voir
https://vercel.com/docs/frameworks/backend/fastapi#exporting-the-fastapi-application

Si une variable d'environnement obligatoire manque, on transforme cette meme
instance en application de diagnostic (503 explicite) plutot que de laisser un
500 opaque — sans jamais remplacer l'objet ``app``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402

# NE PAS remplacer cette ligne par un import : le builder Vercel la cherche.
app = FastAPI(
    title="Jaay — Marketplace multi-vendeurs",
    version="0.1.0",
    description="Boutiques gratuites, WhatsApp-first, commission tracable. Voir /api/docs.",
    docs_url="/api/docs",
    redoc_url=None,
)

try:
    from app.application import configure_app, lifespan  # noqa: E402

    # Le lifespan ne peut pas etre passe au constructeur ci-dessus sans importer
    # la configuration trop tot : on l'attache au routeur juste apres.
    app.router.lifespan_context = lifespan
    configure_app(app)
except Exception as exc:  # pragma: no cover - configuration invalide uniquement
    from app.diagnostic import configure_diagnostic  # noqa: E402

    configure_diagnostic(app, exc)

__all__ = ["app"]
