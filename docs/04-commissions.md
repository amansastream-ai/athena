# Modèle de commissions — fonctionnement exact

## 1. Paramètres

| Paramètre | Valeur par défaut | Où le modifier |
|---|---|---|
| `commission_bps` | `1000` (10 %) | Admin → Paramètres → `commission_bps` (0–10000) |
| `payment_fee_bps` | `0` | Admin → Paramètres ; frais PSP répercutés |
| `commission_trigger` | `payment_confirmed` | `payment_confirmed` ou `delivered` |
| Taux spécifique | `NULL` | Admin → Boutiques → `commission_bps_override` |

Le taux effectif = surcharge boutique si définie, sinon taux global. **Il est figé dans `commissions.rate_bps` au moment de la reconnaissance :** changer le taux global ne modifie aucune commission passée.

## 2. Formule (entiers, jamais de virgule flottante)

```
assiette   = sous-total des articles − remises        (hors frais de livraison)
commission = (assiette × rate_bps + 5000) // 10000    (arrondi au franc le plus proche)
frais PSP  = (assiette × payment_fee_bps + 5000) // 10000
part vendeur = assiette − commission − frais PSP
```

### Exemples exigés

| Prix de vente | Commission 10 % | Part vendeur |
|---:|---:|---:|
| 1 000 F | **100 F** | 900 F |
| 5 000 F | **500 F** | 4 500 F |
| 10 000 F | **1 000 F** | 9 000 F |
| 50 000 F | **5 000 F** | 45 000 F |
| 100 000 F | **10 000 F** | 90 000 F |

`compute_commission()` est testé sur ces cinq montants plus les cas limites (999 F, 1 F, 0 F).

## 3. Quand la commission naît-elle ?

```
Commande créée / vente déclarée
        │  payment_status = unpaid | pending_verification
        │  AUCUNE écriture, AUCUNE commission
        ▼
Preuve soumise ──► admin valide  ──┐
                                   ├──► Payment(verification=…) ──► payment_status = paid
Webhook signé + re-vérification ───┘                                      │
                                                                        ▼
                                             commission_trigger = payment_confirmed ?
                                                    oui ──► maybe_recognize_sale()
                                                    non ──► attendre statut delivered
                                                                        │
                                                                        ▼
                                              Commission (UNIQUE order_id) + 5 à 7 écritures
```

**Un clic, une redirection `success_url`, une déclaration vendeur ou une capture d'écran ne créent jamais de `Payment` vérifié.** La seule porte d'entrée est `payments.mark_paid()`, qui exige une `verification` non vide et un montant exact.

## 4. Écritures produites à la reconnaissance

| `entry_type` | `account` | Montant |
|---|---|---|
| `sale` | `gmv` | `+ total_xof` (articles + livraison) |
| `commission` | `platform_revenue` | `+ commission` |
| `commission` | `commission_receivable` | `+ commission` (créance sur le vendeur) |
| `vendor_share` | `vendor_payable` | `+ part vendeur` |
| `delivery_fee` | `delivery_payable` | `+ frais livraison` (si > 0) |
| `payment_fee` | `payment_expense` | `+ frais PSP` (si > 0) |

Chaque écriture porte `prev_hash` et `entry_hash = SHA-256(canonical_json(écriture) | prev_hash)`. `verify_chain()` parcourt la table et détecte toute altération. Les triggers SQL interdisent `UPDATE` et `DELETE`.

## 5. Encaissement, annulation, remboursement, litige

**Encaissement (`record_settlement`).** L'administrateur enregistre un règlement réel. Le montant ne peut pas dépasser le solde dû. Deux écritures : `cash +montant` et `commission_receivable −montant`. Les commissions `due` sont soldées par ordre chronologique (FIFO) → statut `paid`.

**Annulation avant paiement.** Aucune commission n'existe ; simple changement de statut + audit. Si la commande a été payée puis annulée, `reverse_sale()` s'applique.

**Remboursement / annulation après reconnaissance.** `reverse_sale()` écrit des montants négatifs sur `gmv`, `platform_revenue`, `vendor_payable`, et sur `cash` (si la commission avait déjà été encaissée) ou `commission_receivable` (sinon), plus `delivery_payable` et `payment_expense` si concernés. La commission passe `reversed`. **Aucune ligne n'est supprimée** : l'historique comptable reste complet et la chaîne de hachage reste valide.

**Litige.** Statut `disputed` : la commande sort du flux normal ; l'administrateur instruit, puis passe à `refunded`, `delivered` ou `cancelled`. Chaque transition est journalisée.

## 6. Garanties contre les doublons

- `UNIQUE(commissions.order_id)` + `db.begin_nested()` (savepoint) : deux appels concurrents ne créent qu'une commission.
- `UNIQUE(payments.provider, provider_reference)` : la même transaction fournisseur ne peut pas payer deux commandes.
- `UNIQUE(webhook_events.provider, event_id)` : un webhook rejoué retourne `duplicate`.
- `UNIQUE(orders.idempotency_key)` : un formulaire soumis deux fois ne crée qu'une commande.
- `fraud.duplicate_reference()` : une référence de preuve déjà utilisée ailleurs est refusée.

⚠️ **Limite multi-processus :** la construction de la chaîne de hachage est sérialisée par un verrou Python. Avec plusieurs workers, remplacer par `pg_advisory_xact_lock` (documenté dans `docs/02`). Les contraintes d'unicité, elles, restent fiables en multi-workers car elles sont dans la base.

## 7. Statistiques affichées

Toutes dérivées de `ledger_entries` :

- Chiffre d'affaires total = `SUM(amount_xof WHERE account='gmv')`
- Commissions générées = `SUM(… 'platform_revenue')`
- Commissions encaissées = `SUM(… 'cash' AND entry_type='settlement')` ; net après reversements = `SUM(… 'cash')`
- Commissions en attente = `SUM(… 'commission_receivable')`
- Part vendeur = `SUM(… 'vendor_payable')` ; frais PSP = `SUM(… 'payment_expense')`

Test `test_financial_stats_equal_sum_of_ledger_entries` vérifie l'égalité avec les écritures enregistrées.

## 8. Règle transparente pour les vendeurs

Page publique `/pages/commission` : exemples chiffrés, gratuité de l'inscription, absence de commission sur les frais de livraison, déclencheur exact, règles d'annulation, et engagement de ne jamais lire les conversations WhatsApp privées. Les CGU reprennent ces engagements.
