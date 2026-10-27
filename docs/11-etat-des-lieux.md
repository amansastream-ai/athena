# État des lieux précis — ce qui est opérationnel, configurable ou bloqué

**Légende :** ✅ opérationnel et testé · ⚙️ codé mais nécessite configuration/accès · 🧩 modèle/moteur partiel (à compléter) · ❌ non implémenté.

## 1. Côté client

| Fonctionnalité | État | Précision |
|---|---|---|
| Accueil (populaires, nouveautés, catégories) | ✅ | Rendu serveur testé |
| Recherche + filtres (catégorie, prix, dispo, tri) | ✅ | `ILIKE` SQL ; pas de recherche plein texte |
| Fiches produit (photos, prix, stock, avis) | ✅ | Testé |
| Boutiques avec URL publique et partage | ✅ | `slug` unique, liens de partage |
| Contact WhatsApp prérempli et tracé | ✅ | `claim_code`, IP hachée, message complet |
| Favoris | ✅ | API + session |
| Panier / commande avec référence unique | ✅ | `JY-AAAAMMJJ-XXXXXX`, prix recalculés serveur |
| Suivi de commande | ✅ | Machine à états contrôlée |
| Avis anti-fraude | 🧩 | Unicité par commande + achat payé requis ; modération admin UI à compléter |
| Performance mobile (CSS/JS locaux, WebP) | ✅ | Aucun CDN, images redimensionnées |

## 2. Côté vendeur

| Fonctionnalité | État | Précision |
|---|---|---|
| Inscription + vérification du numéro | ⚙️ | OTP fonctionnel mais **fournisseur SMS réel à brancher** (dev = console) |
| Boutique + URL personnalisée | ✅ | Activable après OTP |
| Produits : titre, description, prix, stock | ✅ | CRUD complet |
| Photos | ✅ | Validation MIME Pillow → WebP, stockage local |
| Vidéos | ❌ | Non implémenté (hébergement vidéo coûteux) |
| Variantes (tailles/couleurs) | 🧩 | Table `variants` existe, pas d'interface ni de gestion de stock par variante |
| Gestion des commandes (préparation → livraison) | ✅ | Refus des transitions interdites |
| Moyens de paiement déclarés | ✅ | Vérification admin requise |
| Commissions dues / encaissées | ✅ | Dérivées du journal |
| Déclaration des ventes WhatsApp | ✅ | Code de demande, score de risque, preuve |
| Statistiques vendeur | ✅ | CA, commandes, demandes, graphique 14 jours |
| Promotions / coupons | 🧩 | Moteur de remise dans `create_order` + table `coupons` ; pas d'interface de création |
| Export CSV | ✅ | `/vendeur/export.csv` |
| Assistant IA | ✅ / ⚙️ | Mode local déterministe opérationnel ; API externe optionnelle |

## 3. Côté administrateur

| Fonctionnalité | État | Précision |
|---|---|---|
| Tableau de bord global | ✅ | GMV, commissions, boutiques, preuves, anomalies |
| Gestion vendeurs / clients | ✅ | Rôles, statuts, suspension |
| Modération des annonces | 🧩 | Statuts produits/boutiques ; pas de file de modération dédiée |
| Catégories | 🧩 | Table + seed ; pas d'interface CRUD admin |
| Taux de commission global + par boutique | ✅ | Figé sur chaque commission |
| Vue commandes / transactions | ✅ | Filtres statut/origine |
| Commissions générées / encaissées / en attente | ✅ | Depuis le journal |
| Historique financier non modifiable | ✅ | Triggers SQL + chaîne SHA-256 |
| Détection des fraudes | ✅ | Score, signaux, rapport de sous-déclaration |
| Remboursements / litiges | 🧩 | Écritures compensatoires + statut `disputed` ; table `disputes` sans workflow UI dédié |
| Paramètres des moyens de paiement | ✅ | Page d'état + variables |
| Notifications | ✅ | In-app (SMTP/webhook en option) |
| Rapports exportables | ✅ | Exports CSV admin et vendeur |
| Journal d'administration | ✅ | `audit_logs` chaîné |

## 4. Intégrations externes

