# 📦 Dossier prêt pour GitHub — lisez ceci en premier

Ce dossier contient **Jaay**, l'application complète, sans aucun secret, prête à être poussée sur GitHub puis déployée sur Vercel.

> ⚠️ **Important : ce dossier ne contient volontairement AUCUN fichier `.env` avec de vraies valeurs.** Les variables doivent être saisies dans le dashboard Vercel. Sans elles, le site renverra `500 FUNCTION_INVOCATION_FAILED`. Voir étape 3 ci-dessous.

---

## 1. Mettre le dossier sur GitHub

> ⚠️ **Copiez le CONTENU du dossier `jaay/`, pas le dossier lui-même.** `vercel.json`, `index.py` et `requirements.txt` doivent se trouver **à la racine** de votre dépôt. Si vous gardez le sous-dossier `jaay/`, réglez alors **Root Directory = `jaay`** dans Vercel → Settings → General.
>
> Pensez aux **fichiers cachés** (`.gitignore`, `.vercelignore`, `.python-version`, `.github/`) : avec l'Explorateur Windows, cochez « Afficher les éléments masqués » ; avec `cp`, utilisez `cp -a dossier/. destination/`.

```bash
# Si vous avez deja un depot (recommande : remplacer son contenu)
cd /chemin/vers/votre-repo
# copier TOUT le contenu de ce dossier ici (fichiers caches inclus)
git add -A
git status --short          # verifier : aucun .env, aucun *.db
git commit -m "Jaay MVP — correction entree ASGI Vercel + diagnostic"
git push

# Ou creer un nouveau depot
cd ce-dossier
git init
git add .
python3 -m scripts.check_before_push
git commit -m "Jaay MVP 0.1"
git branch -M main
git remote add origin https://github.com/VOTRE_COMPTE/jaay.git
git push -u origin main
```

Le contrôle `check_before_push` doit afficher **« OK — dépôt publiable »**.

💡 Une fois poussé, GitHub lancera automatiquement `.github/workflows/tests.yml` (76 tests + contrôle anti-fuite).

---

## 2. Ce que corrige cette version par rapport à votre déploiement actuel

| Problème constaté sur `athenamarketplace.vercel.app` | Correction incluse |
|---|---|
| **`FASTAPI_ENTRYPOINT_NOT_FOUND`** au build : « index.py ne définit pas d'instance FastAPI app de niveau supérieur » | `index.py` contient maintenant un **`app = FastAPI(...)` littéral** au niveau du module (Vercel analyse par AST), puis `configure_app(app)` ajoute les routes |
| `500 FUNCTION_INVOCATION_FAILED` sur **toutes** les routes, y compris `/static/app.css` | Entrée ASGI à imports absolus + une seule instance partagée entre `index.py`, `app/main.py` et les tests |
| 500 totalement opaque | `app/diagnostic.py` renvoie un **503 JSON qui nomme la variable manquante** |
| Variables absentes | `scripts/vercel_env_block.py` génère le bloc exact à coller dans Vercel |
| Avertissement `memory ... ignored` dans le log de build | `memory` retiré de `vercel.json` |
| Fichiers `.git/hooks/*.sample` listés au build | `.git/**` ajouté à `.vercelignore` |

---

## 3. ⚠️ Étape indispensable : les variables dans Vercel

**Le code seul ne suffit pas.** Dans **Vercel → votre projet → Settings → Environment Variables**, ajoutez au minimum (cochez **Production**) :

```dotenv
JAAY_ENV=production
JAAY_SECRET_KEY=<64 caracteres aleatoires>
JAAY_DATABASE_URL=postgresql+psycopg2://user:password@host/db?sslmode=require
JAAY_PUBLIC_BASE_URL=https://athenamarketplace.vercel.app
JAAY_COOKIE_SECURE=1
JAAY_DEV_SHOW_OTP=0
JAAY_DEMO_MODE=1
JAAY_AUTO_INIT_DB=1
```

Pour générer le bloc complet à partir d'un `.env` local :

```bash
cp .env.example .env           # puis remplir .env (jamais commité)
python3 -m scripts.vercel_env_block --domain https://athenamarketplace.vercel.app
```

Il vous faut aussi une **base PostgreSQL** (Neon.tech ou Supabase, connexion directe, pas le pooler transactionnel). SQLite est refusé sur Vercel.

🔴 Choisissez une `JAAY_SECRET_KEY` **fixe** : la régénérer invalide les sessions et ramène « accès refusé ».

Les changements de variables ne prennent effet que sur le **prochain déploiement** [6](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables) : après le push, attendez le déploiement automatique ou lancez `vercel --prod`.

---

## 4. Vérifier après déploiement

```bash
python3 -m scripts.vercel_diagnose https://athenamarketplace.vercel.app
vercel logs <projet> --follow
```

Résultat attendu : `[OK] application importee`, `[OK] base de donnees — postgresql`, `[OK] POST /connexion sans cookies — 303 -> /admin`.

Comptes démo (si `JAAY_DEMO_MODE=1`) : `admin@jaay.local / AdminJaay2026!` · `fatou@jaay.local / VendeurJaay2026!` · `client@jaay.local / ClientJaay2026!`.

---

## 5. Contenu du dossier

| Inclus | Exclu volontairement |
|---|---|
| `app/` (code, templates, CSS/JS), `index.py`, `vercel.json`, `.python-version`, `.vercelignore`, `.gitignore` | `.env` et tout `.env.*.local` (secrets) |
| `docs/` (14 documents), `README.md`, `RAPPORT.md`, `VERCEL.md`, `AVANT-GITHUB.md`, `DIAGNOSTIC-BUILD-VERCEL.md` | `var/*.db`, `var/dev_secret.key` (données locales) |
| `tests/` (76 tests), `scripts/`, `ops/`, `migrations/`, `.github/workflows/tests.yml` | `media/*` sauf `.gitkeep` (uploads) |
| `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `alembic.ini`, `pytest.ini` | `__pycache__/`, `.pytest_cache/`, `.venv/`, `.vercel/` |
| `.env.example`, `.env.vercel.example` (modèles sans secrets) | `.git/` (historique) |

---

## 6. Pour aller plus loin

- Guide de déploiement complet : [`docs/13-deploiement-vercel.md`](docs/13-deploiement-vercel.md)
- Sécurité avant GitHub : [`AVANT-GITHUB.md`](AVANT-GITHUB.md)
- Diagnostic de l'erreur 500 : [`DIAGNOSTIC-BUILD-VERCEL.md`](DIAGNOSTIC-BUILD-VERCEL.md)
- État précis des fonctionnalités : [`docs/11-etat-des-lieux.md`](docs/11-etat-des-lieux.md)

**Rappel :** aucune intégration Wave / Orange Money / WhatsApp Cloud API / SMS n'est active sans compte marchand et clés réelles. `JAAY_DEMO_MODE=1` publie des identifiants de démonstration : à désactiver avant tout usage commercial.
