# Jaay — Rapport de conception multi-agents et état de réalisation

**Date :** 25 octobre 2026 · **Version :** MVP 0.1 · **Statut :** application démarrable et testée en local, intégrations externes non actives.

---

## 1. Nom et concept final

**Jaay** — « vendre » en wolof. Nom court, prononçable en français, anglais et wolof, sans connotation géographique bloquante pour une extension régionale.

> Une boutique en ligne gratuite pour chaque commerçant, un lien partageable partout, WhatsApp au centre de la relation client, et une commission de 10 % prélevée uniquement sur les ventes dont le paiement est réellement vérifié.

### Avantages concurrentiels visés

1. **WhatsApp-first, pas WhatsApp-dépendant.** Les clients cliquent sur un bouton avec message prérempli (produit, prix, référence, lien, code de demande), mais peuvent aussi commander directement sur Jaay. La plateforme ne prétend jamais lire les conversations privées.
2. **Modèle économique lisible :** 0 F à l'entrée, 0 F d'abonnement, 10 % uniquement sur paiement confirmé. Le vendeur voit en permanence sa commission due, encaissée et annulée.
3. **Confiance financière :** journal comptable immuable chaîné par SHA-256, unicité de la commission par commande, remboursements par écritures compensatoires, vérification d'intégrité au démarrage.
4. **Anti-fraude dès la conception :** déclaration des ventes WhatsApp avec code de demande, références de transaction non réutilisables, score de risque, validation humaine des preuves, webhooks signés anti-rejeu.
5. **Sobriété technique :** HTML rendu serveur, CSS/JS locaux, images WebP, aucun CDN — pensé pour un mobile sur réseau lent, avec un coût d'hébergement minimal.

## 2. Architecture retenue et raisons

**FastAPI + SQLAlchemy 2 + Alembic + Jinja2, SQLite en dev / PostgreSQL 16 en production.** Monolithe modulaire avec interfaces d'adaptateurs (paiements, SMS, médias, IA).

| Exigence | Choix | Raison |
|---|---|---|
| Fiabilité | SQL relationnel + transactions | Montants, contraintes d'unicité et triggers : indispensable pour une commission. |
| Sécurité | Contraintes DB + triggers append-only | Impossible de modifier une écriture financière, même en SQL direct. |
| Coût | 1 VPS, 2 workers, ~10–25 €/mois | Pas de Kubernetes, pas de microservices, pas de services managés obligatoires. |
| Maintenance | 1 dépôt, 1 déploiement | Petite équipe, peu d'outillage, migrations versionnées. |
| Vivier développeurs | Python/JS | Compétences disponibles au Sénégal et dans la région. |
| Montée en charge | Adaptateurs + index + recherche SQL | Jusqu'à ~100 000 produits ; ensuite PostgreSQL plein texte/Meilisearch, file de tâches, S3/R2, verrous distribués. |
| Paiements SN | Adaptateurs HTTP isolés | Un fournisseur défaillant n'empêche jamais le parcours manuel. |
| Domaine/hébergement | ASGI standard derrière nginx | Fonctionne sur n'importe quel VPS ou PaaS, avec HTTPS. |

Voir les décisions détaillées (ADR-01 à ADR-07) dans [`docs/02-architecture.md`](docs/02-architecture.md) et l'arborescence dans le README.

## 3. Modèle de données

26 tables : `users`, `otp_codes`, `sessions`, `shops`, `payment_methods`, `categories`, `products`, `product_images`, `variants`, `favorites`, `reviews`, `inquiries`, `orders`, `order_items`, `payments`, `payment_proofs`, `webhook_events`, `commissions`, `ledger_entries`, `commission_settlements`, `notifications`, `audit_logs`, `platform_settings`, `coupons`, `disputes`.

Points clés :

