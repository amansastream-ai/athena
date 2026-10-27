# Étude des intégrations de paiement — Wave, Orange Money, agrégateurs

> **Avertissement central.** Ce document décrit des API publiquement documentées et le code d'adaptation présent dans `app/services/payments.py`. **Aucune transaction réelle n'a été effectuée** : il n'existe pas de compte marchand, de clé API, de secret webhook ni de sandbox contractuel pour ce projet. Aucun lien de paiement dynamique n'est donc annoncé fonctionnel. Le seul parcours opérationnel aujourd'hui est le paiement manuel avec référence, preuve et validation administrative.

## 1. Wave Business / Checkout API

### Ce qui est documenté officiellement

- L'API **Checkout** expose `POST /v1/checkout/sessions`, `GET /v1/checkout/sessions/:id`, la recherche de sessions, l'expiration et le remboursement. Une session contient `amount`, `currency`, `client_reference` (corrélation avec notre commande), `checkout_status`, `payment_status` et `wave_launch_url` [2](https://docs.wave.com/checkout).
- L'authentification utilise un `Bearer` (clé API créée dans le portail Wave Business) et, pour les requêtes signées, un en-tête `Wave-Signature: t=<timestamp>,v1=<HMAC-SHA256(timestamp + corps)>` [3](https://docs.wave.com/business).
- Les **webhooks** sont signés HMAC-SHA256, portent une enveloppe `{id, type, data}` et les événements `checkout.session.completed`, `checkout.session.payment_failed`, etc. La documentation impose de rejeter les livraisons de plus de 5 minutes, de vérifier la signature et de dédupliquer sur `id` [7](https://docs.wave.com/webhook). Le portail permet d'enregistrer une URL HTTPS et de choisir la stratégie de secret [7](https://docs.wave.com/webhook).
- L'API **Aggregated Merchants** permet à un agrégateur de créer des identités de sous-marchands attachées aux sessions Checkout et aux Payout : c'est la brique pertinente pour une marketplace multi-vendeurs [15](https://docs.wave.com/aggregated-merchants).
- L'API **Balance & Reconciliation** liste les transactions (`merchant_payment`, `api_checkout`, refunds…) : c'est l'outil de rapprochement quotidien [12](https://docs.wave.com/balance-api). L'API **Payout** permet de reverser de l'argent à un numéro Wave [8](https://docs.wave.com/payout).
- Conditions d'accès : compte Wave Business validé (KYB : RCCM, NINEA, pièce d'identité, statuts pour les personnes morales), puis génération des clés dans le portail. Les clés ne sont affichées qu'une seule fois [15](https://docs.wave.com/aggregated-merchants). Des guides tiers indiquent un délai de 3 à 10 jours ouvrés et des frais marchands autour de 1 % plafonnés — **à confirmer contractuellement, ce ne sont pas des chiffres officiels vérifiés ici** [1](https://kolonell.com/en/blog/wave-business-api-integration-guide-2026-en). Chaque pays (Sénégal, Côte d'Ivoire) est une entité juridique distincte avec ses propres clés et son propre onboarding [14](https://boldrails.com/payments/wave-api).

### Ce que nous avons implémenté

`WaveCheckoutProvider` dans `app/services/payments.py` :

1. `create_session()` — POST signé `/v1/checkout/sessions` avec `amount` (chaîne), `currency: "XOF"`, `client_reference = order.reference`, `success_url`, `error_url`, et `aggregated_merchant_id` si configuré. Enregistre un `Payment` en statut `pending` avec l'identifiant de session.
2. `verify_webhook()` — lit le **corps brut**, parse `Wave-Signature`, rejette si l'horodatage a plus de 300 s, calcule `HMAC-SHA256(timestamp + corps, WAVE_WEBHOOK_SECRET)` et compare en temps constant.
3. `handle_event()` — n'accepte que `checkout.session.completed` avec `payment_status=succeeded` et `checkout_status=complete` ; vérifie `client_reference`, montant et devise ; déduplique sur `(provider, event_id)` ; **re-vérifie la session via `GET /v1/checkout/sessions/:id`** avant d'appeler `mark_paid()`.
4. `mark_paid()` — unique point de confirmation, crée la commission et les écritures.

**État : `not_configured`**. Il manque : compte marchand, `WAVE_API_KEY`, `WAVE_WEBHOOK_SECRET`, `WAVE_SIGNING_SECRET`, enregistrement de `https://<domaine>/webhooks/wave`, tests sandbox et activation de `wave_checkout_enabled`. Les tests utilisent un transport HTTP simulé : ils valident la logique, **pas l'intégration réelle**.

## 2. Orange Money Web Payment

### Ce qui est documenté officiellement

- Le service **Orange Money Web Payment / M Payment** est destiné aux marchands disposant d'un site ou d'une application ; il est disponible au Sénégal et dans plusieurs pays d'Afrique [1](https://developer.orange.com/apis/om-webpay). L'inscription se fait sur le portail développeur ou en agence Orange, avec signature d'un contrat [1](https://developer.orange.com/apis/om-webpay).
- Le marchand doit être enregistré et conforme KYA/KYB ; la FAQ renvoie vers l'opérateur local pour les critères exacts et les coûts [10](https://developer.orange.com/apis/om-webpay/faq).
- Le portail Sonatel décrit un parcours en 3 étapes (compte développeur, application de test, dossier administratif pour la production) et liste RCCM, NINEA, CNI/passeport et procuration [7](https://beta.developer.orange-sonatel.com/dev/docs/orange-money). Une page d'assistance Orange Sénégal ajoute RIB et descriptif de l'activité pour l'API Billing [22](https://assistance.orange.sn/questions/1968614-pieces-fournir-integrer-paiement-internet-api-billing-via-orange-money).
- Des guides tiers décrivent un flux OAuth (`client_credentials`) puis un POST de webpayment avec `merchant_key`, `currency` (`OUV` en sandbox, `XOF` en production), `order_id`, `amount`, `return_url`, `cancel_url`, `notif_url`, retournant `pay_token` et `payment_url` [2](https://kolonell.com/en/blog/orange-money-api-web-payment-integration-guide-2026). **Ces chemins et le format exact du callback Sénégal doivent être confirmés sur le portail après signature : nous ne les présentons pas comme garantis.**

### Ce que nous avons implémenté

`OrangeMoneyWebPayProvider` :
1. URLs **entièrement configurables** (`OM_TOKEN_URL`, `OM_WEBPAYMENT_URL`, `OM_STATUS_URL`) : aucun endpoint Sénégal n'est inventé.
2. `_token()` (OAuth client_credentials), `create_session()` (initiation) et `verify_webhook()` (HMAC sur corps brut, nom d'en-tête configurable).
3. `handle_event()` — **fail-closed** : si `OM_STATUS_URL` est absent ou si le statut distant n'est pas final, le paiement reste non confirmé. Un callback seul ne suffit jamais.

**État : `not_configured`**. Manquent : contrat marchand Sonatel, URLs d'API Sénégal confirmées, secret de callback, sandbox et activation. Le test `test_orange_money_is_fail_closed_without_status_url` prouve que l'absence de vérification distante empêche la confirmation.

## 3. Ce qu'un numéro de téléphone ne permet PAS

Wave et Orange Money ne permettent pas, à partir d'un simple numéro de téléphone, de générer un lien de paiement officiel, de connaître le solde d'un tiers ou de confirmer une transaction. Il faut une session marchande (Checkout) ou une demande de paiement (Web Payment/request-to-pay). **Nous ne simulons donc aucun lien dynamique.** La page `/vendeur/paiements` affiche les coordonnées déclarées, la référence de commande et les instructions, et la page de commande permet de soumettre une preuve.

## 4. Stratégie multi-vendeurs : trois modèles possibles

| Modèle | Mécanisme | Avantage | Inconvénient |
|---|---|---|---|
| **A. Encaissement plateforme (recommandé)** | Toutes les sessions Checkout sont créées sur le compte Wave Business de Jaay ; `client_reference` identifie la commande ; répartition ensuite via Payout [8](https://docs.wave.com/payout) ou virement. | Commission captée automatiquement, preuve indiscutable, rapprochement simple [12](https://docs.wave.com/balance-api). | Nécessite un compte marchand agréé et une trésorerie ; responsabilité de la plateforme accrue. |
| **B. Sous-marchands agrégés** | API Aggregated Merchants : un `aggregated_merchant_id` par vendeur attaché à chaque session [15](https://docs.wave.com/aggregated-merchants). | Le client paie « la boutique », la plateforme garde la traçabilité. | Statut d'agrégateur/PSP à obtenir ; onboarding de chaque vendeur. |
| **C. Paiement direct au vendeur (par défaut aujourd'hui)** | Le vendeur déclare son numéro Wave/OM ; le client paie hors plateforme ; preuve + validation. | Aucun contrat marchand requis pour démarrer. | La plateforme ne voit pas l'argent : déclaration obligatoire, contrôle anti-fraude, recouvrement manuel de la commission. |

**Recommandation :** démarrer avec C (opérationnel), obtenir le compte Wave Business et basculer progressivement vers A, puis évaluer B ou un agrégateur agréé (PayDunya, CinetPay) qui couvre Wave + Orange + Free + cartes avec une seule API [12](https://absitech.dev/blog/integrer-wave-orange-money-stripe-e-commerce-senegalais-2026) ; PayDunya documente par exemple un parcours Orange Money Sénégal par QR code [6](https://developers.paydunya.com/doc/EN/softpay). Tout agrégateur doit être validé juridiquement (agrément, conservation des fonds, frais, réversibilité).

## 5. Procédure de configuration (checklist)

1. Obtenir le compte marchand (KYB Wave, contrat Sonatel) et les identifiants.
2. Renseigner `.env` (jamais le code) : `WAVE_*`, `OM_*`. Ne jamais committer `.env`.
3. Déclarer les webhooks HTTPS : `/webhooks/wave`, `/webhooks/orange-money`. Vérifier que le reverse proxy transmet le **corps brut** et les en-têtes de signature.
4. Tester en **sandbox** : création de session, paiement test, réception du webhook, re-vérification distante, unicité de la commission, remboursement.
5. Activer côté admin : `wave_checkout_enabled = true`, `om_webpay_enabled = true`. La page `/admin/paiements` affiche l'état réel des variables détectées.
6. Mettre en place le rapprochement quotidien : comparer `payments`/`ledger_entries` avec le relevé Wave Balance [12](https://docs.wave.com/balance-api) ; investiguer tout écart.
7. Documenter les frais réels dans `payment_fee_bps` après réception du contrat.
8. Faire valider le dispositif par un conseil juridique (agrément paiement, protection des fonds, TVA/impôts, CDP).

## 6. Points NON vérifiés (à ne pas présenter comme acquis)

- Aucun appel réel à `api.wave.com` ni au portail Orange n'a été effectué.
- Les délais d'onboarding, frais marchands et plafonds cités par des blogs tiers ne sont pas confirmés.
- Les chemins d'API Orange Money **Sénégal** et le format de signature du callback `notif_url` restent à confirmer sur le portail après contractualisation.
- L'API Aggregated Merchants peut imposer un statut d'agrégateur que Jaay n'a pas encore.
- La conformité réglementaire (BCEAO, CDP, fiscalité) n'a pas été validée juridiquement.
