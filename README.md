# Jaay — marketplace multi-vendeurs WhatsApp-first (Sénégal)

> Chaque vendeur crée gratuitement sa boutique, partage un lien, reçoit des commandes et des demandes WhatsApp. La plateforme prélève **10 % par défaut** (taux configurable) **uniquement sur les ventes dont le paiement a été vérifié**, avec un journal financier immuable.

## ⚠️ Lire avant toute démonstration

Ce dépôt contient une **application réelle et testée** : 76 tests automatisés passent, les pages publiques, les tableaux de bord vendeur/admin et l'API JSON fonctionnent en local.

En revanche, **aucune intégration de paiement ou de messagerie externe n'est active** : il n'existe pas de compte marchand Wave, ni de contrat Orange Money, ni de compte WhatsApp Cloud API, ni de fournisseur SMS. Le parcours de paiement opérationnel est donc **référence + preuve + validation administrateur**. Les adaptateurs Wave/Orange Money/Cloud API sont écrits et testés avec des transports simulés, mais **désactivés**. Voir [`docs/11-etat-des-lieux.md`](docs/11-etat-des-lieux.md) et [`docs/05-paiements-wave-orange-money.md`](docs/05-paiements-wave-orange-money.md).

## Démarrage rapide

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Renseigner JAAY_SECRET_KEY (openssl rand -hex 32)
python3 -m alembic upgrade head
python3 -m scripts.seed --demo
python3 -m pytest -q                  # 76 tests
make dev                              # http://localhost:8000
```

Comptes démo : `admin@jaay.local / AdminJaay2026!` · `fatou@jaay.local / VendeurJaay2026!` · `client@jaay.local / ClientJaay2026!`

### Reprendre l'aperçu après un redémarrage du sandbox

Les paquets pip ne font pas partie du snapshot : après un redémarrage, une seule commande suffit — elle réinstalle les dépendances manquantes, vérifie le schéma sans écraser les données et relance sur le port 8000.

```bash
cd /home/user/jaay && bash scripts/start.sh        # ou : make start
```

Vérifications : `/health`, `/api/docs`, `/admin`, `/vendeur`, `/catalogue`, `/pages/commission`.


## 🔒 Avant de pousser sur GitHub

**Ne mettez jamais `.env` sur GitHub.** Seuls `.env.example` et `.env.vercel.example` (sans secrets) doivent être versionnés. Lisez [`AVANT-GITHUB.md`](AVANT-GITHUB.md) puis lancez `python3 -m scripts.check_before_push`.

## 🚀 Déployer sur Vercel (préparé, en attente de vos accès)

Tout est prêt dans le dépôt : `index.py` (entrée ASGI), `vercel.json`, `.python-version`, `.vercelignore`, `.env.vercel.example`, `scripts/vercel_preflight.py`, `scripts/vercel_bootstrap.sh` et le guide [`docs/13-deploiement-vercel.md`](docs/13-deploiement-vercel.md).

Il vous manque uniquement **un compte Vercel** et **une base PostgreSQL externe** (Neon/Supabase). SQLite est refusé sur Vercel : le disque serverless est éphémère.

```bash
# 1. Variables (voir .env.vercel.example pour la liste complète)
export JAAY_ENV=production JAAY_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export JAAY_DATABASE_URL='postgresql+psycopg2://user:pwd@host/db?sslmode=require'
export JAAY_PUBLIC_BASE_URL='https://VOTRE-PROJET.vercel.app'
export JAAY_COOKIE_SECURE=1 JAAY_DEV_SHOW_OTP=0 JAAY_DEMO_MODE=1 JAAY_AUTO_INIT_DB=1

# 2. Créer le schéma et les comptes démo dans la base distante
bash scripts/vercel_bootstrap.sh

# 3. Contrôle avant déploiement
python3 -m scripts.vercel_preflight