- Montants **entiers XOF** ; taux en points de base (`1000` = 10 %).
- `UNIQUE(commissions.order_id)`, `UNIQUE(payments.provider, provider_reference)`, `UNIQUE(webhook_events.provider, event_id)`, `UNIQUE(orders.idempotency_key)`, `UNIQUE(orders.reference)`, `UNIQUE(inquiries.claim_code)`.
- Isolation `shop_id` sur toutes les tables métier.
- `ledger_entries` et `audit_logs` protégés par triggers SQL interdisant `UPDATE`/`DELETE` et chaînés par `prev_hash`/`entry_hash`.
- Migration Alembic initiale réellement générée et exécutée (`migrations/versions/dbed8e9e46fe_initial_schema.py`), variantes SQLite et PostgreSQL pour les triggers.

Schéma complet : [`docs/03-modele-de-donnees.md`](docs/03-modele-de-donnees.md).

## 4. Fonctionnement exact de la commission de 10 %

```
assiette     = sous-total des articles − remises        (hors livraison)
commission   = (assiette × rate_bps + 5000) // 10000
part vendeur = assiette − commission − frais PSP
```

| Vente | Commission | Part vendeur |
|---:|---:|---:|
| 1 000 F | **100 F** | 900 F |
| 10 000 F | **1 000 F** | 9 000 F |

- Taux global configurable par l'administrateur, surcharge possible par boutique, **figé** dans chaque commission.
- **Aucune commission avant paiement vérifié** : webhook signé re-vérifié côté serveur ou preuve validée par un administrateur. Un clic, une redirection `success_url`, une déclaration vendeur ou une capture d'écran ne suffisent jamais.
- Écritures : `gmv`, `platform_revenue`, `commission_receivable`, `vendor_payable`, `cash`, `payment_expense`, `delivery_payable`.
- Remboursement/annulation après reconnaissance → écritures négatives compensatoires, commission `reversed`, **jamais de suppression**.
- Statistiques = `SUM()` du journal, pas des compteurs séparés.

Détail : [`docs/04-commissions.md`](docs/04-commissions.md).

## 5. Stratégie de vérification des ventes WhatsApp

