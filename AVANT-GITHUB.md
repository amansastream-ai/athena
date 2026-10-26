# Que changer dans `.env` avant GitHub ?

## Réponse courte : **rien — et surtout ne mettez pas `.env` sur GitHub.**

Il n'existe actuellement **aucun fichier `.env`** dans le projet. Les deux seuls fichiers `.env*` présents sont des **modèles sans secrets** :

- `.env.example` — modèle pour le développement local ;
- `.env.vercel.example` — liste des variables pour le dashboard Vercel.

Ces deux fichiers **peuvent et doivent être poussés** : ils ne contiennent que des valeurs vides ou des exemples fictifs (`CHANGER_MOI`, `user:pwd@host`, `<64 caracteres hex>`).

Le fichier `.env` réel, lui, ne doit **jamais** être commité. `.gitignore` l'exclut déjà, ainsi que `var/*.db`, `var/dev_secret.key`, `media/*`, `*.pem`, `*.key` et `.vercel/`.

> 💡 Les secrets ne vont **pas dans le dépôt** : pour Vercel, ils vont dans **Settings → Environment Variables**. Pour votre machine, dans un `.env` local ignoré par git.

---

## 1. Ce qu'il faut modifier, et où

### A. Sur votre machine — `.env` local (jamais poussé)

```bash
cd /home/user/jaay
cp .env.example .env        # ce fichier est ignore par git
```

| Variable | Valeur dans le modèle | À mettre dans votre `.env` |
|---|---|---|
| `JAAY_SECRET_KEY` | *(vide)* | `python3 -c "import secrets; print(secrets.token_hex(32))"` — **unique par environnement** |
| `JAAY_DATABASE_URL` | `sqlite:///var/jaay.db` | En local, garder SQLite. En production, une URI PostgreSQL réelle |
| `POSTGRES_PASSWORD` | `CHANGER_MOI` | Mot de passe fort si vous utilisez Docker Compose |
| `JAAY_PUBLIC_BASE_URL` | `https://exemple.sn` | `https://VOTRE-PROJET.vercel.app` (ou votre domaine) |
| `JAAY_DEV_SHOW_OTP` | `1` | **`0` en production** (sinon le code SMS s'affiche à l'écran) |
| `JAAY_DEMO_MODE` | *(vide)* | `1` pour une démo, **`0` pour un vrai commerce** |

### B. Dans Vercel — Settings → Environment Variables (jamais dans git)

Copiez les valeurs de `.env.vercel.example`, en changeant **uniquement** celles-ci :

| Variable | Valeur à générer / changer | Sensibilité |
|---|---|---|
| `JAAY_SECRET_KEY` | Clé aléatoire de 64 caractères hex, différente de celle du local | 🔴 Critique — invalide toutes les sessions si elle change |
| `JAAY_DATABASE_URL` | URI de votre base Neon/Supabase, **avec le vrai mot de passe** | 🔴 Critique |
| `JAAY_PUBLIC_BASE_URL` | `https://VOTRE-PROJET.vercel.app` | 🟠 Important (liens de partage, webhooks) |
| `JAAY_DEMO_MODE` | `1` pour la démo, `0` ensuite | 🟠 Les comptes démo sont publics |
| `JAAY_AUTO_INIT_DB` | `1` au 1ᵉʳ déploiement, puis `0` | 🟡 |
| `JAAY_MEDIA_BACKEND=s3` + `JAAY_S3_ACCESS_KEY`, `JAAY_S3_SECRET_KEY`, `JAAY_S3_BUCKET`, `JAAY_S3_ENDPOINT`, `JAAY_S3_PUBLIC_BASE_URL` | Clés de votre bucket R2/S3 | 🔴 Critique |
| `SMS_API_KEY`, `WAVE_*`, `OM_*`, `WA_CLOUD_TOKEN`, `AI_API_KEY` | **Laisser VIDES** tant que vous n'avez pas de contrat réel | 🔴 Critique |
| `JAAY_BOOTSTRAP_ADMIN_EMAIL/PASSWORD/PHONE` | Utiliser une seule fois pour créer l'admin, puis **supprimer ces variables de Vercel** | 🔴 Critique |

À garder tels quels : `JAAY_ENV=production`, `JAAY_COOKIE_SECURE=1`, `JAAY_DEV_SHOW_OTP=0`, `JAAY_DEBUG=0`.

### C. Ce qui part sur GitHub

