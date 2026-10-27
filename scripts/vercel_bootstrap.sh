#!/usr/bin/env bash
# Initialise la base PostgreSQL distante depuis une machine locale.
# A lancer UNE FOIS avant le premier deploiement (ou apres la creation de la DB).
#
#   export JAAY_DATABASE_URL='postgresql+psycopg2://user:pwd@host/db?sslmode=require'
#   export JAAY_SECRET_KEY='...'
#   export JAAY_ENV=production
#   export JAAY_DEMO_MODE=1          # uniquement pour une demo publique
#   bash scripts/vercel_bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

: "${JAAY_DATABASE_URL:?Definissez JAAY_DATABASE_URL vers votre base PostgreSQL}"

echo "[bootstrap] schema + categories + parametres par defaut"
python3 -c "from app.seed import init_db; init_db(); print('[bootstrap] schema pret')"

if [[ "${JAAY_DEMO_MODE:-0}" == "1" ]]; then
  echo "[bootstrap] comptes et produits de demonstration"
  JAAY_ENV=production python3 -m scripts.seed --demo --force
else
  echo "[bootstrap] compte administrateur initial (JAAY_BOOTSTRAP_ADMIN_*)"
  python3 -m scripts.seed --admin
fi

echo "[bootstrap] verification des journaux"
python3 -m scripts.verify_integrity

echo "[bootstrap] termine. Vous pouvez deployer : vercel --prod"
