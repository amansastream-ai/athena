#!/usr/bin/env bash
# Sauvegarde PostgreSQL (production) ou SQLite (developpement) + медиas.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date -u +%Y%m%d-%H%M%S)
DB_URL="${JAAY_DATABASE_URL:-sqlite:///var/jaay.db}"

if [[ "$DB_URL" == postgresql* ]]; then
  # pg_dump utilise les variables PG* ou l'URL fournie.
  echo "[backup] pg_dump -> backups/jaay-$STAMP.sql.gz" 2>/dev/null || true
  pg_dump "$DB_URL" | gzip > "backups/jaay-$STAMP.sql.gz"
else
  DB_PATH="${DB_URL#sqlite:///}"
  echo "[backup] SQLite -> backups/jaay-$STAMP.db"
  sqlite3 "$DB_PATH" ".backup 'backups/jaay-$STAMP.db'"
fi

tar -czf "backups/jaay-media-$STAMP.tar.gz" media 2>/dev/null || true
echo "[backup] termine : $(ls -1 backups | tail -3 | tr '\n' ' ')"
echo "[backup] RAPPEL : tester la restauration regulierement (voir docs/08)."
