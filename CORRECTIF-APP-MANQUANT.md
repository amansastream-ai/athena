# ✅ Cause réelle trouvée : `ModuleNotFoundError: No module named 'app'`

Vos Runtime Logs ont enfin donné la réponse exacte :

```
File "/var/task/index.py", line 34, in <module>
    from app.application import configure_app, lifespan
ModuleNotFoundError: No module named 'app'
...
File "/var/task/index.py", line 41, in <module>
    from app.diagnostic import configure_diagnostic
ModuleNotFoundError: No module named 'app'
Python process exited with exit status: 1
```

---

## 1. Pourquoi

Vercel ne copie **que `index.py`** (et les dépendances pip) dans `/var/task/`. Le dossier **`app/` n'était pas inclus dans le bundle de la fonction**.

Deux raisons cumulées :

1. **Import non traçable.** Le preset FastAPI de Vercel suit les imports statiques pour décider quels fichiers embarquer. Notre `from app.application import ...` était placé **dans un bloc `try/except`** — il n'a pas été suivi.
2. **Fichiers non importés.** `app/templates/` (34 fichiers `.html`) et `app/static/` (CSS/JS) ne sont référencés que par des **chemins calculés à l'exécution**, donc aucun import ne les désigne. Sans directive explicite, Vercel ne les embarque jamais.

Résultat : `/var/task/index.py` existe, `/var/task/app/` n'existe pas → toutes les requêtes échouent avec `FUNCTION_INVOCATION_FAILED`. Ce n'était ni un problème de variables d'environnement, ni de PostgreSQL.

---

## 2. Les 3 corrections appliquées

### A. Import statique au niveau du module dans `index.py`

```python
from fastapi import FastAPI
# IMPORT STATIQUE au niveau du module (plus de try autour) :
from app.application import configure_app, lifespan
from app.config import settings
from app.diagnostic import configure_diagnostic

app = FastAPI(...)          # toujours littéral pour l'analyse AST de Vercel
app.router.lifespan_context = lifespan
configure_app(app)
```

### B. Forcer l'inclusion du dossier dans `vercel.json`

```json
"functions": {
  "index.py":     { "maxDuration": 30, "includeFiles": "app/**", "excludeFiles": "..." },
  "app/main.py":  { "maxDuration": 30, "includeFiles": "app/**", "excludeFiles": "..." }
}
```

`includeFiles: "app/**"` embarque **tout** le package : code Python, `templates/`, `static/`, `api/`, `services/`. C'est la garantie principale. Vérifiez aussi que `.vercelignore` ne contient **pas** de ligne `app/` (ce n'est pas le cas).

### C. La configuration ne fait plus planter l'import

`app/config.py` ne lève plus `RuntimeError` au chargement : il collecte l'erreur dans `settings.config_error` (et génère une clé provisoire si besoin). `configure_app()` détecte cette erreur et renvoie un **503 explicite**. Ainsi l'import statique de `app.application` est sûr, même sans variables.

Effet de bord : `/__diag` indique désormais `config_error` directement, et `application_import` vaut `"ok"` même quand la config est mauvaise.

### D. Sonde enrichie

`GET /__diag` renvoie maintenant :

```json
{
  "package_present": true,
  "package_files": ["__init__.py", "api", "application.py", "static", "templates", ...],
  "config_error": null,
  "env_presence": {...},
  "application_import": "ok"
}
```

Si `package_present` vaut `false` → le dossier `app/` n'est pas dans le dépôt GitHub ou n'a pas été embarqué.

---

## 3. Vérifications faites ici

- **Simulation du layout Vercel** : un dossier ne contenant que `index.py` + `app/`, lancé depuis un autre répertoire avec un `sys.path` restreint → `/__diag` renvoie **200**, `package_present: true`, `application_import: "ok"`.
- Test AST garantissant que `from app.application import ...` reste **au niveau du module** (hors `try`).
- Test garantissant `includeFiles: "app/**"` dans `vercel.json`.
- Configuration invalide simulée (`VERCEL=1`, production, aucune variable) → import OK, `/health` renvoie **503** avec la liste des variables manquantes.
- **99/99 tests passent**, contrôle anti-fuite OK, aperçu local opérationnel.

---

## 4. Ce que vous devez faire

1. **Vérifiez que le dossier `app/` est bien sur GitHub** : ouvrez `https://github.com/amansastream-ai/athena/tree/main/app`. Vous devez voir `application.py`, `config.py`, `templates/`, `static/`, `api/`, `services/`. S'il manque, vous avez copié seulement les fichiers de la racine — recopiez **tout le contenu** de l'archive, dossiers compris.
2. **Re-téléchargez `jaay-github.zip`** (reconstruite) et remplacez les fichiers dans votre dépôt, notamment `index.py`, `vercel.json`, `app/config.py`, `app/application.py`.
3. `git add -A && git commit -m "Fix: include app package in Vercel function bundle" && git push`.
4. **Redéployez** (`vercel --prod` ou automatique via GitHub).
5. Ouvrez `https://athena-market.vercel.app/__diag` :
   - `package_present: true` et `config_error: null` → l'application doit répondre ; testez `/health` puis `/connexion`.
   - `config_error` non nul → corrigez la variable indiquée dans Vercel, puis **redéployez**.
   - `package_present: false` → le dossier `app/` n'est pas arrivé jusqu'à Vercel (voir étape 1).

---

## 5. Si `includeFiles` ne suffisait pas (plan B)

Remplacez `vercel.json` par la configuration classique, qui embarque tout le projet :

```json
{
  "version": 2,
  "builds": [{ "src": "index.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "index.py" }]
}
```

⚠️ Dans ce cas, retirez `"framework": "fastapi"`. Gardez `requirements.txt`, `index.py` et `app/` à la racine.

---

## 6. Fichiers modifiés

| Fichier | Changement |
|---|---|
| `index.py` | Import statique de `app.application` au niveau du module ; `app = FastAPI(...)` conservé ; `/__diag` enrichi (`package_present`, `config_error`) |
| `vercel.json` | `includeFiles: "app/**"` sur les deux entrées de fonctions |
| `app/config.py` | Ne lève plus à l'import ; expose `settings.config_error` |
| `app/application.py` | Import statique de `configure_diagnostic` ; `configure_app()` renvoie 503 si `config_error` |
| `tests/test_vercel_csrf.py` | 3 tests de plus (import statique, `includeFiles`, simulation du layout `/var/task`) |
| Docs | Ce guide + mise à jour des compteurs de tests |
