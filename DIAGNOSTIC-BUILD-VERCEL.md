# Diagnostic du déploiement `https://athenamarketplace.vercel.app`

> Mesures effectuées le **26 octobre 2026 à 20:42 UTC** depuis cet environnement, avec `scripts/vercel_diagnose.py` et `curl`.

---

## 🆕 Mise à jour — nouvelle erreur de build `FASTAPI_ENTRYPOINT_NOT_FOUND`

Votre dernier log Vercel contient une erreur **différente** de la précédente :

```
Erreur : [FASTAPI_ENTRYPOINT_NOT_FOUND] J'ai trouvé index.py mais il ne définit
pas d'instance FastAPI « app » de niveau supérieur.
```

Ici le build **échoue** (avant, il réussissait puis le site renvoyait 500 au runtime).

**Cause :** Vercel analyse `index.py` **statiquement** (AST) et exige une ligne littérale `app = FastAPI(...)` au niveau du module. La version précédente de `index.py` faisait seulement `from app.main import app` — objet parfaitement valable à l'exécution, mais invisible pour l'analyseur. C'est un défaut de **mon** shim précédent, pas de votre configuration.

**Correction appliquée :**

```python
# index.py
from fastapi import FastAPI

app = FastAPI(title="Jaay — ...", docs_url="/api/docs", redoc_url=None)  # <- littéral détecté

try:
    from app.application import configure_app, lifespan
    app.router.lifespan_context = lifespan
    configure_app(app)              # mounts, middlewares, /health, tous les routers
except Exception as exc:
    from app.diagnostic import configure_diagnostic
    configure_diagnostic(app, exc)  # 503 explicite si une variable manque
```

`app/application.py` expose désormais `new_app()`, `configure_app(app)` et `lifespan` ; `app/main.py` ré-exporte l'instance unique. Il n'y a donc **qu'un seul objet FastAPI**, utilisable par Vercel, `uvicorn app.main:app` et les tests.

**Vérifications locales :** `app = FastAPI(...)` bien présent au niveau du module (test AST dédié), `index.app is app.main.app`, lifespan exécuté (25 tables créées au démarrage), `/health`, `/`, `/catalogue`, `/connexion`, `/static/app.css`, `/api/docs` → 200, **99/99 tests passent**.

👉 **Action :** re-téléchargez l'archive (elle a été reconstruite avec ce fix), remplacez les fichiers dans votre dépôt, poussez, puis déployez. Les variables d'environnement de la section ci-dessous restent **indispensables** : sans elles, vous aurez un 500 (ou, avec le nouveau code, un 503 explicite).

---

## 🔴 Résultat mesuré : l'application ne démarre pas du tout

```
GET /health                     → HTTP 500
GET /                           → HTTP 500
GET /catalogue                  → HTTP 500
GET /connexion                  → HTTP 500
GET /api/docs                   → HTTP 500
GET /api/categories             → HTTP 500
GET /favicon.ico                → HTTP 500
GET /static/app.css             → HTTP 500   ← révélateur
```

En-têtes retournés :

```
content-type: text/plain; charset=utf-8
x-vercel-error: FUNCTION_INVOCATION_FAILED
x-vercel-cache: MISS
A server error has occurred
```

**Deux conclusions certaines :**

1. **Ce n'est pas une erreur de route, ni de CSS, ni de base de données inaccessible.** Même `/static/app.css` (un simple fichier statique) renvoie 500. Cela signifie que **l'application ASGI échoue à l'import**, avant de pouvoir répondre à quoi que ce soit.
2. **La réponse est celle de la plateforme Vercel** (`text/plain`, `A server error has occurred`), pas un JSON de notre application. Le handler Python n'a jamais été chargé.

Le build, lui, était entièrement vert (`Build Completed`, `Deployment completed`). Vercel installe le code au build mais **ne l'exécute pas** : le crash arrive à la première requête.

---

## 🎯 Deux causes identifiées — il faut corriger les DEUX

### Cause A — Variables d'environnement absentes dans Vercel

`app/config.py` lit la configuration **dès l'import** :

```python
if s.is_production and not s.secret_key:
    raise RuntimeError("JAAY_SECRET_KEY est obligatoire en production ...")
if s.on_vercel and s.database_url.startswith("sqlite"):
    raise RuntimeError("JAAY_DATABASE_URL doit pointer vers PostgreSQL sur Vercel ...")
```

Sans variable, l'URL par défaut est SQLite → la deuxième vérification échoue immédiatement. Le fichier `.env` étant ignoré par `.gitignore`, **il n'a jamais été envoyé à Vercel** : tout doit être saisi dans le dashboard.

### Cause B — `app/main.py` chargé par chemin par Vercel (confirmé par simulation)

Vercel reconnaît `app/main.py` comme point d'entrée et peut le charger **par chemin** plutôt que comme package Python. L'ancien fichier utilisait des imports relatifs (`from .config import settings`). Simulation reproduite ici :

```
chargement par chemin de l'ancien app/main.py  → ImportError: attempted relative import with no known parent package
chargement par chemin du nouveau shim          → OK FastAPI
```

Les deux causes produisent exactement le même `FUNCTION_INVOCATION_FAILED`. **C'est pourquoi il faut à la fois pousser le code corrigé ET définir les variables.**

> Le déploiement actuel ne contient visiblement pas encore le garde-fou de diagnostic : avec le nouveau code, une configuration invalide renverrait un **503 JSON** expliquant la variable manquante, pas un 500 muet.

---

## ✅ Procédure de correction, dans l'ordre

