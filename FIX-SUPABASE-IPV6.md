# 🛠 Erreur Vercel + Supabase : `Cannot assign requested address`

## 1. L'erreur exacte

```json
{
  "status": "runtime_error",
  "error": "OperationalError: (psycopg2.OperationalError) connection to server at \"db.gxybuhczjfzpjvaduhqq.supabase.co\" (2a05:d018:1b65:3000:974:b421:ac47:4ddd), port 5432 failed: Cannot assign requested address"
}
```

✅ **Bonne nouvelle** : le package `app/` est maintenant bien embarqué et l'application démarre. Le problème est **réseau**, pas du code.

## 2. Cause

| Élément | Situation |
|---|---|
| `db.<ref>.supabase.co:5432` | Connexion **directe** Supabase. Ne publie qu'un enregistrement DNS **AAAA (IPv6)**. IPv4 est une option payante (« IPv4 add-on »). |
| Vercel Functions | **Pas de sortie IPv6**. Une fonction ne peut ouvrir une socket IPv6. |
| Résultat | `libpq` résout `2a05:...`, tente de s'y connecter et échoue avec `Cannot assign requested address`. |

Vérification faite ici (résolution DNS réelle de votre hôte) :

```
AF_INET6 2a05:d018:1b65:3000:974:b421:ac47:4ddd
AF_INET6 2a05:d018:1b65:3000:974:b421:ac47:4ddd
# aucun enregistrement AF_INET (IPv4)
```