| Intégration | État | Ce qui manque |
|---|---|---|
| Paiement manuel (référence + preuve + validation) | ✅ | Rien — c'est le parcours par défaut |
| WhatsApp `wa.me` + tracking | ✅ | Rien |
| Wave Checkout (session dynamique) | ⚙️ | Compte Wave Business KYB, `WAVE_API_KEY`, `WAVE_WEBHOOK_SECRET`, `WAVE_SIGNING_SECRET`, déclaration du webhook, tests sandbox réels, activation |
| Webhook Wave signé | ⚙️ | Même chose ; logique testée avec transport simulé uniquement |
| Aggregated Merchants / Payout / Balance | ❌ | Statut d'agrégateur à obtenir ; rapprochement automatique à écrire |
| Orange Money Web Payment | ⚙️/❌ | Contrat Sonatel, URLs d'API Sénégal confirmées, format de callback, sandbox ; adaptateur fail-closed écrit |
| WhatsApp Cloud API | ⚙️ | Vérification Meta, WABA, numéro dédié, templates approuvés, budget, consentement |
| SMS OTP réel | ⚙️ | Fournisseur + `SMS_API_URL`/`SMS_API_KEY` |
| Email SMTP / alertes | ⚙️ | Variables SMTP ou `JAAY_ALERT_WEBHOOK_URL` |
| API IA externe | ⚙️ | `AI_API_KEY` (mode local fonctionne sans) |
| Stockage S3/R2 | ❌ | Interface `MediaBackend` locale seulement |

## 5. Sécurité / exploitation

| Élément | État | Limite |
|---|---|---|
| Hachage PBKDF2, sessions, CSRF, RBAC, rate limiting | ✅ | Rate limiter en mémoire (Redis à prévoir en multi-instances) |
| Isolation vendeur | ✅ | Testée |
| Audit + journal chaînés | ✅ | Verrou de chaîne **applicatif** : à remplacer par `pg_advisory_xact_lock` en multi-workers |
| Migrations Alembic | ✅ | Testées sur SQLite ; variante PostgreSQL écrite mais non exécutée ici |
| Docker / nginx / sauvegardes / smoke tests | ✅ | Non testés sur un serveur réel |
| Configuration Vercel (`index.py`, `vercel.json`, `.python-version`, `.vercelignore`) | ✅ | Prête ; déploiement réel non effectué (compte + PostgreSQL externes requis) |
| Stockage objet S3/R2 pour les photos | ⚙️ | Adaptateur écrit et testé ; bucket et clés à fournir |
| Connexion en iframe / cookies tiers | ✅ | CSRF signé + cookies CHIPS ; testé sans cookie |
| CGU / confidentialité | 🧩 | Rédigés mais **à valider par un conseil juridique** |
| 2FA administrateur | ❌ | À ajouter avant production |
| Tests de pénétration | ❌ | À commander |

## 6. Ce qui a été démontré par des tests

59 tests passent. Commission 1 000 F → 100 F, 10 000 F → 1 000 F, annulation/remboursement, idempotence, isolation, notification admin, paiement non confirmé jamais payé, statistiques cohérentes avec le journal. Voir `docs/09-tests-rapport.md`.

## 7. Ce qui n'a PAS été démontré

Aucune transaction Wave/Orange Money réelle ou sandbox, aucun message Cloud API réel, aucun SMS réel, aucune charge de production, aucun test PostgreSQL exécuté, aucun audit de sécurité externe, aucune validation juridique. **Ces éléments ne doivent jamais être présentés comme fonctionnels.**

## 8. Prochaines actions concrètes (ordre recommandé)

1. Obtenir le compte Wave Business et le contrat Orange Money (chemin critique).
2. Choisir un fournisseur SMS et configurer l'OTP réel.
3. Déployer en staging PostgreSQL + HTTPS, vérifier migrations et sauvegardes.
4. Tester les webhooks en sandbox avec des clés réelles et ajouter le rapprochement Balance.
5. Compléter les interfaces variantes, coupons, modération et litiges.
6. Faire valider le juridique, ajouter 2FA admin, puis ouvrir les inscriptions publiques.
