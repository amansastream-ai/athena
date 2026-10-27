# Réunion de conception multi-agents — 25 octobre 2026

**Objet :** cadrer la MVP d'une marketplace multi-vendeurs WhatsApp-first pour le Sénégal, avec un modèle de commission fiable.
**Participant·e·s :** 12 agents spécialisés + l'architecte en chef (arbitre).
**Résultat :** décisions techniques, risques financiers, dépendances externes, chemin de livraison.

---

## 1. Synthèse des positions (chaque agent livre son analyse)

**Agent 1 — Architecte en chef.** Proposition : un monolithe modulaire Python/FastAPI, SQLAlchemy + PostgreSQL, rendu serveur Jinja2 pour le MVP. Raison : une seule unité de déploiement, moins de pannes, coût d'hébergement minimal, vivier de développeurs Python correct au Sénégal, SEO natif des fiches boutiques (essentielles quand le trafic vient de WhatsApp/Facebook). Refus d'une architecture microservices + Kubernetes au stade MVP : complexité sans volume.

**Agent 2 — Produit / e-commerce.** Parcours vendeur : inscription → vérification téléphone → boutique → produit → partage. Parcours client : découverte → fiche → WhatsApp ou commande → référence unique → paiement → suivi. Exige que la création de boutique soit gratuite et que la commission ne soit jamais présentée comme un frais clients. Signale un risque majeur : beaucoup de ventes se concluront dans WhatsApp, hors plateforme — il faut un mécanisme de déclaration.

**Agent 3 — Frontend / UX.** Mobile-first obligatoire (trafic presque exclusivement mobile, réseaux lents). Refus d'un SPA lourd avec CDN : pages rendues serveur, CSS/JS locaux, images WebP redimensionnées, zéro police externe. Bouton WhatsApp visible sans scroll sur la fiche produit. Tableaux de bord lisibles sur 360 px.

**Agent 4 — Backend.** API JSON + vues HTML partageant les mêmes services métier. RBAC admin/vendeur/client, isolation par `shop_id` sur toutes les requêtes, validation serveur des prix, machine à états des commandes, journal d'audit. Alerte : ne jamais laisser un vendeur fixer un prix, un statut de paiement ou un taux de commission depuis le client.

**Agent 5 — Paiements / comptabilité.** Règle : un paiement n'est confirmé que par (a) webhook signé + re-vérification distante, ou (b) preuve validée par un administrateur. Journal financier en écriture seule, chaîné par SHA-256, calcul en entiers XOF, une commission par commande par contrainte d'unicité. Distinction explicite GMV / commission / part vendeur / frais PSP / remboursements / encaissé / en attente.

**Agent 6 — Wave & Orange Money.** Vérification documentaire : Wave publie une API Checkout, des webhooks HMAC, une API Aggregated Merchants (sous-marchands) et une API Balance/Payout ; l'accès exige un compte Wave Business validé (KYB). Orange Money publie Web Payment mais l'accès marchand passe par un contrat et le portail local ; les chemins d'API Sénégal et le format exact du callback ne sont pas publics. Conclusion : **aucune intégration ne peut être déclarée fonctionnelle sans compte, clés et tests sandbox**. Un numéro de téléphone ne suffit jamais à créer un lien de paiement dynamique.

**Agent 7 — WhatsApp.** `wa.me` est un lien click-to-chat documenté, sans API, sans webhook, sans lecture de conversation. Il faut tracer le clic sortant (et seulement lui), injecter un **code de demande** dans le message prérempli, et demander au vendeur de le fournir lors de sa déclaration. Cloud API = automatisation possible mais exige vérification Meta, WABA, templates, fenêtre 24 h et budget par message.

**Agent 8 — Cybersécurité.** Exige : hachage PBKDF2, sessions HttpOnly, CSRF sur toute mutation, rate limiting, validations serveur, triggers SQL d'immutabilité, idempotence des webhooks, déduplication des références de paiement, détection d'auto-ventes, CSP, secrets hors code. Refuse qu'un administrateur puisse modifier une écriture sans trace.

**Agent 9 — Base de données / performance.** Schéma relationnel normalisé, index sur `shop_id`, `status`, `created_at`, `slug`, `reference`. SQLite en dev, PostgreSQL en prod. Recherche SQL `ILIKE` pour la MVP ; migration vers la recherche plein texte PostgreSQL / Meilisearch au-delà de ~100 000 produits. Médias sur système de fichiers puis S3/R2. Sauvegardes quotidiennes + test de restauration mensuel.

**Agent 10 — IA.** Fonctions utiles et réalistes : aide à la rédaction de fiches, mots-clés, traduction FR/EN/WO, suggestions de prix par médiane des produits comparables (jamais un prix inventé), recommandations par catégorie/fourchette de prix. Mode local déterministe sans clé, adaptateur OpenAI-compatible optionnel.

**Agent 11 — Qualité / tests.** Exige des tests exécutables couvrant les 11 critères de validation, avec blocage de livraison si un calcul de commission, une isolation ou une confirmation de paiement échoue. Tests d'intégration sur le parcours complet, scénarios de fraude et de rejeu de webhook.

**Agent 12 — Déploiement / exploitation.** Docker + PostgreSQL + nginx, migrations Alembic, variables d'environnement, `/health`, sauvegardes, restauration, surveillance, procédure de mise à jour sans perte de données. Trois environnements : dev / staging / production.

## 2. Conflits arbitrés par l'architecte en chef

