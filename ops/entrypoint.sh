#!/bin/sh
set -eu
# 1. Migrations (source de vérité du schéma en production).
python -m alembic upgrade head
# 2. Amorçage optionnel du premier administrateur (si variables définies).
if [ -n "${JAAY_BOOTSTRAP_ADMIN_EMAIL:-}" ] && [ -n "${JAAY_BOOTSTRAP_ADMIN_PASSWORD:-}" ]; then
  python -m scripts.seed --admin || echo "[entrypoint] bootstrap admin deja effectue ou ignore"
fi
# 3. Vérification de l'intégrité des journaux au démarrage.
python -m scripts.verify_integrity || echo "[entrypoint] integrite non verifiee"
exec "$@"
