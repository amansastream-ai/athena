# Cahier des charges fonctionnel — Jaay MVP 0.1

## 1. Vision et principes

Créer une infrastructure commerciale où chaque vendeur possède sa boutique, chaque produit peut être partagé partout, chaque client contacte facilement le vendeur et chaque vente validée génère une commission traçable. Priorités : fiabilité, simplicité, transparence financière, capacité de croissance.

**Règle commerciale :** inscription et usage gratuits, commission de **10 % par défaut** sur chaque vente dont le paiement est vérifié, taux configurable par l'administrateur.

## 2. Acteurs

| Acteur | Besoins | Droits |
|---|---|---|
| **Client** | Trouver un produit, poser une question sur WhatsApp, commander, payer, suivre, noter. | Lecture publique, création de commande, soumission de preuve, avis après achat payé. |
| **Vendeur** | Boutique gratuite, lien partageable, catalogue, stocks, commandes, déclarations WhatsApp, commissions. | Gestion **uniquement** de sa boutique. Ne peut ni confirmer un paiement, ni modifier son taux, ni voir une autre boutique. |
| **Administrateur** | Vue globale, modération, validation des preuves, paramètres, journal financier, litiges, notifications. | Tous droits sur la plateforme, chaque action sensible journalisée. |
| **Système** | Webhooks, calculs, notifications, sauvegardes. | Aucune confirmation sans preuve vérifiable. |

## 3. Parcours utilisateurs

### Parcours vendeur
1. Inscription (nom, téléphone, e-mail optionnel, mot de passe ≥ 10 car.) → code OTP.
2. Vérification du numéro → boutique active avec URL `/boutique/<slug>`.
3. Personnalisation (nom, description, ville, couleur, numéro WhatsApp).
4. Ajout de produits (titre, description, prix, stock, catégorie, frais de livraison), photos WebP, variantes (modèle prêt).
5. Déclaration des moyens de paiement (Wave, Orange Money, banque, espèces) → validation admin.
6. Partage du lien boutique/produit sur WhatsApp, Facebook, TikTok, Instagram.
7. Réception des demandes WhatsApp; consultation des statistiques de clics.
8. Déclaration des ventes conclues hors plateforme avec le code de demande.
9. Suivi des commandes (préparation, expédition, livraison) et des commissions dues.

### Parcours client
1. Découverte : accueil, catalogue, recherche, filtres, boutique, lien partagé.
2. Fiche produit : photos, prix, stock, description, frais de livraison, vendeur, avis.
3. Contact WhatsApp : message prérempli (nom, prix, référence, lien, code de demande).
4. Commande : quantité, nom, téléphone, retrait/livraison → référence unique `JY-AAAAMMJJ-XXXXXX`.
5. Paiement : instructions + référence, ou lien de paiement officiel si le fournisseur est activé.
6. Preuve : identifiant de transaction soumis → statut « en attente de vérification ».
7. Confirmation par admin ou webhook → commande « payée », commission calculée, notifications.
8. Suivi : confirmée → payée → en préparation → expédiée → livrée ; annulation / remboursement / litige.
9. Avis après achat confirmé (unicité par commande).

### Parcours administrateur
1. Tableau de bord : GMV, commissions générées/encaissées/en attente, boutiques, commandes, demandes.
2. Validation/refus des preuves de paiement (action déclenchant la commission).
3. Modération des boutiques/produits, taux spécifique par boutique, paramètres globaux.
4. Journal financier (écritures immuables + vérification de chaîne) et journal d'audit.
5. Encaissement des commissions dues, gestion des remboursements et litiges.
6. Détection des sous-déclarations et commandes à risque.

## 4. Fonctionnalités livrées (MVP)

### Client
Accueil (populaires/nouveautés), catalogue avec filtres (catégorie, prix, disponibilité, tri), fiches produits, boutiques avec URL personnalisée, favoris, partage social, panier/commande, contact WhatsApp direct, suivi de commande, avis anti-fraude, interface mobile légère.

