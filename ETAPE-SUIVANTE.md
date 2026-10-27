# Étape suivante — sortir du 500 sur `athena-market.vercel.app`

> Mesure du **27 octobre 2026 à 00:57 UTC** depuis cet environnement.

```
/health / /catalogue /connexion /static/app.css /api/docs /api/categories  →  HTTP 500
x-vercel-error: FUNCTION_INVOCATION_FAILED
content-type: text/plain  →  "A server error has occurred"
```

Toutes les routes, y compris le CSS, échouent : l'application ne répond pas du tout.

---

## 1. Ce que vous m'avez envoyé = un log de **BUILD**, pas d'exécution

```
Running "vercel build" → Build Completed → Deployment completed → Build cache uploaded
```

Ces lignes sont **toutes vertes** : Vercel a installé les dépendances et publié le code. Il **n'exécute pas l'application pendant le build**. Le crash se produit à la première requête HTTP, et sa trace se trouve ailleurs :

- **Vercel dashboard → votre projet → Deployments → cliquez sur le dernier déploiement → onglet « Functions » / « Runtime Logs »** ;
- ou en CLI : `vercel logs athena-market --follow` puis rechargez la page.

C'est cette trace Python qu'il me faudrait. Mais bonne nouvelle : **avec la nouvelle version, vous n'en aurez plus besoin** — voir ci-dessous.

---

## 2. 🆕 Sonde `/__diag` : elle répond MÊME quand tout le reste plante

J'ai ajouté dans `index.py` une route enregistrée **en première position**, qui n'importe aucun module métier. Elle fonctionne donc même si `app.application` échoue à s'importer.

```
GET https://athena-market.vercel.app/__diag
```

Exemple de réponse quand une variable manque :

```json
{
  "status": "diagnostic",
  "python": "3.13.16",
  "app_title": "Jaay — configuration a corriger",
  "routes_count": 2,
  "config_error_at_import": "RuntimeError: JAAY_SECRET_KEY est obligatoire en production ...",
  "env_presence": {
    "JAAY_ENV": true, "JAAY_SECRET_KEY": false,
    "JAAY_DATABASE_URL": false, "JAAY_PUBLIC_BASE_URL": false,
    "JAAY_COOKIE_SECURE": false, "JAAY_DEV_SHOW_OTP": false,
    "JAAY_DEMO_MODE": false, "JAAY_AUTO_INIT_DB": false, "VERCEL": true
  },
  "jaay_env_keys": ["JAAY_ENV"],
  "application_import": "error",
  "error": "RuntimeError: JAAY_SECRET_KEY est obligatoire en production ...",
  "traceback": "activez JAAY_DEBUG_ERRORS=1 pour la trace complete"
}
```

Lecture :
- `config_error_at_import` / `error` → **la cause exacte** du 500 ;
- `env_presence` → quelles variables arrivent **réellement** dans la fonction (aucune valeur n'est exposée) ;
- `jaay_env_keys` → tous les noms `JAAY_*` présents, utile pour repérer une faute de frappe ;
- `traceback` → trace complète si vous ajoutez `JAAY_DEBUG_ERRORS=1` (à retirer ensuite).

Un middleware renvoie aussi un **JSON 500 lisible** au lieu du texte brut de Vercel si une erreur survient pendant une requête.

`scripts/vercel_diagnose.py` interroge désormais `/__diag` en premier et affiche ces informations.

---

## 3. Ce qu'il faut faire, dans l'ordre

### Étape 1 — Pousser la dernière archive

**Re-téléchargez `jaay-github.zip`** (elle a été reconstruite avec `/__diag`), remplacez les fichiers dans votre dépôt `amansastream-ai/athena`, puis :

```bash
git add -A && git commit -m "Ajout sonde /__diag" && git push
```

Fichiers concernés : `index.py`, `scripts/vercel_diagnose.py`, `app/application.py`, `tests/test_vercel_csrf.py`, `ETAPE-SUIVANTE.md`.

### Étape 2 — Vérifier le bon projet et les variables

Dans le dashboard Vercel, confirmez en haut **l'équipe (team)** et **le projet** lié à `amansastream-ai/athena`. Puis **Settings → Environment Variables** :

```
JAAY_ENV=production
JAAY_SECRET_KEY=<64 caractères, valeur FIXE>
JAAY_DATABASE_URL=postgresql+psycopg2://user:password@host/db?sslmode=require
JAAY_PUBLIC_BASE_URL=https://athena-market.vercel.app
JAAY_COOKIE_SECURE=1
JAAY_DEV_SHOW_OTP=0
JAAY_DEMO_MODE=1
JAAY_AUTO_INIT_DB=1
```

Cochez **Production**. Rappel : les valeurs de type **Secret** sont masquées après sauvegarde — c'est normal, la ligne reste présente [1](https://vercel.com/docs/environment-variables/sensitive-environment-variables). Si une ligne entière a disparu, vous êtes probablement sur un autre projet ou une autre équipe [2](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables).

Optionnel (diagnostic uniquement) : `JAAY_DEBUG_ERRORS=1` pour obtenir la trace Python complète dans `/__diag`. À supprimer ensuite.

### Étape 3 — Redéployer en production

```bash
vercel --prod
```

Les variables ne prennent effet qu'au **déploiement suivant** [2](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables).

### Étape 4 — Me renvoyer le résultat

```bash
curl https://athena-market.vercel.app/__diag
# ou
python3 -m scripts.vercel_diagnose https://athena-market.vercel.app
```

👉 **Collez-moi le JSON de `/__diag`** : il dira exactement quelle variable manque ou quelle exception Python empêche le démarrage.

---

## 4. Interprétation selon le résultat

| Résultat de `/__diag` | Signification | Action |
|---|---|---|
| **200 + `config_error_at_import`** | Une variable obligatoire manque ou est invalide | Corriger la variable indiquée, **redéployer** |
| **200 + `env_presence` avec des `false`** | Les variables n'atteignent pas la fonction | Mauvais projet/équipe ou portée « Production » non cochée |
| **200 + `application_import: "error"`** avec une autre erreur | Problème de dépendance ou de code | Activer `JAAY_DEBUG_ERRORS=1`, redéployer, lire `traceback` |
| **200 + `application_import: "ok"`** mais `/health` en 500 | La base PostgreSQL est injoignable | Vérifier l'URI, `sslmode=require`, base en pause, connexion directe (pas le pooler transactionnel) |
| **500 ou 404 sur `/__diag` aussi** | L'ancien code est encore déployé | Repousser l'archive et vérifier que le déploiement est bien en **Production** |

---

## 5. État du projet après cette mise à jour

- **79/79 tests passent**, dont 2 nouveaux tests garantissant que `/__diag` répond même avec une configuration cassée et qu'aucune valeur de secret n'est exposée.
- Contrôle anti-fuite : **OK — dépôt publiable**.
- Aperçu local (port 8000) : `/__diag`, `/health`, `/`, `/catalogue`, `/static/app.css` → 200.
- Archive reconstruite : `jaay-github.zip` / `.tar.gz` / `.sha256`.
