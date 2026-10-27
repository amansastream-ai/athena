# Référence API (MVP)

Documentation interactive : `GET /api/docs` (Swagger UI) et `GET /api/openapi.json`.
Toutes les mutations exigent un jeton CSRF : en-tête `X-CSRF-Token` (ou champ `csrf_token` dans le corps JSON), obtenu via la meta `<meta name="csrf-token">` d'une page HTML.

## Authentification

| Méthode | Chemin | Description |
|---|---|---|
| POST | `/api/auth/register` | Inscription vendeur → OTP envoyé |
| POST | `/api/auth/verify-phone` | `{phone, code}` → compte activé |
| POST | `/api/auth/login` | `{identifiant, mot_de_passe}` → session |
| POST | `/api/auth/logout` | Ferme la session |
| GET | `/api/auth/me` | Utilisateur courant, rôle, boutique |

## Catalogue public

| Méthode | Chemin | Paramètres |
|---|---|---|
| GET | `/api/products` | `q, categorie, min_prix, max_prix, tri, dispo, page, per_page` |
| GET | `/api/products/{shop_slug}/{product_slug}` | Fiche produit + images + avis |
| GET | `/api/shops/{slug}` | Boutique + produits |
| GET | `/api/categories` | Catégories |
| GET | `/api/payments/capabilities` | État réel des fournisseurs |

## Commandes

| Méthode | Chemin | Description |
|---|---|---|
| POST | `/api/orders` | Crée une commande (prix relus serveur), renvoie `reference` et `url` |
| GET | `/api/orders/{reference}` | Suivi public |
| POST | `/api/orders/{reference}/proof` | Soumet une preuve de paiement |
| POST | `/api/orders/{id}/transition?status=…` | Transition contrôlée (admin/vendeur) |
| POST | `/api/reviews` | Avis après commande payée |

## Vendeur (rôle vendeur)

`/api/vendor/shop`, `/api/vendor/shop` (PUT), `/api/vendor/products`, `/api/vendor/products/{id}`, `/api/vendor/orders`, `/api/vendor/declarations`, `/api/vendor/payment-methods`, `/api/vendor/commissions`, `/api/vendor/stats`, `/api/ai/assist`.

## Administrateur (rôle admin)

`/api/admin/dashboard`, `/api/admin/orders`, `/api/admin/proofs/{id}/review`, `/api/admin/settings`, `/api/admin/ledger`, `/api/admin/settlements`, `/api/admin/shops/{id}`, `/api/admin/export.csv`.

## Webhooks entrants

| Chemin | Fournisseur | Sécurité |
|---|---|---|
| POST | `/webhooks/wave` | `Wave-Signature: t=…,v1=…` (HMAC du corps brut, ≤ 5 min), déduplication `event.id` |
| POST | `/webhooks/orange-money` | HMAC configurable (`x-om-signature` par défaut) + **re-vérification distante obligatoire** |
| GET/POST | `/webhooks/whatsapp` | Handshake `hub.verify_token` ; `X-Hub-Signature-256` en POST |

Un webhook valide ne confirme le paiement qu'après vérification du montant, de la devise, de la référence et re-vérification auprès du fournisseur. Toute anomalie est enregistrée dans `webhook_events` avec `status` et `error`.

## Codes de retour

| Code | Signification |
|---|---|
| 200/201 | Succès |
| 303 | Redirection HTML (login, flash) |
| 400 | Règle métier refusée (transition invalide, référence dupliquée) |
| 403 | CSRF invalide ou rôle insuffisant |
| 404 | Ressource inexistante ou appartenant à une autre boutique |
| 422 | Validation Pydantic |
| 429 | Limite de débit dépassée |
| 503 | Fournisseur indisponible (jamais « payé » par défaut) |
