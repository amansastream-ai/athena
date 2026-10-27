# Déploiement sur Vercel — guide complet

> **Objectif :** rendre Jaay accessible publiquement sur `https://<projet>.vercel.app`, avec les comptes démo fonctionnels.
> **Réalisé dans ce dépôt :** configuration Vercel, compatibilité serverless, correction des cookies/CSRF, script de pré-vérification, script d'amorçage, stockage objet optionnel.
> **Non réalisé ici :** le déploiement lui-même — il faut un compte Vercel, une base PostgreSQL externe et vos variables d'environnement. Aucune clé réelle n'est présente dans ce dépôt.

---

## 1. Ce que change Vercel (à comprendre avant de déployer)

Vercel exécute l'application FastAPI comme **une seule fonction serverless** : l'objet `app` est chargé depuis un point d'entrée reconnu (`index.py`, `app/main.py`, etc.) et toutes les requêtes sont routées vers lui [1](https://vercel.com/docs/functions/runtimes/python) [2](https://vercel.com/docs/frameworks/backend/fastapi). La détection du framework FastAPI se fait depuis `requirements.txt`, sans configuration de build [3](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel).

Conséquences concrètes pour Jaay :

| Contrainte | Impact | Solution retenue |
|---|---|---|
| **Système de fichiers éphémère et en lecture seule** | SQLite et les photos locales sont perdues à chaque instance | PostgreSQL externe **obligatoire** ; stockage S3/R2 pour les uploads |
| **Pas de processus long** | Le limiteur de débit en mémoire est propre à chaque instance | Acceptable pour la démo ; passer à Redis/Upstash pour la production |
| **Durée de fonction limitée** | Traitements longs à éviter | `maxDuration: 30` dans `vercel.json` [4](https://vercel.com/docs/functions/configuring-functions/duration) |
| **Démarrage à froid** | `init_db()` au premier démarrage | `JAAY_AUTO_INIT_DB=1`, puis `=0` une fois Alembic en place |
| **HTTPS partout** | Cookies `SameSite=Lax` bloqués dans un iframe inter-sites | Cookies `SameSite=None; Secure; Partitioned` (CHIPS) + jeton CSRF signé |
| **Bundle Python non « tree-shaké »** | 500 Mo max, temps de démarrage | `excludeFiles` + `.vercelignore` pour retirer tests/docs/bases locales [3](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel) |

⚠️ **Vercel Hobby est réservé à un usage personnel/non commercial.** Pour une marketplace réelle avec des paiements, prévoyez un plan Pro et vérifiez les conditions contractuelles, la conformité des données (CDP au Sénégal) et la politique de votre hébergeur de base de données.

---

## 2. Fichiers ajoutés pour Vercel

| Fichier | Rôle |
|---|---|
| `index.py` | **Point d'entrée ASGI racine** : crée littéralement `app = FastAPI(...)` au niveau du module puis appelle `configure_app(app)` — Vercel analyse ce fichier par AST [2](https://vercel.com/docs/frameworks/backend/fastapi) |
| `vercel.json` | `framework: fastapi`, `maxDuration: 30`, `excludeFiles`, en-têtes de cache statique |
| `.python-version` | `3.13` — Vercel gère 3.12 (défaut), 3.13 et 3.14 [3](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel) |
| `.vercelignore` | Exclut `tests/`, `docs/`, `var/*.db`, `media/`, `.env`, Docker… de l'upload |
| `.env.vercel.example` | Liste exacte des variables à coller dans le dashboard |
| `scripts/vercel_preflight.py` | Vérifie les secrets, la connexion PostgreSQL et les fichiers requis |
| `scripts/vercel_bootstrap.sh` | Crée le schéma + les comptes démo dans la base distante |
| `app/config.py` | Normalise `postgres://`, refuse SQLite sur Vercel, repli `/tmp` si le disque est en lecture seule |
| `app/application.py` | `new_app()`, `configure_app(app)`, `lifespan` tolérant aux pannes DB, montages statiques `check_dir=False`, `/health` enrichi |
| `app/main.py` | Ré-exporte l'unique instance créée dans `index.py` (`uvicorn app.main:app` reste valable) |
| `app/diagnostic.py` | Réponse 503 explicite si une variable obligatoire manque (au lieu d'un 500 opaque) |
| `app/security.py` | Jeton CSRF signé HMAC + cookies CHIPS compatibles iframe/HTTPS |
| `app/services/media.py` | Backend S3/R2 pour les photos produits |

---

## 3. Prérequis

1. Un compte **Vercel** (vercel.com) et, si déploiement par CLI, `npm i -g vercel` (CLI ≥ 48.1.8) [2](https://vercel.com/docs/frameworks/backend/fastapi).
2. Un dépôt **GitHub/GitLab/Bitbucket** contenant ce dossier (ou le CLI en local).
3. Une base **PostgreSQL** externe : Neon, Supabase, Neon Postgres, Aiven… Récupérez l'URI de connexion.
   - Format accepté : `postgres://…`, `postgresql://…` ou `postgresql+psycopg2://…` (les deux premiers sont convertis automatiquement).
   - Ajoutez `?sslmode=require` si le fournisseur l'exige.
   - ⚠️ Avec le **pooler transactionnel** Supabase, privilégiez l'URI de connexion directe pour les migrations ; le pooler peut refuser les DDL de création de triggers.
4. (Recommandé) Un bucket **Cloudflare R2 / AWS S3** public pour les photos produits.
5. Une clé secrète : `python3 -c "import secrets; print(secrets.token_hex(32))"`.

---

## 4. Procédure pas à pas

### Étape 1 — Préparer la base PostgreSQL

Copier `.env.vercel.example` vers un fichier local **non commité** (`.env`) et renseigner au minimum :

```bash
cd /home/user/jaay
export JAAY_ENV=production
export JAAY_SECRET_KEY='<64 caracteres hex>'
export JAAY_DATABASE_URL='postgresql+psycopg2://user:password@host/db?sslmode=require'
export JAAY_PUBLIC_BASE_URL='https://VOTRE-PROJET.vercel.app'
export JAAY_COOKIE_SECURE=1
export JAAY_DEV_SHOW_OTP=0
export JAAY_DEMO_MODE=1
export JAAY_AUTO_INIT_DB=1
```

Puis créer le schéma et les comptes de démonstration **depuis votre machine** (la base est accessible depuis Internet, la fonction Vercel n'a pas besoin d'un accès SSH) :

```bash
bash scripts/vercel_bootstrap.sh
```

Ce script exécute `init_db()` (tables + triggers d'immutabilité + catégories + paramètres), `seed_demo()` (comptes, boutiques, produits, une commande vérifiée 10 000 F → 1 000 F de commission) et `verify_integrity`.

> `seed_demo()` est idempotent : s'il existe déjà des utilisateurs, il ne crée rien. Vous pouvez donc le relancer sans risque.

### Étape 2 — Pré-vérification

```bash
python3 -m scripts.vercel_preflight
```

Le script contrôle : `JAAY_SECRET_KEY`, une URL PostgreSQL, la connexion réelle (`SELECT 1`), la présence de `index.py` / `vercel.json` / `requirements.txt` / `.python-version`, le mode démo, le stockage objet et le CLI. Il **échoue (code 1)** si un blocage subsiste.

### Étape 3 — Renseigner les variables dans Vercel

Dans **Project Settings → Environment Variables**, ajoutez toutes les variables de `.env.vercel.example`. Minimum vital :

| Variable | Exemple | Obligatoire |
|---|---|---|
| `JAAY_ENV` | `production` | ✅ |
| `JAAY_SECRET_KEY` | 64 caractères aléatoires | ✅ |
| `JAAY_DATABASE_URL` | `postgresql+psycopg2://…?sslmode=require` | ✅ |
| `JAAY_PUBLIC_BASE_URL` | `https://jaay-xxx.vercel.app` | ✅ |
| `JAAY_COOKIE_SECURE` | `1` | ✅ |
| `JAAY_DEV_SHOW_OTP` | `0` | ✅ |
| `JAAY_DEMO_MODE` | `1` pour la démo, `0` en exploitation réelle | ⚠️ |
| `JAAY_AUTO_INIT_DB` | `1` au premier déploiement, `0` ensuite | recommandé |
| `JAAY_MEDIA_BACKEND` | `s3` | pour les uploads |
| `JAAY_S3_*` | voir `.env.vercel.example` | pour les uploads |

Variables sensibles (`WAVE_*`, `OM_*`, `WA_CLOUD_*`, `SMS_API_KEY`, `AI_API_KEY`) : laissez-les vides tant que vous n'avez pas de contrat marchand. **Aucune intégration de paiement n'est active.**

### Étape 4 — Déployer

**Option A — GitHub (recommandée)** : vérifiez d'abord le dépôt avec `python3 -m scripts.check_before_push` (voir [`AVANT-GITHUB.md`](../AVANT-GITHUB.md)), poussez-le, puis `vercel.com/new` → Import Git Repository. Vercel détecte FastAPI automatiquement, aucune commande de build ni répertoire de sortie à configurer [3](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel).

**Option B — CLI** :

```bash
npm i -g vercel
vercel link          # relie le dossier au projet
vercel               # deploiement de preview
vercel --prod        # production
```

**Option C —`vercel dev`** pour tester localement le comportement serverless :

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
vercel dev
```

⚠️ Avec `VERCEL=1`, l'application **refuse SQLite** avec un message explicite. C'est volontaire : une base fichier perdrait les données à chaque instance.

### Étape 5 — Vérifier le déploiement

```bash
curl https://VOTRE-PROJET.vercel.app/health
./scripts/smoke.sh https://VOTRE-PROJET.vercel.app
```

`/health` renvoie notamment :

```json
{
  "status": "ok", "env": "production", "database": "ok",
  "database_backend": "postgresql", "media_backend": "s3",
  "demo_mode": true, "runtime": "vercel"
}
```

Puis, dans le navigateur :

1. Ouvrir `/connexion` et se connecter avec `admin@jaay.local / AdminJaay2026!` → `/admin` doit s'afficher.
2. Se connecter avec `fatou@jaay.local / VendeurJaay2026!` → `/vendeur`.
3. Ouvrir `/catalogue`, `/boutique/teranga-style`, `/pages/commission`, `/api/docs`.
4. Vérifier `/admin/journal` : 5 écritures, chaîne de hachage intacte, 1 000 F de commission.

### Étape 6 — Activer les photos (si bucket configuré)

Dans Vercel, `JAAY_MEDIA_BACKEND=s3` + `JAAY_S3_*`. Le bucket doit autoriser la lecture publique des objets (ou servir via un CDN). Sur **Cloudflare R2**, laissez `JAAY_S3_ACL` vide : R2 n'accepte pas les ACL S3. `JAAY_S3_PUBLIC_BASE_URL` doit pointer sur le domaine public du bucket.

Test : dans l'espace vendeur, ouvrir un produit et ajouter une image → elle doit s'afficher avec une URL absolue `https://…`.

### Étape 7 — Après le premier déploiement

- Passer `JAAY_AUTO_INIT_DB=0` et utiliser `python -m alembic upgrade head` depuis une machine de confiance pour les évolutions de schéma.
- Désactiver `JAAY_DEMO_MODE` et **supprimer/changer les mots de passe des comptes démo** avant tout usage réel.
- Déclarer les webhooks `https://VOTRE-PROJET.vercel.app/webhooks/wave` et `/webhooks/orange-money` **uniquement après** obtention des contrats marchands et tests sandbox (voir `docs/05`).
- Configurer un nom de domaine personnalisé et mettre à jour `JAAY_PUBLIC_BASE_URL`.
- Mettre en place des sauvegardes PostgreSQL (Vercel ne les gère pas) et tester une restauration.
- Remplacer le limiteur de débit en mémoire par Redis/Upstash, ajouter la 2FA admin et faire valider le juridique.

---

## 4ter. Erreur de build `FASTAPI_ENTRYPOINT_NOT_FOUND`

Le preset FastAPI de Vercel **analyse le fichier d'entrée par AST** : il cherche une assignation `app = FastAPI(...)` au niveau du module. Un fichier qui se contente de ré-exporter une application (`from app.main import app`) est refusé, même si l'objet est bien une instance FastAPI à l'exécution.

Structure retenue dans `index.py` :

```python
from fastapi import FastAPI

app = FastAPI(title="Jaay — ...", docs_url="/api/docs", redoc_url=None)  # <- littéral, détecté

try:
    from app.application import configure_app, lifespan
    app.router.lifespan_context = lifespan
    configure_app(app)          # mounts, middlewares, /health, routers
except Exception as exc:
    from app.diagnostic import configure_diagnostic
    configure_diagnostic(app, exc)   # 503 explicite si une variable manque
```

⚠️ Ne remplacez **jamais** la ligne `app = FastAPI(...)` par un import : le build échouerait à nouveau. Un test (`test_vercel_entrypoint_has_literal_fastapi_assignment`) verrouille ce comportement.

## 4bis. Le build réussit mais le site ne marche pas

Le log Vercel ci-dessous est **un build réussi**, pas une erreur :

```
Running "vercel build"
Using Python 3.13 from .python-version
Installing required dependencies from requirements.txt...
Build Completed in /vercel/output [7s]
Deploying outputs...
Deployment completed
Build cache uploaded
```

Les lignes `/.git/hooks/*.sample` sont de simples modèles de hooks Git listés lors de la préparation des fichiers : sans danger. L'unique avertissement — `Provided 'memory' setting in vercel.json is ignored on Active CPU billing` — a été corrigé en retirant `memory` de `vercel.json`.

**Un build réussi ne garantit pas que l'application démarre** : `JAAY_SECRET_KEY` et `JAAY_DATABASE_URL` sont lus à l'import de `app/config.py`. S'ils manquent, la fonction renvoie une erreur 500 au premier appel. Pour savoir exactement où ça coince :

```bash
python3 -m scripts.vercel_diagnose https://VOTRE-PROJET.vercel.app
```

Le script vérifie `/health`, la connexion PostgreSQL, les pages, le CSS, les cookies et la connexion au compte démo (sans cookies), puis affiche les causes probables. Complétez avec :

```bash
vercel logs <projet> --follow      # trace d'erreur exacte
vercel env ls                      # variables réellement définies
```

Causes les plus fréquentes d'une erreur 500 après un build OK :

1. `JAAY_SECRET_KEY` absent → `RuntimeError: JAAY_SECRET_KEY est obligatoire en production`.
2. `JAAY_DATABASE_URL` absent ou SQLite → `RuntimeError: JAAY_DATABASE_URL doit pointer vers PostgreSQL sur Vercel`.
3. URI PostgreSQL sans `?sslmode=require`, base Neon en pause, ou pooler transactionnel qui refuse les DDL de triggers.
4. `JAAY_SECRET_KEY` **régénérée à chaque déploiement** → les jetons CSRF et les sessions deviennent invalides d'une instance à l'autre. Fixez une valeur stable.
5. `JAAY_DEMO_MODE` non défini → les comptes démo n'existent pas et la connexion échoue.

Après toute modification de variable d'environnement, il faut **redéployer** (`vercel --prod`) : Vercel n'injecte les variables qu'au moment du build/déploiement.

## 5. Dépannage

| Symptôme | Cause probable | Correction |
|---|---|---|
| `JAAY_DATABASE_URL doit pointer vers PostgreSQL sur Vercel` | `VERCEL=1` + SQLite | Renseigner une URL PostgreSQL |
| Build OK mais HTTP 500 partout (`FUNCTION_INVOCATION_FAILED`) | Variable absente **ou** ancien `app/main.py` à imports relatifs chargé par chemin | Pousser le code corrigé (shim `app/main.py`), définir les variables, **redéployer** ; voir `DIAGNOSTIC-BUILD-VERCEL.md` |
| Avertissement `memory ... is ignored` | Réglage obsolète avec Active CPU | Retiré de `vercel.json` (déjà fait) |
| `FASTAPI_ENTRYPOINT_NOT_FOUND: I found index.py but it does not define a top-level FastAPI 'app' instance` | Vercel analyse `index.py` **statiquement** : un simple `from app.main import app` ne suffit pas | `index.py` doit contenir un appel littéral `app = FastAPI(...)` au niveau du module ; la configuration est ajoutée ensuite par `configure_app(app)` |
| 500 au démarrage, « secret key » | `JAAY_SECRET_KEY` absent | Ajouter la variable (32+ caractères) et redéployer |
| 500 « permission denied » sur `/var/task/...` | Tentative d'écriture disque | Déjà neutralisé : repli `/tmp` ; activer S3 pour les uploads |
| CSS/JS en 404 | `app/static` exclu du bundle | Vérifier que `excludeFiles`/`.vercelignore` ne contiennent PAS `app/static/**` |
| Connexion : retour à `/connexion` en boucle | Cookies tiers bloqués | Les cookies sont `SameSite=None; Secure; Partitioned`. Tester en onglet direct (pas iframe) et vérifier `JAAY_COOKIE_SECURE=1` |
| `acces_refuse` / `session_expiree` | Jeton CSRF expiré (12 h) | Recharger la page puis se reconnecter |
| « Table already exists » | `init_db()` + Alembic sur la même base | Choisir **un seul** chemin : `JAAY_AUTO_INIT_DB=1` **ou** Alembic, pas les deux |
| Fonction trop lente / timeout | Froid + `init_db()` | `JAAY_AUTO_INIT_DB=0`, augmenter `maxDuration` [4](https://vercel.com/docs/functions/configuring-functions/duration) |
| Bundle > 500 Mo | Fichiers de dev embarqués | Affiner `excludeFiles` dans `vercel.json` [3](https://vercel.com/kb/guide/ship-a-fastapi-app-on-vercel) |
| Triggers PostgreSQL refusés | Pooler transactionnel / droits DDL | Utiliser la connexion directe et un rôle propriétaire des tables |

### Si la détection automatique échoue

`vercel.json` force déjà `"framework": "fastapi"`. Si le builder ne reconnaît pas ce slug, **retirez la ligne `"framework"`** (la détection automatique via `requirements.txt` suffit). En dernier recours, configuration classique avec un fichier `api/index.py` (à créer) :

```json
{
  "version": 2,
  "builds": [{ "src": "index.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "index.py" }]
}
```

---

## 6. Limites de ce déploiement de démonstration

- **Aucun paiement réel** : Wave / Orange Money / WhatsApp Cloud API restent désactivés (pas de compte marchand ni de clés). Voir `docs/05` et `docs/11`.
- **Pas de SMS réel** : l'OTP est en console en dev ; sur Vercel, renseigner un fournisseur SMS ou désactiver l'inscription publique.
- **Rate limiting non distribué**, **pas de tâches de fond garanties**, **pas de WebSocket**.
- **Sauvegardes non fournies par Vercel** : à mettre en place chez le fournisseur PostgreSQL.
- **Comptes démo publics** : `JAAY_DEMO_MODE=1` affiche les identifiants sur la page de connexion. À **désactiver impérativement** avant une exploitation commerciale.
- Les triggers d'immutabilité PostgreSQL et la migration Alembic n'ont **pas été exécutés sur une base PostgreSQL dans cet environnement** (pas de serveur Postgres disponible ici). Le code est écrit et la logique SQLite est testée ; la première exécution sur Vercel doit être validée via `/health` et `/admin/journal`.

---

## 7. Alternative si Vercel ne convient pas

Si vous avez besoin d'un processus persistant (workers, cron longs, SMS synchrones, stockage local), le déploiement Docker décrit dans `docs/08-installation-deploiement.md` (VPS + PostgreSQL + nginx) reste la cible de production recommandée. Vercel est ici un excellent choix pour **une démo publique rapide et gratuite**, pas pour l'exploitation finale d'une marketplace de paiement.