### Étape 1 — Pousser le code corrigé sur GitHub

Depuis ce workspace, récupérez les fichiers modifiés (ou recopiez tout le dossier `jaay/` sauf `.env*`, `var/`, `media/`), puis :

```bash
git add .
git status --short                 # aucun .env ne doit apparaître
git commit -m "Fix: entrée ASGI Vercel + diagnostic de configuration"
git push
```

Fichiers importants de cette correction : `index.py`, `app/main.py` (shim), `app/application.py`, `app/diagnostic.py`, `vercel.json`, `.vercelignore`, `scripts/vercel_diagnose.py`.

### Étape 2 — Créer une base PostgreSQL (si pas encore fait)

Neon.tech ou Supabase → nouvelle base → copier l'URI de connexion. Format attendu :

```
postgresql+psycopg2://user:password@host/db?sslmode=require
```

⚠️ Utilisez la **connexion directe**, pas le pooler transactionnel Supabase (il refuse les DDL de création de triggers). SQLite est formellement refusé sur Vercel : le disque est éphémère.

### Étape 3 — Coller les variables dans Vercel

**Vercel → athenamarketplace → Settings → Environment Variables.** Vercel permet de coller directement des lignes `CLE=VALEUR`. Cochez bien **Production** (et Preview si vous voulez tester les branches) [6](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables).

Un fichier prêt à copier existe dans le workspace : **`.env.vercel.local`** (ignoré par git). Contenu :

```dotenv
JAAY_ENV=production
JAAY_SECRET_KEY=<clé générée, voir .env.vercel.local>
JAAY_DATABASE_URL=REMPLACEZ_PAR_VOTRE_URI_POSTGRESQL
JAAY_PUBLIC_BASE_URL=https://athenamarketplace.vercel.app
JAAY_COOKIE_SECURE=1
JAAY_DEV_SHOW_OTP=0
JAAY_DEMO_MODE=1
JAAY_AUTO_INIT_DB=1
```

Générer une clé :

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

🔴 **Choisissez une valeur FIXE et ne la régénérez jamais** : elle signe les jetons CSRF et les sessions. La changer invalide toutes les connexions et ramène le message « accès refusé ».

Alternative CLI (le dashboard reste le plus fiable — des versions du CLI ont stocké des valeurs vides avec `--value`) [1](https://vercel.com/docs/cli/env) :

```bash
vercel link
printf '%s' 'LA_VALEUR' | vercel env add JAAY_SECRET_KEY production
vercel env ls production          # vérifier que tout est présent
```

### Étape 4 — RE-DÉPLOYER (obligatoire)

Les variables ne prennent effet que sur le **prochain déploiement**, jamais sur celui déjà en ligne [6](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables). Si le dépôt GitHub est connecté, le push de l'étape 1 déclenche déjà un déploiement ; sinon :

```bash
vercel --prod
```

### Étape 5 — Vérifier

```bash
python3 -m scripts.vercel_diagnose https://athenamarketplace.vercel.app
```

Attendu : `[OK] application importee`, `[OK] base de donnees — postgresql`, `[OK] POST /connexion sans cookies — 303 -> /admin`.

Si une variable manque encore, le nouveau code répond **503 avec un JSON qui nomme la variable manquante** au lieu d'un 500 opaque :

```json
{
  "status": "misconfigured",
  "error": "RuntimeError: ...",
  "missing_variables": ["JAAY_DATABASE_URL"],
  "runtime": "vercel",
  "how_to_fix": ["...", "vercel --prod"]
}
```

Logs d'exécution pour voir la trace exacte :

```bash
vercel logs athenamarketplace --follow
```

---

## 📋 Checklist de vérification

- [ ] Code corrigé poussé sur GitHub (shim `app/main.py` + `index.py` + `app/diagnostic.py`).
- [ ] Base PostgreSQL créée, URI copiée avec `?sslmode=require`.
- [ ] `JAAY_SECRET_KEY` définie **une seule fois**, valeur stable.
- [ ] `JAAY_DATABASE_URL` définie en **Production** (pas seulement Preview).
- [ ] `JAAY_ENV=production`, `JAAY_COOKIE_SECURE=1`, `JAAY_DEV_SHOW_OTP=0`.
- [ ] `JAAY_DEMO_MODE=1` pour retrouver les comptes démo.
- [ ] Redéploiement effectué **après** l'ajout des variables.
- [ ] `/health` renvoie 200 avec `"database": "ok"`.

---

## Rappels

- `JAAY_DEMO_MODE=1` publie les identifiants de démo sur la page de connexion : à désactiver avant tout usage réel.
- Aucune intégration Wave / Orange Money / WhatsApp Cloud API n'est active (pas de contrat marchand).
- Le plan Vercel Hobby est réservé à un usage non commercial.

---

## Ce qui a été fait côté projet (vérifié localement)

| Élément | Résultat |
|---|---|
| `vercel.json` | `memory` retiré (supprime l'avertissement du log de build) |
| `.vercelignore` | `.git/**` ajouté (les `*.sample` ne sont plus listés) |
| `app/main.py` | Shim à imports absolus : chargé par chemin → **OK FastAPI** |
| `app/application.py` | Application réelle déplacée ici |
| `app/diagnostic.py` | 503 explicite au lieu du 500 muet |
| `index.py` | Même garde-fou de diagnostic |
| `scripts/vercel_diagnose.py` | Détecte désormais `x-vercel-error: FUNCTION_INVOCATION_FAILED` |
| Tests | **99/99 passent** ; smoke 9/9 ; aperçu local sur port 8000 opérationnel |