# 4. Renseigner les mêmes variables dans Vercel > Settings > Environment Variables,
#    puis importer le dépôt GitHub sur vercel.com/new (ou `vercel --prod`)
```

⚠️ `JAAY_DEMO_MODE=1` publie des identifiants de démonstration : à **désactiver** et à remplacer par de vrais comptes avant tout usage commercial. Les intégrations Wave / Orange Money / WhatsApp Cloud API restent désactivées.

## 🔐 Connexion depuis un aperçu intégré (iframe)

Le problème « accès refusé » venait du jeton CSRF qui dépendait d'un cookie `SameSite=Lax`, non envoyé lors d'un POST en iframe inter-sites. Correctifs apportés : **jeton CSRF signé HMAC** (vérifiable sans cookie) et cookies `SameSite=None; Secure; Partitioned` (CHIPS) en HTTPS. La connexion fonctionne désormais même si le navigateur bloque les cookies tiers.

## Ce que contient le dépôt

```
app/            Application FastAPI : modèles, services métier, API JSON, vues HTML
app/services/   orders · ledger · payments · fraud · whatsapp · ai · media · sms · stats · audit
app/templates/  Pages publiques, vendeur, admin (Jinja2, aucun CDN)
app/static/     CSS + JS locaux (mobile-first)
migrations/     Alembic — migration initiale + triggers d'immutabilité
tests/          76 tests Pytest (commissions, paiements, WhatsApp, sécurité, E2E)
scripts/        seed, sauvegarde, restauration, intégrité, smoke tests
ops/            Dockerfile, entrypoint, nginx.conf
docs/           13 documents de conception, déploiement et suivi
```

## Règles métier essentielles

- Calcul en entiers XOF : `commission = (assiette × rate_bps + 5000) // 10000`. 1 000 F → 100 F ; 10 000 F → 1 000 F.
- Aucune commission avant paiement vérifié (webhook signé re-vérifié **ou** preuve validée par un administrateur).
- `wa.me` trace le clic et injecte un code de demande ; Jaay ne lit **jamais** les conversations privées.
- Journal financier append-only chaîné par SHA-256 ; annulation/remboursement = écriture compensatoire.
- Isolation stricte entre boutiques, CSRF sur toutes les mutations, secrets uniquement dans l'environnement.

## Documentation

| Document | Contenu |
|---|---|
| [`docs/00-reunion-conception.md`](docs/00-reunion-conception.md) | Réunion des 12 agents, arbitrages, risques financiers, dépendances |
| [`docs/01-cahier-des-charges.md`](docs/01-cahier-des-charges.md) | Parcours, fonctionnalités, critères d'acceptation |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Choix techniques justifiés, ADR, montée en charge |
| [`docs/03-modele-de-donnees.md`](docs/03-modele-de-donnees.md) | Schéma, tables, index, contraintes |
| [`docs/04-commissions.md`](docs/04-commissions.md) | Fonctionnement exact des 10 % et du journal |
| [`docs/05-paiements-wave-orange-money.md`](docs/05-paiements-wave-orange-money.md) | Étude documentée, checklist d'activation |
| [`docs/06-whatsapp.md`](docs/06-whatsapp.md) | Stratégie de vérification des ventes WhatsApp |
| [`docs/07-securite.md`](docs/07-securite.md) | Protections et durcissement |
| [`docs/08-installation-deploiement.md`](docs/08-installation-deploiement.md) | Local, Docker, migrations, sauvegardes |
| [`docs/09-tests-rapport.md`](docs/09-tests-rapport.md) | Résultats, couverture, points non vérifiés |
| [`docs/10-plan-de-lancement.md`](docs/10-plan-de-lancement.md) | Plan de lancement et croissance |
| [`docs/11-etat-des-lieux.md`](docs/11-etat-des-lieux.md) | État précis par fonctionnalité |
| [`docs/12-api.md`](docs/12-api.md) | Référence API et webhooks |
| [`docs/13-deploiement-vercel.md`](docs/13-deploiement-vercel.md) | Déploiement Vercel pas à pas |

## Licence

Projet livré comme base de travail propriétaire. Ajouter une licence explicite et faire valider les CGU avant exploitation commerciale.