`wa.me` est un lien click-to-chat : il ouvre une conversation avec un message prérempli, mais **ne transmet ni la conversation, ni le statut de livraison, ni une confirmation de vente** [11](https://www.unipile.com/api-whatsapp-link/) [9](https://www.engagelab.com/blog/whatsapp-api-send-message). Jaay ne prétend donc pas lire les échanges privés.

Dispositif réellement implémenté :

1. Jeton signé HMAC `/wa/<token>` → enregistrement du clic (`claim_code`, IP hachée, source, date) puis redirection serveur vers `wa.me` avec produit, prix, référence et lien.
2. **Code de demande** (`JY-XXXXX`) dans le message ; le vendeur le saisit lors de sa déclaration de vente, ce qui relie la commande à la demande tracée.
3. Déclaration vendeur + preuve de paiement + validation administrateur : la vente n'est reconnue qu'à ce stade.
4. Détection anti-fraude : auto-vente, code absent/inconnu, référence dupliquée, montant aberrant, vélocité, faible taux de conversion demandes→ventes.
5. Évolution possible : Cloud API de Meta (webhooks, templates, numéro dédié), qui exige un portefeuille business, une app, des jetons et des permissions [2](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform) [1](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview/). L'adaptateur est écrit, **désactivé**.

Détail : [`docs/06-whatsapp.md`](docs/06-whatsapp.md).

## 6. Stratégie Wave / Orange Money (sans simulation)

**Wave :** l'API Checkout officielle crée des sessions avec `client_reference`, `success_url`/`error_url` et renvoie `wave_launch_url` [2](https://docs.wave.com/checkout) ; les webhooks sont signés HMAC avec horodatage et déduplication sur `event.id` [7](https://docs.wave.com/webhook) ; les requêtes sortantes sont signées [3](https://docs.wave.com/business). L'API Aggregated Merchants gère les sous-marchands [15](https://docs.wave.com/aggregated-merchants), et Balance/Payout servent au rapprochement et au reversement [12](https://docs.wave.com/balance-api) [8](https://docs.wave.com/payout). **L'accès exige un compte Wave Business validé (KYB).**

**Orange Money :** Web Payment est documenté pour les marchands [1](https://developer.orange.com/apis/om-webpay), avec enregistrement/KYA et contrat [10](https://developer.orange.com/apis/om-webpay/faq) ; le portail Sonatel confirme le parcours sandbox → production avec RCCM, NINEA et pièce d'identité [7](https://beta.developer.orange-sonatel.com/dev/docs/orange-money). **Les chemins d'API Sénégal et le format exact du callback doivent être confirmés après contractualisation** ; nous ne les inventons pas.

**Décision :** un simple numéro de téléphone ne permet pas de générer un lien de paiement dynamique officiel. Nous avons donc :

- un **parcours manuel opérationnel** : référence de commande + coordonnées du vendeur vérifiées + preuve soumise + validation administrateur ;
- des **adaptateurs complets mais désactivés** : Wave (création de session, HMAC, anti-rejeu 5 min, re-vérification distante) et Orange Money (OAuth, HMAC, **fail-closed** sans URL de statut) ;
- une page `/admin/paiements` qui affiche l'état réel des intégrations, et une stratégie multi-vendeurs en trois modèles (encaissement plateforme, sous-marchands agrégés, direct vendeur).

⚠️ **Aucune transaction réelle ou sandbox n'a été effectuée.** Pour activer : compte marchand, clés, secrets, webhooks HTTPS, tests sandbox, puis activation admin. Voir [`docs/05-paiements-wave-orange-money.md`](docs/05-paiements-wave-orange-money.md).

## 7. Plan étape par étape (phases A–F)

| Phase | Contenu | État |
|---|---|---|
| **A** — Analyse/conception | 12 agents, arbitrages, cahier des charges, architecture, schéma, étude paiements, risques | ✅ `docs/00` → `docs/07` |
| **B** — Prototype/fondations | Auth/OTP/rôles, boutiques/URL, produits/photos, catalogue, dashboards | ✅ code + pages + migration |
| **C** — Commandes/commissions | Références, états contrôlés, commissions, journal chaîné, notifications admin | ✅ code + tests |
| **D** — WhatsApp/paiements | Liens tracés + code de demande, adaptateurs Wave/OM/Cloud API, parcours manuel | ✅ code + tests simulés |
| **E** — Tests/sécurité | 76 tests Pytest, CSRF, isolation, immutabilité, fraude, E2E | ✅ 76/76 passent |
| **F** — Déploiement | Alembic, Docker, nginx, sauvegardes, smoke tests, README, plan de lancement | ✅ fichiers livrés, non testés sur serveur réel |

**Plan de mise sur le marché :** pré-lancement (domaine, KYB Wave, contrat Orange, SMS, juridique, 20 vendeurs pilotes) → pilote Dakar (100 boutiques, 500 produits, SLA de validation 2 h) → automatisation des paiements (Wave Checkout + rapprochement Balance) → consolidation nationale → internationalisation. Voir [`docs/10-plan-de-lancement.md`](docs/10-plan-de-lancement.md).

## 8. Fichiers réellement créés

109 fichiers dans `/home/user/jaay/` (hors bases SQLite et caches). Racine : `README.md`, `RAPPORT.md`, `requirements.txt`, `.env.example`, `.gitignore`, `pytest.ini`, `alembic.ini`, `Dockerfile`, `docker-compose.yml`, `Makefile`.

- `app/` — 31 modules Python : `config`, `db`, `models`, `security`, `schemas`, `templating`, `seed`, `main`, `web`, `api/` (7 routers), `services/` (12 services métier).
- `app/templates/` — 34 templates Jinja2 : pages publiques, vendeur, admin, pages légales.
- `app/static/` — `app.css`, `app.js` (aucun CDN).
- `migrations/` — `env.py`, `script.py.mako`, migration initiale exécutée.
- `tests/` — `conftest.py` + 6 fichiers, **59 tests passent**.
- `scripts/` — seed, backup, restore, smoke, verify_integrity.
- `ops/` — entrypoint, nginx.conf.
- `docs/` — 13 documents (conception, cahier des charges, architecture, données, commissions, paiements, WhatsApp, sécurité, déploiement, tests, lancement, état des lieux, API).

**Serveur de prévisualisation en cours d'exécution :** `http://localhost:8000` (proxied preview). `/health` renvoie 200 ; `/`, `/catalogue`, `/connexion`, `/pages/commission`, `/api/docs`, `/api/payments/capabilities` répondent 200 (smoke tests OK).

## 9. Résultats des tests et limites

**76 tests réussis, 0 échec** ; 8 bugs réels découverts et corrigés pendant les tests (dont un blocage de la commande démo qui ne passait jamais en `paid`). Les 11 critères de validation demandés sont couverts nominalement : boutique partagée, fiche + WhatsApp, référence unique, 1 000 F → 100 F, 10 000 F → 1 000 F, annulation/remboursement, notification admin, isolation vendeur, absence de double commission, paiement non confirmé jamais payé, cohérence statistiques/journal. Voir [`docs/09-tests-rapport.md`](docs/09-tests-rapport.md).

**Non vérifié / non opérationnel (à ne pas présenter comme acquis) :**

- aucune transaction Wave/Orange Money réelle ou sandbox, aucun message Cloud API réel, aucun SMS réel ;
- aucune charge de production, aucun test PostgreSQL exécuté (seule la compatibilité des triggers est écrite), aucun test de pénétration externe ;
- verrou de chaîne de hachage applicatif : à remplacer par un verrou advisory PostgreSQL en multi-workers ;
- 2FA admin, modération avancée, variantes, vidéos, litiges structurés et stockage S3 restent à compléter ;
- CGU/confidentialité rédigées mais à valider par un conseil juridique sénégalais.

L'état fonctionnel précis, fonctionnalité par fonctionnalité, figure dans [`docs/11-etat-des-lieux.md`](docs/11-etat-des-lieux.md).

## 9bis. Mise à jour — connexion et déploiement Vercel

Deux correctifs livrés après le rapport initial :

- **Connexion « accès refusé » depuis l'aperçu intégré :** le jeton CSRF dépendait d'un cookie `SameSite=Lax` absent lors d'un POST inter-sites. Il est désormais **signé HMAC** (vérifiable sans cookie) et les cookies sont `SameSite=None; Secure; Partitioned` en HTTPS. Le message d'erreur est devenu explicite.
- **Préparation Vercel :** `index.py`, `vercel.json`, `.python-version`, `.vercelignore`, `.env.vercel.example`, `scripts/vercel_preflight.py`, `scripts/vercel_bootstrap.sh`, backend S3/R2 pour les médias, refus de SQLite sur Vercel et repli `/tmp` pour le filesystem lecture seule. Voir [`VERCEL.md`](VERCEL.md) et [`docs/13-deploiement-vercel.md`](docs/13-deploiement-vercel.md). Le déploiement lui-même nécessite un compte Vercel et une base PostgreSQL externe.

## 10. Prochaines actions concrètes

1. Déposer le dossier Wave Business (RCCM, NINEA, CNI) et contacter Sonatel pour Web Payment : c'est le chemin critique.
2. Choisir un fournisseur SMS et brancher l'OTP réel (`JAAY_DEV_SHOW_OTP=0`).
3. Déployer en staging PostgreSQL + HTTPS ; vérifier migrations, sauvegardes et restauration.
4. Tester les webhooks réels en sandbox, ajouter le rapprochement quotidien Balance, puis activer `wave_checkout_enabled`.
5. Compléter variantes/coupons/modération/litiges/2FA, faire valider le juridique, puis ouvrir les inscriptions publiques.