Le code, les templates `.env.example` / `.env.vercel.example`, la doc, les tests — **et aucun secret**.

---

## 2. Procédure exacte avant le premier push

```bash
cd /home/user/jaay

# 1. Verifier qu'aucun secret ne serait pousse
python3 -m scripts.check_before_push

# 2. Initialiser et inspecter
git init
git add .
git status --short            # AUCUN .env, AUCUN var/*.db, AUCUN *.key ne doit apparaitre
git status --ignored --short | grep -E '\.env$|\.db$'   # doit afficher "!!" (ignore)

# 3. Commiter et pousser
git commit -m "Jaay MVP 0.1 — marketplace multi-vendeurs"
git branch -M main
git remote add origin https://github.com/VOTRE_COMPTE/jaay.git
git push -u origin main
```

Ensuite : [vercel.com/new](https://vercel.com/new) → importer le dépôt → renseigner les variables → déployer. Voir [`VERCEL.md`](VERCEL.md).

---

## 3. À ne jamais mettre dans le dépôt

- ❌ `.env`, `.env.local`, `.env.production`, tout fichier `*.key` / `*.pem` ;
- ❌ `var/jaay.db` et les autres bases SQLite (elles contiennent les mots de passe hachés et le journal financier de démo) ;
- ❌ `var/dev_secret.key` ;
- ❌ les photos uploadées dans `media/` ;
- ❌ toute URI PostgreSQL avec un vrai mot de passe, même « temporaire » ;
- ❌ les clés Wave / Orange Money / WhatsApp Cloud / SMS / IA / S3 ;
- ❌ les variables `JAAY_BOOTSTRAP_ADMIN_*` après création du compte admin.

---

## 4. Paramètres GitHub à activer

1. **Dépôt privé** recommandé (contient toute la logique métier et la documentation).
2. **Settings → Code security → Secret scanning** et **Push protection** : GitHub bloquera un commit contenant une clé.
3. Ajouter une branche protégée `main` et activer la 2FA sur votre compte GitHub.
4. Ne jamais utiliser `git add -f .env`.

---

## 5. Si un secret a déjà été poussé

1. **Régénérer immédiatement** le secret concerné (nouveau mot de passe DB, nouvelle `JAAY_SECRET_KEY`, nouvelles clés API). Considérez l'ancien comme compromis, même en dépôt privé.
2. **Purger l'historique** :
   ```bash
   pip install git-filter-repo
   git filter-repo --path .env --invert-paths
   git push --force --all origin
   ```
3. Demander à tous les collaborateurs de **re-cloner** le dépôt.

---

## 6. Ce qui est public volontairement (à savoir)

Les identifiants de démonstration sont écrits en clair dans `app/seed.py`, `README.md`, `docs/` et affichés sur la page `/connexion` quand `JAAY_DEMO_MODE=1` :

```
admin@jaay.local / AdminJaay2026!
fatou@jaay.local / VendeurJaay2026!
client@jaay.local / ClientJaay2026!
```

C'est normal pour **une démo**, mais cela signifie que n'importe qui peut se connecter en administrateur sur votre Vercel si vous laissez `JAAY_DEMO_MODE=1`. Avant tout usage réel : `JAAY_DEMO_MODE=0`, supprimer ces comptes ou changer leurs mots de passe, et retirer l'encart de la page de connexion.

---

## 7. Ce que j'ai corrigé en préparant ce contrôle

- **`Dockerfile`** : contenait un mot de passe PostgreSQL codé en dur (`jaay:jaay`). Il a été **supprimé** — `JAAY_SECRET_KEY` et `JAAY_DATABASE_URL` doivent désormais être fournis au runtime uniquement.
- **`.gitignore`** renforcé : `*.pem`, `*.key`, `*.p12`, `*.pfx`, `var/*.sqlite*`, `var/*.sql`, `.vercel/`, `.venv/`.
- **`scripts/vercel_diagnose.py`** ajouté : vérifie un déploiement en ligne et identifie la cause d'une erreur 500 après un build réussi.
- **`scripts/check_before_push.py`** ajouté : vérifie les exclusions git, les modèles `.env.example` et recherche des secrets réels (clé applicative, URI de BDD, clés AWS/OpenAI/GitHub, clés privées, variables d'intégration). Testé en positif et en négatif.

Résultat actuel du contrôle : **« OK — dépôt publiable »**, avec 2 rappels (dépôt git pas encore initialisé ici, et mode démo public).
