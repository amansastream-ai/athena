# « Mes variables d'environnement ont disparu » — que se passe-t-il ?

> Constat effectué le **27 octobre 2026 à 00:17 UTC** sur `https://athenamarketplace.vercel.app`.

---

## 1. Réponse courte

| Ce qui a disparu | Est-ce normal ? | Explication |
|---|---|---|
| **La VALEUR** (champ vide / masqué), mais la ligne avec le nom de la variable est toujours là | ✅ **Oui, normal** | Les variables de type **Secret** sont *write-only* : après enregistrement, la valeur ne peut plus être lue, affichée ou récupérée — mais elle est bien injectée dans les déploiements [1](https://vercel.com/docs/environment-variables/sensitive-environment-variables) |
| **Toute la ligne** (le nom aussi) | ❌ **Non** | Mauvais projet, mauvaise équipe (*team*), portée (Environment) différente, ou variable supprimée |
| La valeur est **réellement vide** dans la fonction | ❌ **Non** | Bug connu du CLI : `vercel env add ... --value ... --sensitive` peut enregistrer une chaîne vide tout en affichant « Added Environment Variable » [13](https://github.com/vercel/vercel/issues/17636) [18](https://github.com/vercel/vercel/issues/16160) |

### Pourquoi vos valeurs sont masquées

Depuis août 2026, Vercel distingue **Config** (lisible) et **Secret** (invisible après sauvegarde). Pour **Production, le type par défaut est Secret** [8](https://vercel.com/docs/cli/env). Le dashboard affiche donc le nom de la variable mais **jamais son contenu** — c'est une protection voulue, pas une perte de données. `vercel env pull` renvoie un placeholder pour ces mêmes variables [2](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables).

⚠️ Vous ne pouvez **pas** convertir un Secret en Config : il faut supprimer la ligne et la recréer [1](https://vercel.com/docs/environment-variables/sensitive-environment-variables).

---

## 2. ⚠️ Mais il y a un AUTRE problème : votre domaine ne répond plus

Depuis cet environnement, l'URL que vous m'avez donnée renvoie :

```
HTTP/2 404
x-vercel-error: DEPLOYMENT_NOT_FOUND
The deployment could not be found on Vercel.
```

Ce n'est **pas** le 500 que vous voyez. Cela signifie que **le domaine de production ne pointe vers aucun déploiement actif**. Causes probables :

1. Vous avez déployé en **Preview** (`vercel` sans `--prod`) → le domaine de production reste vide.
2. Le déploiement a été **supprimé / annulé / a échoué** et aucun autre n'a été promu.
3. Un **nouveau projet Vercel** a été créé lors d'un ré-import GitHub : les variables sont sur l'ancien projet, le déploiement sur le nouveau (ou inversement). Cela expliquerait **à la fois** les variables « disparues » et le 500.
4. Vous regardez une **URL différente** de celle testée ici.

👉 **Copiez-moi l'URL exacte du déploiement** qui affiche le 500 (elle ressemble à `https://athena-abc123xyz.vercel.app`) depuis Vercel → Deployments. Je la testerai directement.

---

## 3. Nouvelle sonde : vérifier à distance quelles variables arrivent vraiment

Comme on ne peut pas lire les valeurs des Secrets dans le dashboard, j'ai ajouté à `/health` un bloc **`env_presence`** qui indique uniquement si chaque variable est **présente** (booléen), sans jamais exposer sa valeur :

```json
{
  "status": "ok",
  "env": "production",
  "database": "ok",
  "database_backend": "postgresql",
  "runtime": "vercel",
  "env_presence": {
    "JAAY_SECRET_KEY": true,
    "JAAY_DATABASE_URL": true,
    "JAAY_PUBLIC_BASE_URL": true,
    "JAAY_ENV": true,
    "VERCEL": true
  }
}
```

```bash
python3 -m scripts.vercel_diagnose https://VOTRE-URL.vercel.app
# → [OK] variables presentes dans la fonction
# ou → [FAIL] variables absentes dans la fonction : JAAY_DATABASE_URL, ...
```

Si les booléens sont `true` mais que le site renvoie 500, le problème vient alors de la **connexion PostgreSQL** (URI erronée, `sslmode=require` manquant, base en pause, pooler transactionnel) — et non des variables.

---

## 4. Procédure pour repartir sur des bases saines

### Étape 1 — Identifier le bon projet

Vercel dashboard → vérifiez en haut à gauche **l'équipe (team)** et **le nom du projet**. Comparez avec le projet lié à votre dépôt GitHub (`amansastream-ai/athena`). Si deux projets existent, choisissez celui qui a un déploiement récent et notez son URL.

### Étape 2 — Recréer les variables (dashboard, pas CLI)

Dans **Settings → Environment Variables** du bon projet :

1. **Supprimez** les lignes existantes (icône ⋯ → Remove). C'est le seul moyen de « réécrire » un Secret.
2. **Ajoutez** chaque variable. Choisissez le type :
   - **Config** (recommandé, valeurs visibles) : `JAAY_ENV`, `JAAY_PUBLIC_BASE_URL`, `JAAY_COOKIE_SECURE`, `JAAY_DEV_SHOW_OTP`, `JAAY_DEMO_MODE`, `JAAY_AUTO_INIT_DB`.
   - **Secret** (valeur masquée, c'est normal) : `JAAY_SECRET_KEY`, `JAAY_DATABASE_URL`.
3. Cochez **Production** (et Preview si vous testez des branches). Ne cochez pas seulement Development.
4. Cliquez sur **Save** pour **chaque** variable.

```dotenv
JAAY_ENV=production
JAAY_SECRET_KEY=<64 caractères aléatoires — NOTEZ-LA ailleurs, vous ne la reverrez plus>
JAAY_DATABASE_URL=postgresql+psycopg2://user:password@host/db?sslmode=require
JAAY_PUBLIC_BASE_URL=https://VOTRE-URL.vercel.app
JAAY_COOKIE_SECURE=1
JAAY_DEV_SHOW_OTP=0
JAAY_DEMO_MODE=1
JAAY_AUTO_INIT_DB=1
```

⚠️ Si vous utilisez le CLI malgré tout, **évitez** `--value ... --sensitive` (bug de valeur vide). Utilisez `printf '%s' "$VALEUR" | vercel env add KEY production` ou `--type config` pour les valeurs non sensibles [8](https://vercel.com/docs/cli/env).

### Étape 3 — Redéployer en PRODUCTION

Les modifications de variables ne s'appliquent qu'au **prochain déploiement** [2](https://vercel.com/kb/guide/how-to-add-vercel-environment-variables).

```bash
vercel --prod          # ou poussez sur la branche de production (main)
vercel env ls production
```

### Étape 4 — Vérifier

```bash
curl https://VOTRE-URL.vercel.app/health
python3 -m scripts.vercel_diagnose https://VOTRE-URL.vercel.app
vercel logs <projet> --follow
```

---

## 5. Si le 500 persiste après tout cela

Avec le code actuel, une configuration invalide renvoie un **503 JSON** qui nomme la variable manquante. Si vous voyez encore un **500 brut de la plateforme**, c'est que :

- l'ancien code (sans garde-fou) est encore déployé → repoussez la dernière archive ;
- ou l'import échoue pour une autre raison → `vercel logs <projet> --follow` donne la trace Python exacte.

Copiez-collez-moi cette trace : je vous dirai précisément où ça coince.

---

## 6. Fichiers mis à jour (archive reconstruite)

| Fichier | Changement |
|---|---|
| `app/application.py` | `/health` ajoute `env_presence` (booléens, aucune valeur) |
| `scripts/vercel_diagnose.py` | Détecte `DEPLOYMENT_NOT_FOUND` et signale les variables absentes de la fonction |
| `tests/test_vercel_csrf.py` | Test garantissant qu'aucune valeur de secret n'est exposée |
| `jaay-github.zip` / `.tar.gz` | Reconstruits avec ces corrections |

**79/79 tests passent**, contrôle anti-fuite OK, aperçu local opérationnel.
