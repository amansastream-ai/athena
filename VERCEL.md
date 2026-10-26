# Jaay sur Vercel — checklist de mise en ligne

**État :** tout le code et toute la configuration sont prêts dans ce dépôt. Il reste **2 comptes à créer** et **8 variables à renseigner**. Guide détaillé : [`docs/13-deploiement-vercel.md`](docs/13-deploiement-vercel.md). En cas d'erreur 500 après déploiement : [`DIAGNOSTIC-BUILD-VERCEL.md`](DIAGNOSTIC-BUILD-VERCEL.md).

⚠️ **Avant de pousser le dépôt sur GitHub**, lisez [`AVANT-GITHUB.md`](AVANT-GITHUB.md) : aucun fichier `.env` ne doit être commité. Lancez `python3 -m scripts.check_before_push`.

---

## ✅ Ce qui est déjà préparé

| Élément | Fichier |
|---|---|
| Entrée ASGI reconnue par Vercel | `index.py` |
| Configuration fonction (FastAPI, 30 s, 1 Go, exclusions) | `vercel.json` |
| Version Python 3.13 | `.python-version` |
| Exclusion des fichiers de dev du bundle | `.vercelignore` |
| Liste exacte des variables | `.env.vercel.example` |
| Vérification avant déploiement | `scripts/vercel_preflight.py` |
| Création du schéma + comptes démo | `scripts/vercel_bootstrap.sh` |
| Refus de SQLite sur Vercel, normalisation `postgres://` | `app/config.py` |
| Compatibilité filesystem lecture seule (`/tmp`, `check_dir=False`) | `app/config.py`, `app/main.py` |
| Photos produits sur S3 / Cloudflare R2 | `app/services/media.py` |
| Connexion qui marche en iframe (CSRF signé + cookies CHIPS) | `app/security.py` |

**76 tests automatisés passent**, dont 17 nouveaux tests dédiés à Vercel, à la connexion sans cookies et au contrôle anti-fuite de secrets.

---

## 🔧 Ce qu'il vous reste à faire (15–20 min)

### 1. Créer une base PostgreSQL gratuite
Neon.tech ou Supabase → copier l'URI de connexion. Ajouter `?sslmode=require`.
⚠️ **SQLite est refusé sur Vercel** : le disque serverless est éphémère.

### 2. Générer la clé secrète
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Renseigner ces variables dans Vercel → Settings → Environment Variables

```
JAAY_ENV=production
JAAY_SECRET_KEY=<clé de l'étape 2>
JAAY_DATABASE_URL=postgresql+psycopg2://user:pwd@host/db?sslmode=require
JAAY_PUBLIC_BASE_URL=https://VOTRE-PROJET.vercel.app
JAAY_COOKIE_SECURE=1
JAAY_DEV_SHOW_OTP=0
JAAY_DEMO_MODE=1
JAAY_AUTO_INIT_DB=1
```

Optionnel mais nécessaire pour les photos : `JAAY_MEDIA_BACKEND=s3` + `JAAY_S3_*` (voir `.env.vercel.example`).

### 4. Initialiser la base depuis votre machine
```bash
cd /home/user/jaay
export JAAY_ENV=production JAAY_SECRET_KEY='<clé>'
export JAAY_DATABASE_URL='postgresql+psycopg2://...'
export JAAY_DEMO_MODE=1
bash scripts/vercel_bootstrap.sh
python3 -m scripts.vercel_preflight
```
Le preflight doit afficher **« Pret a deployer »**.

### 5. Déployer
Poussez le dépôt sur GitHub → [vercel.com/new](https://vercel.com/new) → **Import Git Repository**. Vercel détecte FastAPI automatiquement. Ou en CLI :
```bash
npm i -g vercel && vercel --prod
```

### 6. Vérifier
```bash
curl https://VOTRE-PROJET.vercel.app/health
python3 -m scripts.vercel_diagnose https://VOTRE-PROJET.vercel.app
./scripts/smoke.sh https://VOTRE-PROJET.vercel.app
```
⚠️ Un **build réussi ne veut pas dire que le site fonctionne** : si vous voyez une erreur 500, le script de diagnostic vous dira quelle variable manque. Consultez aussi `vercel logs <projet> --follow`.
Puis dans le navigateur : `/connexion` → `admin@jaay.local / AdminJaay2026!` → `/admin` doit s'afficher.

---

## 🔐 Le bug de connexion est corrigé

Le message « accès refusé » venait du jeton CSRF lié à un cookie `SameSite=Lax`, non transmis lors d'un POST depuis l'iframe de prévisualisation. Désormais :

- **jeton CSRF signé HMAC**, vérifiable par le serveur **sans aucun cookie** ;
- cookies `SameSite=None; Secure; Partitioned` (CHIPS) en HTTPS ;
- message d'erreur clair (« votre session de sécurité a expiré ») au lieu d'un code brut.

Vérifié : connexion **sans cookies** → `303 /admin`, `/vendeur` → 200, 76/76 tests OK. L'aperçu local sur le port 8000 a été redémarré avec ces correctifs.

---

## ⚠️ Avant un usage réel

1. `JAAY_DEMO_MODE=0` et **supprimer / changer les mots de passe des comptes démo** (ils sont affichés publiquement sur `/connexion` quand le mode démo est actif).
2. `JAAY_AUTO_INIT_DB=0` et passer aux migrations Alembic.
3. Configurer un fournisseur SMS pour l'OTP, S3 pour les médias, des sauvegardes PostgreSQL et la 2FA admin.
4. **Aucun paiement réel n'est actif** : Wave, Orange Money et WhatsApp Cloud API nécessitent vos contrats marchands et des tests sandbox (voir `docs/05`).
5. Vérifier le plan Vercel (Hobby = usage non commercial) et la conformité juridique/CDP.
