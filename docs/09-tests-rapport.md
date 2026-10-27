# Rapport de tests — MVP 0.1

**Date d'exécution :** 25 octobre 2026 · **Environnement :** Python 3.13.16, FastAPI 0.162.0, SQLAlchemy 2.1.6, SQLite isolée, pytest 9.x.
**Commande :** `python3 -m pytest -q`
**Résultat :** **79 tests réussis, 0 échec** (durée ~15 s). Avertissement unique : dépréciation de `starlette.testclient` avec `httpx` (sans impact fonctionnel).

## 1. Répartition

| Fichier | Tests | Domaine |
|---|---:|---|
| `tests/test_commission.py` | 20 | Calcul 10 %, cas limites, taux configurable, unicité, remboursement, statistiques |
| `tests/test_orders.py` | 14 | Références, prix serveur, idempotence, transitions, déclarations, fraude, stock, isolation |
| `tests/test_payments.py` | 10 | Signatures HMAC, anti-rejeu, re-vérification distante, montants, fail-closed OM, création de session |
| `tests/test_whatsapp.py` | 6 | Liens tracés, message prérempli, jetons invalides, Cloud API désactivée |
| `tests/test_security.py` | 10 | Hachage, CSRF, permissions, immutabilité, isolation, rate limiting |
| `tests/test_e2e.py` | 1 | Parcours complet de bout en bout |
| `tests/test_vercel_csrf.py` | 20 | Connexion sans cookies, CHIPS, config Vercel, refus SQLite, normalisation PostgreSQL |

## 2. Critères de validation demandés

| # | Critère | Résultat | Preuve |
|---|---|---|---|
| 1 | Vendeur crée une boutique + lien partagé | ✅ | `test_full_seller_and_buyer_journey` : inscription OTP → boutique active → `/boutique/boutique-e2e` en 200 |
| 2 | Consultation produit + WhatsApp tracé | ✅ | `test_product_whatsapp_redirect_is_tracked` : 302 vers `wa.me/221770000002`, message contenant produit/prix/référence/lien/code |
| 3 | Référence unique de commande | ✅ | `test_order_has_unique_reference_and_server_side_price` : `JY-AAAA...` unique |
| 4 | 1 000 F → 100 F | ✅ | `test_commission_10_percent_examples[1000-100]` |
| 5 | 10 000 F → 1 000 F | ✅ | `test_commission_10_percent_examples[10000-1000]`, `test_10000_gives_9000_to_vendor`, E2E |
| 6 | Annulation/remboursement conformes | ✅ | `test_refund_creates_compensating_entries_and_cancels_commission`, `test_cancel_before_payment_creates_no_commission`, `test_settlement_then_reversal_keeps_stats_consistent` |
| 7 | Notification administrateur | ✅ | `test_declaration_never_confirms_payment_and_notifies_admin`, E2E (`payment_confirmed`) |
| 8 | Isolation stricte entre vendeurs | ✅ | `test_vendor_cannot_see_another_vendors_order_page` (404), `test_vendor_cannot_read_another_vendors_order`, `test_vendor_cannot_change_platform_settings` (403) |
| 9 | Transaction répétée sans double commission | ✅ | `test_exactly_one_commission_per_order_even_on_replay`, `test_wave_webhook_replay_does_not_double_commission`, `test_idempotency_key_prevents_duplicate_order_and_commission` |
| 10 | Paiement non confirmé jamais affiché payé | ✅ | `test_declaration_never_confirms_payment_and_notifies_admin`, `test_vendor_cannot_confirm_his_own_payment`, `test_wave_amount_mismatch_is_rejected`, E2E |
| 11 | Statistiques financières = journal | ✅ | `test_financial_stats_equal_sum_of_ledger_entries` |

## 3. Scénarios supplémentaires couverts