Supabase le documente explicitement : la connexion directe est IPv6, le **pooler partagé** est IPv4 ; pour le serverless, il recommande le **Transaction pooler** (port 6543) avec `NullPool` côté SQLAlchemy. Sources : [Connect to your database](https://supabase.com/docs/guides/database/connecting-to-postgres), [Using SQLAlchemy with Supabase](https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT).

👉 **Aucune modification de `JAAY_SECRET_KEY`, de variable Vercel cachée ou de code ne peut contourner ça.** Il faut changer l'**hôte** dans `JAAY_DATABASE_URL`.

## 3. Le correctif, en 5 étapes

### Étape 1 — Récupérer l'URI du pooler dans Supabase

1. Ouvrez votre projet : `https://supabase.com/dashboard/project/gxybuhczjfzpjvaduhqq`.
2. Cliquez sur **Connect** (en haut de la page) → onglet **Connection string**.
3. Dans **Method**, choisissez **Connection pooling** → **Transaction pooler** (ou **Session pooler**).
4. Copiez l'URI affichée. Elle ressemble à :

```
postgresql://postgres.gxybuhczjfzpjvaduhqq:[YOUR-PASSWORD]@aws-0-eu-west-1.pooler.supabase.com:6543/postgres
```

> ⚠️ La région (`eu-west-1`, `us-east-1`…) et l'indice (`aws-0-`, `aws-1-`) dépendent de **votre** projet. Ne recopiez pas aveuglément l'exemple : prenez exactement la chaîne du dashboard.

### Étape 2 — Remplacer le mot de passe

Remplacez `[YOUR-PASSWORD]` par le **mot de passe de la base** (pas la clé `anon`, ni la `service_role`). Si vous l'avez perdu : Supabase → **Project Settings → Database → Reset database password**. Si le mot de passe contient `@`, `:`, `/`, `#` ou `?`, encodez-le en percent-encoding (`@` → `%40`, etc.).

Notez que le nom d'utilisateur du pooler est **`postgres.<ref>`** (avec le point), et non simplement `postgres`.

### Étape 3 — Ajouter SSL

Ajoutez `?sslmode=require` si ce n'est pas déjà dans l'URI :

```
postgresql+psycopg2://postgres.gxybuhczjfzpjvaduhqq:[YOUR-PASSWORD]@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?sslmode=require
```

L'application ajoute ce paramètre automatiquement si vous l'oubliez, mais mieux vaut qu'il soit explicite.

### Étape 4 — Mettre à jour Vercel

1. **Vercel → votre projet → Settings → Environment Variables**.
2. Trouvez `JAAY_DATABASE_URL`, cliquez sur **Edit**, collez la nouvelle valeur.
3. Cochez bien **Production** (et Preview/Development si vous déployez aussi des previews).
4. **Redéployez** : les variables ne sont injectées qu'au prochain déploiement (`vercel --prod` ou nouveau push GitHub).

### Étape 5 — Vérifier

```bash
curl -s https://athena-market.vercel.app/__diag | python3 -m json.tool
curl -s https://athena-market.vercel.app/health | python3 -m json.tool
python3 -m scripts.vercel_diagnose https://athena-market.vercel.app
```

`/__diag` doit maintenant afficher :

```json
"database": {
  "host": "aws-0-....pooler.supabase.com",
  "port": 6543,
  "pooler": true,
  "direct_supabase_ipv6": false,
  "null_pool": true,
  "ipv4": ["13.xx.xx.xx"],
  "ipv6": [],
  "hint": "Pooler Supabase IPv4 detecte : configuration reseau compatible Vercel."
}
```

et `/health` doit renvoyer `"database": "ok"`.

## 4. Session pooler ou Transaction pooler ?

| | Session pooler | Transaction pooler |
|---|---|---|
| Port | **5432** | **6543** |
| Hôte | `...pooler.supabase.com` | `...pooler.supabase.com` |
| IPv4 | ✅ | ✅ |
| Recommandé pour | serveurs persistants, migrations, Alembic | **serverless / Vercel** |
| Côté Jaay | pool SQLAlchemy classique | **`NullPool` activé automatiquement** |

👉 Utilisez le **Transaction pooler (6543)** pour l'application. Gardez l'URI **directe** uniquement pour vos migrations Alembic exécutées depuis une machine qui a IPv6 — ou exécutez les migrations via le Session pooler.

## 5. Alternatives

- **Add-on IPv4 Supabase** (payant) : rend `db.<ref>.supabase.co:5432` joignable en IPv4. Dans ce cas, posez aussi `JAAY_ALLOW_IPV6_DB=1` sur Vercel pour désactiver le garde-fou ajouté dans le code.
- **Changer de fournisseur PostgreSQL** : Neon, Render, Railway… publient des hôtes IPv4 et fonctionnent avec la configuration actuelle.

## 6. Ce qui a été ajouté au code

| Fichier | Rôle |
|---|---|
| `app/db_network.py` | Détection `db.<ref>.supabase.co` (IPv6 seul), normalisation d'URI (`postgres://` → `postgresql+psycopg2://`, `sslmode=require`, suppression des options Prisma), diagnostic DNS IPv4/IPv6, conseil actionnable |
| `app/config.py` | Sur Vercel, une URI directe Supabase devient un **`config_error`** : l'app renvoie un **503 explicite** avec la marche à suivre au lieu d'une erreur réseau incompréhensible. `JAAY_ALLOW_IPV6_DB=1` désactive le garde-fou |
| `app/db.py` | `NullPool` automatique quand l'URI est un Transaction pooler (port 6543), conformément à la doc Supabase |
| `index.py` | `/__diag` expose `database` (hôte, port, IPv4, IPv6, pooler, conseil) **sans jamais exposer le mot de passe** ; le middleware ajoute `network_hint` + `diagnosis: ipv6_egress_unavailable` |
| `app/application.py` | `/health` renvoie `database_hint` quand la connexion échoue |
| `app/diagnostic.py` | Les `how_to_fix` incluent la procédure pooler Supabase |
| `scripts/vercel_preflight.py` | Refuse une URI directe Supabase avant le déploiement |
| `scripts/vercel_diagnose.py` | Lit la bonne clé `config_error` et affiche le diagnostic réseau |
| `tests/test_supabase_ipv6.py` | Tests de non-régression (détection, normalisation, `NullPool`, 503, absence de fuite de secret) |

## 7. Points encore non vérifiés

La connexion réelle à **votre** base Supabase, la création des 26 tables et des triggers append-only en production, et le peuplement des comptes de démonstration n'ont pas pu être testés ici : ils nécessitent votre mot de passe de base (qui ne doit jamais nous être communiqué). Une fois l'URI du pooler en place, `/health` et `scripts.vercel_diagnose` vous le confirmeront.
