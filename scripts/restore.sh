#!/usr/bin/env bash
# Restauration : NE JAMAIS tester directement en production. Utiliser une copie.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-}"
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "Usage : ./scripts/restore.sh backups/jaay-YYYYMMDD-HHMMSS.sql.gz|backups/jaay-YYYYMMDD-HHMMSS.db"
  exit 1
fi
DB_URL="${JAAY_DATABASE_URL:-sqlite:///var/jaay.db}"

echo "[restore] ARRET de l'application avant restauration (docker compose stop app)."
if [[ "$FILE" == *.sql.gz ]]; then
  if [[ "$DB_URL" != postgresql* ]]; then echo "Fichier SQL PostgreSQL mais base non PostgreSQL."; exit 1; fi
  gunzip -c "$FILE" | psql "$DB_URL"
elif [[ "$FILE" == *.db ]]; then
  DB_PATH="${DB_URL#sqlite:///}"
  cp "$DB_PATH" "$DB_PATH.before-restore" 2>/dev/null || true
  cp "$FILE" "$DB_PATH"
else
  echo "Format inconnu."; exit 1
fi
python -m alembic upgrade head
python -m scripts.verify_integrity
echo "[restore] termine. Verifier /health, /admin/journal et un parcours de commande."
