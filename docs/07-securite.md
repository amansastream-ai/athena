# Sécurité et confiance

## 1. Authentification et sessions

- Mots de passe : **PBKDF2-HMAC-SHA256**, 260 000 itérations, sel aléatoire de 16 octets, comparaison en temps constant. Aucun mot de passe en clair ni hash MD5/SHA1.
- Vérification du téléphone par **OTP à 6 chiffres** : code haché en base, expiration 10 min, 5 tentatives maximum, consommation unique. En production `JAAY_DEV_SHOW_OTP=0` et un fournisseur SMS réel est obligatoire.
- Sessions : jeton aléatoire de 256 bits, **seul son SHA-256 est stocké**, cookie `jaay_sid` `HttpOnly`, `SameSite=Lax`, `Secure` en production, expiration 7 jours. Verrouillage temporaire du compte après 5 échecs.
- Limitation de débit par IP et par portée (login, OTP, inscription, déclarations, preuves, webhooks). ⚠️ Limiteur en mémoire : en multi-instances, passer à Redis (documenté).

## 2. Permissions et isolation

| Ressource | Admin | Vendeur | Client | Anonyme |
|---|---|---|---|---|
| Catalogue / boutiques | ✓ | ✓ | ✓ | ✓ |
| Créer une commande | ✓ | ✓ | ✓ | ✓ (avec CSRF) |
| Voir les commandes de sa boutique | toutes | les siennes | — | — |
| Valider une preuve de paiement | ✓ | ✗ | ✗ | ✗ |
| Modifier `commission_bps` | ✓ | ✗ | ✗ | ✗ |
| Modifier `commission_bps_override` | ✓ | ✗ | ✗ | ✗ |
| Journal financier / audit | ✓ | ✗ | ✗ | ✗ |

L'isolation est appliquée côté serveur sur chaque requête (`shop_id` dérivé de l'utilisateur connecté, jamais d'un paramètre client). `order_for_vendor()` lève une erreur si la commande appartient à une autre boutique.

## 3. Protections contre les attaques courantes

| Attaque | Protection |
|---|---|
| CSRF | Jeton par session + cookie anonyme, exigé sur **tous** les POST/PUT (formulaire ou en-tête `X-CSRF-Token`), comparaison en temps constant. |
| Injection SQL | Requêtes paramétrées SQLAlchemy, aucun SQL concaténé avec des données utilisateur. |
| XSS | Échappement automatique Jinja2, CSP `default-src 'self'`, pas de CDN, pas de `innerHTML` non contrôlé. |
| Clickjacking | CSP `frame-ancestors` à durcir en production (laissé ouvert pour la prévisualisation). |
| Vol de session | Cookie HttpOnly + Secure + SameSite, jeton haché en base. |
| Faux paiement | Webhook HMAC + horodatage + re-vérification distante + montant/devise/référence contrôlés ; preuve validée uniquement par un admin. |
| Double paiement / double commission | Contraintes UNIQUE sur `payments`, `commissions`, `webhook_events`, `orders.idempotency_key`. |
| Référence de transaction réutilisée | `fraud.duplicate_reference()` interdit la réutilisation inter-commandes. |
| Manipulation des prix | Le client n'envoie aucun prix : `create_order()` relit les prix en base. |
| Altération du journal | Triggers SQL interdisent UPDATE/DELETE sur `ledger_entries` et `audit_logs` ; chaîne SHA-256 vérifiable. |
| Upload malveillant | Vérification du contenu par Pillow (`verify()`), conversion WebP, taille maximale, nom aléatoire, aucun exécutable servi. |
| Énumération de comptes | Messages d'erreur génériques sur login, rate limiting. |
| Redirections ouvertes | L'URL `wa.me` est reconstruite depuis le numéro vérifié du vendeur ; le jeton est signé HMAC. |
| Abus de déclarations | Score de risque (auto-vente, vélocité, code de demande, montant aberrant, faible conversion). |

## 4. Journalisation et audit

- `audit_logs` : action, acteur, rôle, entité, avant/après (JSON), IP, horodatage, chaîne de hachage. Append-only.
- `webhook_events` : tous les événements fournisseurs reçus, validité de la signature, décision et erreur.
- `notifications` : commandes créées, ventes déclarées, preuves soumises, paiements confirmés (avec vendeur, produit/prix, commission, statuts, date, référence de transaction).
- `scripts/verify_integrity.py` vérifie les deux chaînes au démarrage et après restauration.

## 5. Données personnelles

Voir `/pages/confidentialite`. Points clés : IP hachée pour les clics WhatsApp, contenu des conversations privées **non collecté**, cookies strictement nécessaires, conservation des données financières pour obligation de preuve. À compléter avant production : identité du responsable de traitement, registre CDP, DPO, durées de conservation, procédure d'exercice des droits.

## 6. Durcissement à faire avant mise en production

1. `JAAY_ENV=production`, `JAAY_SECRET_KEY` aléatoire, `JAAY_COOKIE_SECURE=1`, `JAAY_DEV_SHOW_OTP=0`.
2. HTTPS obligatoire (HSTS), CSP `frame-ancestors 'none'` si l'app n'est pas embarquée, `X-Frame-Options: DENY`.
3. Fournisseur SMS réel, SMTP ou webhook d'alerte configuré.
4. Rate limiter Redis partagé, WAF en amont, protection DDoS de l'hébergeur.
5. 2FA pour les comptes administrateurs (non implémenté dans la MVP — voir `docs/11`).
6. Rotation des clés API, coffre à secrets, sauvegardes chiffrées, test de restauration.
7. Revue juridique : CGU, confidentialité, conformité CDP et réglementation paiement BCEAO.
8. Tests de pénétration externes et revue de code avant ouverture publique.

## 7. Ce qui n'est PAS protégé dans la MVP

- Le contenu des conversations WhatsApp privées reste hors de portée (par conception).
- Un vendeur peut encore omettre de déclarer une vente : les contrôles sont dissuasifs et statistiques, pas infaillibles.
- Les captures d'écran de paiement sont vérifiées par un humain : la charge et le risque d'erreur humaine demeurent.
- Pas de 2FA admin, pas de détection d'anomalies comportementales avancée, pas de KYC automatisé.
