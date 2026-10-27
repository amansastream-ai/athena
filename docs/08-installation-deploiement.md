# Installation locale et déploiement

## 1. Prérequis

| Outil | Version vérifiée | Utilité |
|---|---|---|
| Python | 3.13.16 | Application |
| pip | fourni avec Python | Dépendances |
| Node.js | v20.20.2 (optionnel) | Non requis par l'application (aucun build front) |
| Docker + compose | optionnels | Déploiement conteneurisé |
| PostgreSQL | 16 recommandé | Production (SQLite suffit en dev) |

Versions logicielles vérifiées dans cet environnement : FastAPI 0.162.0, SQLAlchemy 2.1.6, Alembic 1.20.1, Pydantic 2.x, pytest 9.x.

## 2. Démarrage local (5 minutes)

```bash
cd jaay
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Générer une clé : python3 -c "import secrets; print(secrets.token_hex(32))"
# Coller la valeur dans .env -> JAAY_SECRET_KEY
python3 -m alembic upgrade head       # schéma réel versionné
python3 -m scripts.seed --demo        # comptes et produits de démonstration
make dev                              # uvicorn --reload sur 0.0.0.0:8000
```

Ouvrir `http://localhost:8000`. Vérifier `http://localhost:8000/health` et `http://localhost:8000/api/docs`.

### Comptes de démonstration (dev uniquement)

| Rôle | Identifiant | Mot de passe |
|---|---|---|
| Administrateur | `admin@jaay.local` | `AdminJaay2026!` |
| Vendeur | `fatou@jaay.local` | `VendeurJaay2026!` |
| Client | `client@jaay.local` | `ClientJaay2026!` |

La commande de démonstration passe par une preuve puis une validation administrateur : elle illustre 10 000 F → 1 000 F de commission.

## 3. Tests

```bash
python3 -m pytest -q              # 59 tests
./scripts/smoke.sh http://localhost:8000
python3 -m scripts.verify_integrity
```

Les tests utilisent une base SQLite isolée (`var/test_jaay.db`) et réinitialisent le schéma entre chaque test. Aucun accès réseau n'est requis.

## 4. Déploiement conteneurisé

```bash
cp .env.example .env
# Renseigner JAAY_SECRET_KEY, POSTGRES_PASSWORD, JAAY_PUBLIC_BASE_URL, JAAY_ENV=production,
# JAAY_DATABASE_URL, JAAY_COOKIE_SECURE=1, JAAY_DEV_SHOW_OTP=0
docker compose up --build -d
docker compose logs -f app
curl -s http://localhost:8000/health
docker compose --profile proxy up -d   # nginx devant l'app (à compléter avec TLS)
```

`ops/entrypoint.sh` exécute dans l'ordre : `alembic upgrade head` → amorçage admin optionnel → `verify_integrity` → `uvicorn`. Les migrations sont la source de vérité du schéma en production.

### Sans Docker (VPS systemd)

```bash
pip install -r requirements.txt
export JAAY_ENV=production JAAY_SECRET_KEY=... JAAY_DATABASE_URL=postgresql+psycopg2://...
python -m alembic upgrade head
python -m scripts.seed --admin       # si JAAY_BOOTSTRAP_ADMIN_* définis
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Placer ensuite nginx/apache en reverse proxy avec TLS (Let's Encrypt). Voir `ops/nginx.conf`.

## 5. Trois environnements

| Env | Base | Particularités |
|---|---|---|
| `dev` | SQLite | Données de démo, OTP affiché en console, debug contrôlé, CSP tolérante. |
| `staging` | PostgreSQL | Copie anonymisée de prod, sandbox Wave/OM, webhooks de test, secrets distincts. |
| `production` | PostgreSQL | HTTPS, sauvegardes, SMS réel, monitoring, aucune démo, `JAAY_DEV_SHOW_OTP=0`. |

Créer un fichier `.env` par environnement et ne **jamais** le committer. Les secrets sont lus uniquement depuis l'environnement (`app/config.py`).

## 6. Mises à jour sans perte

1. Sauvegarder : `./scripts/backup.sh`.
2. Déployer le nouveau code (git pull / nouvelle image).
3. `python -m alembic upgrade head`.
4. `./scripts/smoke.sh https://exemple.sn`.
5. `python -m scripts.verify_integrity`.
6. Vérifier `/admin`, `/admin/journal`, une commande de test.
7. En cas d'échec : restaurer la sauvegarde sur une copie, rejouer les migrations, rebasculer le trafic.

## 7. Sauvegarde et restauration

- `./scripts/backup.sh` : `pg_dump | gzip` (ou sauvegarde SQLite en dev) + archive des médias.
- `./scripts/restore.sh <fichier>` : restaure, rejoue les migrations, vérifie l'intégrité.
- Fréquence recommandée : quotidienne pour la base, hebdomadaire pour les médias, conservation 30 jours, copie hors site chiffrée.
- **Test mensuel de restauration sur une copie** : une sauvegarde non restaurée n'est pas une sauvegarde.

⚠️ `pg_dump`, `psql`, `sqlite3` CLI ne sont pas installés dans l'environnement de développement fourni : la procédure est documentée pour le serveur de production. En local, copier `var/jaay.db` suffit.

## 8. Surveillance et exploitation

- Sonde `/health` toutes les 60 s ; alerte si `X-Request-Id` manque ou si `/health` renvoie autre chose que 200.
- Alertes métier : preuves en attente > 24 h, commissions en attente anormales, pic de commandes à risque, `verify_chain()` en échec.
- À installer : Sentry (erreurs), uptime externe, métriques Prometheus/Grafana, rotation des logs, alertes SMS/email admin.
- Runbook incident paiement : passer le fournisseur en `off`, basculer sur le parcours manuel, vérifier `/admin/journal`, rapprocher les transactions avec le relevé fournisseur, corriger par écriture compensatoire **uniquement** (jamais par modification de ligne), notifier les vendeurs.

## 9. Variables d'environnement

Voir `.env.example` (commenté, sans secrets). Variables sensibles : `JAAY_SECRET_KEY`, `WAVE_API_KEY`, `WAVE_WEBHOOK_SECRET`, `WAVE_SIGNING_SECRET`, `OM_*`, `WA_CLOUD_TOKEN`, `WA_CLOUD_APP_SECRET`, `SMS_API_KEY`, `SMTP_PASSWORD`, `AI_API_KEY`, `POSTGRES_PASSWORD`. Aucune ne doit apparaître dans un dépôt, un log ou une page HTML.
