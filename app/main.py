"""Compatibilite ASGI : `uvicorn app.main:app` (local/Docker) et Vercel.

L'objet applicatif unique est cree dans ``index.py`` a la racine, car Vercel
exige un ``app = FastAPI(...)`` literal dans son point d'entree. Ce module le
re-exporte simplement : il n'y a donc qu'UNE SEULE instance FastAPI en service.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from index import app  # noqa: E402,F401
except Exception as exc:  # pragma: no cover - fastapi manquant uniquement
    from app.diagnostic import build_diagnostic_app  # noqa: E402

    app = build_diagnostic_app(exc)

__all__ = ["app"]
