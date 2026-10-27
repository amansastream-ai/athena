#!/usr/bin/env bash
# Demarrage resilient de l'apercu Jaay.
# Les paquets pip ne sont pas persistes dans le snapshot du sandbox : on les
# reinstalle uniquement s'ils manquent, puis on verifie le schema et on lance.
set -euo pipefail
cd "$(dirname "$0")/.."

export JAAY_ENV="${JAAY_ENV:-dev}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-0}"

if ! python3 -c "import fastapi, uvicorn, sqlalchemy, alembic, jinja2, multipart, PIL, pydantic" 2>/dev/null; then
  echo "[start] dependances Python absentes -> pip install -r requirements.txt"
  python3 -m pip install --disable-pip-version-check -q -r requirements.txt
fi

# create_all + triggers sont idempotents : ils ne detruient aucune donnee.
# En production, c'est ops/entrypoint.sh (alembic upgrade head) qui fait autorite.
python3 -c "from app.seed import init_db; init_db(); print('[start] schema pret')"

ARGS=(uvicorn app.main:app --host 0.0.0.0 --port "$PORT")
if [[ "$RELOAD" == "1" ]]; then ARGS+=(--reload); fi
echo "[start] http://0.0.0.0:$PORT  (env=$JAAY_ENV, reload=$RELOAD)"
exec "${ARGS[@]}"