| Conflit | Position A | Position B | Décision |
|---|---|---|---|
| Frontend | SPA React/Next.js (Agent 3 minorité) | Rendu serveur Jinja2 | **Rendu serveur** : SEO des boutiques, faible bande passante, pas de chaîne de build, surface d'attaque réduite. Une API JSON existe déjà pour un futur front mobile. |
| Base de données | MongoDB (souplesse catalogue) | Relationnelle | **Relationnelle** : les montants et l'audit exigent transactions, contraintes d'unicité et triggers. Les attributs produits restent en JSON. |
| Paiements | Encaissez tout sur le compte plateforme | Laissez chaque vendeur encaisser | **Les deux, routés** : `PaymentMethod.routing` distingue `platform` et `merchant_direct`. En direct, la plateforme a une créance de commission et croise preuves + codes de demande. |
| Commission | Calculer à la commande | Calculer au paiement | **Au paiement vérifié** (déclencheur configurable `delivered`). Une commande non payée ne génère aucune écriture. |
| Microservices | Files + workers dès le MVP | Tâches en processus | **Monolithe + interface de file** : `BackgroundTasks` aujourd'hui, Celery/Arq + Redis quand le volume le justifie. |
| Intégration Wave/OM | Simuler des liens dynamiques pour la démo | Ne rien afficher | **Parcours manuel réel + adaptateurs HTTP complets mais désactivés**, avec page « État des intégrations » transparente. |

## 3. Décisions techniques retenues

1. **Nom de projet : Jaay** (« vendre » en wolof) — court, mémorisable, extensible hors du Sénégal.
2. FastAPI 0.162 + SQLAlchemy 2 + Alembic + Jinja2 + CSS local ; SQLite dev / PostgreSQL 16 prod.
3. Tous les montants en **entiers XOF**. Taux en **points de base** (`1000` = 10 %), figé sur chaque commission.
4. Source de vérité financière = `ledger_entries` (append-only, chaîne SHA-256). Les statistiques sont dérivées du journal.
5. Confirmation de paiement = webhook signé re-vérifié côté serveur **ou** preuve validée par un administrateur. Jamais un clic.
6. WhatsApp = lien tracé + code de demande ; Cloud API en adaptateur optionnel, désactivé.
7. Sécurité : PBKDF2, CSRF, RBAC, isolation `shop_id`, rate limiting, audit chaîné, triggers SQL.
8. MVP livré en 6 phases (A→F) avec tests exécutables et documentation de déploiement.

## 4. Risques financiers identifiés

| Risque | Gravité | Mitigation retenue |
|---|---|---|
| Vente conclue dans WhatsApp non déclarée | **Critique** | Code de demande obligatoire dans le message, déclaration vendeur, croisement demandes/ventes, score de risque, suspension possible. Mesure résiduelle : **aucune preuve absolue** sans intégration Cloud API ou paiement capté par la plateforme. |
| Fausse preuve de paiement / capture d'écran | Critique | Validation humaine, référence unique non réutilisable, contrôle du montant exact, audit de la décision, rapprochement avec l'API Balance de Wave quand disponible. |
| Double commission sur webhook rejoué | Élevée | `UNIQUE(order_id)`, `UNIQUE(provider, event_id)`, `UNIQUE(provider, provider_reference)`, savepoints. |
| Frais PSP non anticipés | Moyenne | `payment_fee_bps` configurable, affiché séparément de la commission. |
| Remboursement après encaissement | Moyenne | Écriture compensatoire sur `cash` et `platform_revenue`, jamais d'effacement. |
| Change/devise à l'international | Future | `currency` sur les paiements, montants entiers, taux par boutique ; à revoir lors de l'ouverture hors zone XOF. |

## 5. Dépendances externes (bloquantes pour certaines fonctions)

| Dépendance | Statut | Action propriétaire |
|---|---|---|
| Compte Wave Business (RCCM, NINEA, CNI) | **Non obtenu** | Ouvrir le compte, demander l'accès API, récupérer clé + secrets, déclarer le webhook HTTPS. |
| Contrat marchand Orange Money / Sonatel | **Non obtenu** | Signer la convention, obtenir les URLs d'API Sénégal et le format de callback. |
| Fournisseur SMS pour OTP | **Non choisi** | Choisir un opérateur, renseigner `SMS_API_URL` / `SMS_API_KEY`. En dev le code est affiché en console. |
| WhatsApp Cloud API / Meta Business | **Non demandé** | Vérification entreprise, WABA, numéro dédié, templates approuvés, budget messages. |
| Nom de domaine + hébergement + TLS | **Non choisi** | Choisir l'hébergeur (VPS UE ou Dakar), configurer DNS, HTTPS, sauvegardes. |
| Conseil juridique / conformité CDP | **À faire** | Faire valider CGU, confidentialité, registre de traitement, obligations fiscales. |

## 6. Chemin de livraison retenu (budget raisonnable)

- **Phase A** (faite) : cahier des charges, architecture, modèle de données, étude paiements, risques.
- **Phase B** (faite) : auth + rôles + boutiques + produits + catalogue + tableaux de bord.
- **Phase C** (faite) : commandes + commissions + journal financier + notifications admin.
- **Phase D** (faite) : WhatsApp tracé + adaptateurs Wave/OM + parcours manuel de secours.
- **Phase E** (faite) : 59 tests automatisés passent, scénarios de fraude et rejeu couverts.
- **Phase F** (faite) : Docker, migrations Alembic, sauvegardes, smoke tests, documentation.

**Coût de départ estimé :** 1 VPS (2 vCPU / 4 Go), PostgreSQL managé ou conteneurisé, nom de domaine, ~10–25 €/mois hors SMS et frais de passerelle. Aucun coût de licence logicielle.

**Prochaine décision à prendre par le propriétaire :** quel rail de paiement prioriser (Wave direct / agrégateur / manuel), et le nom de domaine définitif.