### Vendeur
Inscription + OTP, création/personnalisation boutique, URL publique, CRUD produits, images WebP, stocks, catégories, gestion commandes, moyens de paiement, calcul et historique des commissions, solde dû, déclaration des ventes WhatsApp, statistiques, promotions/coupons (moteur prêt), export CSV, assistant IA (mode local + option API).

### Administrateur
Tableau de bord global, gestion boutiques/clients, modération, catégories, taux de commission, vue centralisée commandes/transactions, commissions générées/encaissées/en attente, historique financier non modifiable, détection des ventes suspectes, remboursements/litiges/sanctions, paramètres des paiements, notifications, rapports CSV, journal d'activité.

### Intégrations
- WhatsApp click-to-chat tracé + code de demande (opérationnel).
- WhatsApp Cloud API : adaptateur complet, **désactivé** (vérification Meta requise).
- Wave Checkout : adaptateur complet (création de session, signature, webhook, re-vérification distante), **désactivé** (KYB requis).
- Orange Money Web Payment : adaptateur complet avec URLs configurables, **désactivé** et **fail-closed** sans URL de statut.
- Paiement manuel : **opérationnel** (référence + preuve + validation admin).

## 5. Règles métier prioritaires

1. Aucun prix, taux, statut ou montant n'est accepté du client sans recalcul serveur.
2. Aucune vente n'est payée sans preuve vérifiable : webhook signé re-vérifié ou validation admin.
3. Une commande = une commission maximum (`UNIQUE(order_id)`).
4. Toute annulation/remboursement après reconnaissance = écriture compensatoire, jamais suppression.
5. Statistiques financières = somme des écritures du journal, pas un compteur séparé.
6. Isolation stricte : chaque requête vendeur est filtrée par `shop_id`.
7. Taux de commission figé à la date de reconnaissance de la vente.
8. Les frais de livraison ne sont pas commissionnés.

## 6. Critères d'acceptation (tests associés)

| # | Critère | Test |
|---|---|---|
| 1 | Vendeur crée une boutique et partage son lien | `test_full_seller_and_buyer_journey` |
| 2 | Client consulte un produit et contacte le vendeur sur WhatsApp | `test_product_whatsapp_redirect_is_tracked` |
| 3 | Commande reçoit une référence unique | `test_order_has_unique_reference_and_server_side_price` |
| 4 | 1 000 F → 100 F de commission | `test_commission_10_percent_examples` |
| 5 | 10 000 F → 1 000 F de commission | `test_commission_10_percent_examples`, `test_10000_gives_9000_to_vendor` |
| 6 | Vente annulée/remboursée suit les règles comptables | `test_refund_creates_compensating_entries_and_cancels_commission`, `test_cancel_before_payment_creates_no_commission` |
| 7 | Notification administrateur générée selon le statut | `test_declaration_never_confirms_payment_and_notifies_admin`, E2E |
| 8 | Un vendeur ne peut pas consulter les données d'un autre | `test_vendor_cannot_see_another_vendors_order_page`, `test_vendor_cannot_read_another_vendors_order` |
| 9 | Transaction répétée ne double pas la commission | `test_exactly_one_commission_per_order_even_on_replay`, `test_wave_webhook_replay_does_not_double_commission` |
| 10 | Paiement non confirmé ne peut pas être présenté comme payé | `test_declaration_never_confirms_payment_and_notifies_admin`, `test_vendor_cannot_confirm_his_own_payment`, E2E |
| 11 | Statistiques financières = écritures | `test_financial_stats_equal_sum_of_ledger_entries` |

## 7. Hors périmètre MVP (documenté, non simulé)

Paiement en ligne réel sans contrat marchand, lecture des conversations WhatsApp privées, messagerie instantanée intégrée, application mobile native, moteur de recherche distribué, multi-devises et multi-pays, logistique intégrée aux transporteurs, KYC automatisé, programme d'affiliation, publicité interne. Voir `docs/11-etat-des-lieux.md`.