- Webhook Wave : signature valide, signature fausse, horodatage expiré, montant incorrect, re-vérification distante en échec, commande inconnue, rejeu du même `event.id`.
- Orange Money : signature HMAC vérifiée mais **fail-closed** sans URL de statut → aucun paiement confirmé.
- Création de session Wave avec transport simulé : vérifie `client_reference`, montant, devise, en-tête `Wave-Signature` ; confirme que la création d'une session ne confirme rien.
- Déclencheur `commission_trigger=delivered` : aucune commission avant livraison.
- Taux spécifique et figé : changer `commission_bps` de 1250 à 2000 ne recalcule pas une commission existante.
- Anti-fraude : auto-vente détectée, référence de paiement dupliquée refusée, code de demande reliant une inquiry à une commande.
- Immutabilité : `UPDATE`/`DELETE` sur `ledger_entries` et `audit_logs` bloqués par triggers SQL ; chaîne SHA-256 vérifiée.
- Sécurité : CSRF sur l'API, hachage PBKDF2 salé, normalisation des numéros, jetons signés altérés rejetés, client ne pouvant pas injecter un prix.

## 4. Bugs découverts et corrigés pendant les tests

1. `result.items` dans les templates Jinja résolvait la méthode de dictionnaire au lieu de la clé → remplacé par `result['items']`.
2. Macros Jinja importées sans `with context` : le jeton CSRF des formulaires était vide → tous les imports corrigés.
3. `login_post` utilisait `or` sur une clause SQLAlchemy → `TypeError` → parenthèses corrigées.
4. `review_proof()` détectait sa propre preuve comme doublon → ajout de `exclude_order_id` (la commande de démo ne passait jamais en `paid`).
5. URL produit de l'API contenait un placeholder `{shop_slug}` → vrai slug renvoyé.
6. `/api/auth/verify-phone` renvoyait volontairement 400 → implémentation complète (phone + code).
7. Gestionnaire d'erreur 403 masquait le détail `CSRF` sur l'API → détail conservé.
8. Limiteur de débit partagé entre tests → remise à zéro dans `conftest.py`.
9. **« Accès refusé » à la connexion depuis l'aperçu intégré** : le jeton CSRF dépendait du cookie `jaay_csrf` (`SameSite=Lax` non envoyé sur un POST inter-sites). → jeton CSRF **signé HMAC** vérifiable sans cookie + cookies `SameSite=None; Secure; Partitioned`.
10. `Starlette` refuse `partitioned=True` avant Python 3.14 → helper `apply_cookie()` qui ajoute l'attribut CHIPS directement dans l'en-tête.
11. Sur Vercel, `mkdir` sur le code source en lecture seule faisait planter l'import → repli automatique vers `/tmp` et `StaticFiles(check_dir=False)`.

## 5. Ce qui N'A PAS été testé (à ne pas confondre avec « validé »)

- ❌ **Aucune transaction Wave ou Orange Money réelle ou sandbox** : pas de compte marchand, ni clés, ni secret webhook. Les tests utilisent un transport HTTP simulé.
- ❌ **Aucun envoi/réception WhatsApp Cloud API réel** : pas de compte Meta vérifié.
- ❌ **Aucun SMS réel** : l'OTP est simulé en console/interface en dev.
- ❌ **Aucune charge multi-utilisateurs ni test de montée en charge** : pas de test de concurrence avec plusieurs workers PostgreSQL.
- ❌ **Aucun test de pénétration externe**, ni audit de code de sécurité par un tiers.
- ❌ **Aucun test sur PostgreSQL exécuté ici** : les migrations y ont été générées/vérifiées sur SQLite ; la compatibilité des triggers PostgreSQL est écrite mais pas encore exécutée.
- ❌ **Aucune validation juridique** des CGU / confidentialité / conformité paiement.
- ❌ **Aucun test navigateur réel** sur appareil mobile (les templates ont été vérifiés par rendu HTML, pas par capture d'écran).

## 6. Comment reproduire

```bash
cd jaay
pip install -r requirements.txt
python3 -m pytest -q                    # 79 tests
python3 -m scripts.verify_integrity
./scripts/smoke.sh http://localhost:8000
```
