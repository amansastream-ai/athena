# Stratégie WhatsApp — conversion, traçabilité et limites honnêtes

## 1. Ce qui est réellement possible sans API

Le format documenté est `https://wa.me/<numéro E.164 sans +>?text=<message encodé>` : il ouvre une conversation avec un message prérempli, **sans authentification, sans compte développeur et sans webhook** [11](https://www.unipile.com/api-whatsapp-link/). Le texte reste visible et modifiable par l'utilisateur [21](https://qualimero.com/en/blog/whatsapp-link). Un lien click-to-chat ne permet ni d'envoyer un message depuis un serveur, ni de recevoir le contenu d'une conversation, ni de connaître le statut de livraison [9](https://www.engagelab.com/blog/whatsapp-api-send-message).

**Conséquence :** Jaay ne peut pas savoir, avec `wa.me`, si une vente a été conclue. Toute affirmation contraire serait fausse.

## 2. Ce que nous avons construit (opérationnel)

### Lien tracé, pas une conversation espionnée

1. Chaque produit et chaque boutique reçoivent un **jeton signé** HMAC (`/wa/<token>`), valable 7 jours, contenant l'identifiant du produit et de la boutique.
2. Au clic, `tracked_redirect()` :
   - enregistre un événement `inquiries` : `claim_code`, date, type, IP **hachée**, hash de session, user-agent, source ;
   - incrémente le compteur de vues ;
   - construit le message prérempli avec **nom du produit, prix, référence (`claim_code`), boutique, lien public** et la question de disponibilité ;
   - reconstruit l'URL `wa.me` **côté serveur** depuis le numéro vérifié du vendeur (aucune redirection ouverte, aucune injection possible) ;
   - répond `302` vers WhatsApp.

Message type :
```
Bonjour, je suis interesse par votre produit Boubou brode premium, affiche a 10 000 F CFA.
Reference : JY-7Q3K2. Boutique : Teranga Style (/teranga-style).
Lien : https://exemple.sn/boutique/teranga-style/produit/boubou-brode-premium.
Est-il disponible ?
```

### Le code de demande : pont entre conversation privée et plateforme

Le `claim_code` est la clé anti-fraude. Le vendeur le retrouve dans le message du client et dans son tableau de bord (« Derniers codes de demande »). Lorsqu'il déclare une vente WhatsApp, il saisit ce code : `fraud.evaluate_order()` relie alors `inquiries.order_id` à la commande déclarée. Une déclaration sans code, avec un code inconnu ou appartenant à une autre boutique augmente le score de risque.

### Statistiques et conversion

Le vendeur voit le nombre de demandes WhatsApp, le tableau de bord admin calcule le ratio ventes déclarées / demandes par boutique, et signale les ratios anormalement bas. Les boutons de partage WhatsApp, Facebook, X et la copie de lien sont générés côté serveur.

### Parcours de transformation en commande

Le client peut : (a) cliquer sur WhatsApp, (b) ouvrir « Commander directement sur Jaay » sur la fiche produit et créer une commande avec une référence unique, en indiquant éventuellement le code de demande reçu. La page de commande affiche les instructions de paiement et permet de soumettre une preuve.

## 3. WhatsApp Business Platform (Cloud API) — ce qu'il faudrait

La Cloud API permet d'envoyer/recevoir des messages, de gérer des templates et de recevoir des webhooks, mais exige un **portefeuille business, un compte WhatsApp, un compte de messagerie, une app Meta, des jetons d'accès et des permissions** [2](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform). Les webhooks notifient les messages entrants et les statuts ; Meta réessaie jusqu'à 7 jours en cas d'échec et peut envoyer des doublons [1](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview/). La mise en route passe par une app Meta, un jeton et un endpoint webhook de test [3](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started). Les messages hors fenêtre de 24 h exigent un template approuvé et sont facturés à la conversation.

### Adaptateur fourni, désactivé

`CloudApiProvider` (`app/services/whatsapp.py`) implémente :
- `send_template()` avec `POST /{phone_number_id}/messages` et bearer token ;
- `verify_signature()` : `X-Hub-Signature-256` = `sha256=HMAC(app_secret, corps_brut)` ;
- `verify_subscription()` : handshake `hub.mode` / `hub.verify_token` / `hub.challenge` ;
- `parse_inbound()` : extraction des messages entrants.

`GET/POST /webhooks/whatsapp` sont prêts. Ils ne sont actifs que si `WA_CLOUD_TOKEN`, `WA_CLOUD_PHONE_NUMBER_ID` et `WA_CLOUD_APP_SECRET` sont définis. Les messages entrants créent des notifications et des entrées d'audit ; **ils ne confirment jamais un paiement**.

### Coût et contraintes à arbitrer avant activation

- Vérification Meta Business et numéro dédié (le numéro personnel des vendeurs ne peut pas être utilisé par la Cloud API).
- Templates approuvés pour les notifications hors fenêtre 24 h, facturation par conversation.
- Conformité : consentement explicite des clients, politique de confidentialité mise à jour, opt-out.
- Modèle économique : la plateforme devient intermédiaire de la conversation → nouvelle responsabilité (support, litiges, modération).

## 4. Stratégie de vérification des ventes WhatsApp (résumé)

| Couche | Ce qu'elle apporte | Limite |
|---|---|---|
| Clic tracé + code de demande | Preuve qu'un client a contacté la boutique pour un produit donné | Ne prouve pas la vente |
| Déclaration vendeur obligatoire (CGU) | Relie la vente au code, au produit et au montant | Dépend de l'honnêteté du vendeur |
| Contrôle anti-fraude | Auto-vente, référence de paiement dupliquée, montant aberrant, vélocité, faible taux de déclaration | Détection probabiliste |
| Preuve + validation administrateur | Confirme le paiement avant toute commission | Charge humaine |
| Webhook Wave/OM (quand actif) | Confirmation automatique et indiscutable | Nécessite contrat marchand |
| Cloud API (quand actif) | Visibilité sur les messages, parcours de commande dans WhatsApp | Vérification Meta, coût, consentement |

**Règle appliquée :** aucune couche isolée ne suffit. Une vente n'est reconnue financièrement que lorsque le paiement est vérifié (webhook signé re-vérifié ou preuve validée par un administrateur), et le code de demande + les signaux de risque permettent de rapprocher cette vente d'une conversation.

## 5. Points NON vérifiés

- Aucun envoi réel via la Cloud API, aucune vérification Meta effectuée.
- Le comportement exact de `wa.me` sur tous les appareils / versions n'a pas été testé sur le terrain.
- Le taux de conversion réel demandes → ventes est inconnu : les seuils de suspicion sont paramétrables et devront être calibrés après 30 jours de données.
- WhatsApp n'est pas un canal de paiement : aucune confirmation de paiement ne peut en provenir.
