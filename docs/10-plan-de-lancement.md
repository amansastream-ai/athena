# Plan de lancement et croissance

## 1. Principes directeurs

1. **Résoudre un problème réel avant d'ajouter des fonctionnalités :** le vendeur sénégalais vend déjà sur WhatsApp ; Jaay lui donne une vitrine, un lien, des commandes et des chiffres.
2. **Ne jamais promettre une intégration non testée :** la crédibilité est l'actif le plus rare d'une marketplace.
3. **Croître par densité locale :** mieux vaut 200 boutiques actives à Dakar que 2 000 inscriptions dormantes dans 10 pays.

## 2. Étapes de lancement

### Étape 0 — Pré-lancement (semaines 1–4) — PRIORITÉ

| Action | Livrable | Responsable |
|---|---|---|
| Sécuriser le nom de domaine et l'hébergement | DNS + HTTPS fonctionnels | Ops |
| Obtenir un compte Wave Business | KYB déposé, clés API en attente | Fondateur |
| Contacter Sonatel pour Orange Money Web Payment | Dossier contractualisé | Fondateur |
| Choisir un fournisseur SMS OTP | Contrat + `SMS_API_URL` | Ops |
| Faire valider CGU / confidentialité | Documents signés par un conseil | Juridique |
| Déployer staging + sauvegardes testées | Restauration réussie sur copie | Ops |
| Recruter 20 vendeurs pilotes (marchés de Dakar : Sandaga, Kermel, HLM, Colobane) | Boutiques actives avec ≥ 5 produits | Growth |

**Indicateur de sortie :** 20 boutiques, 100 produits, 10 commandes de test, 0 erreur dans `/admin/journal`.

### Étape 1 — Lancement pilote à Dakar (mois 2–3)

- Ouverture des inscriptions avec vérification téléphone obligatoire.
- Accompagnement manuel des vendeurs (WhatsApp, formation photos/prix).
- Paiement manuel + validation administrative sous 2 h ouvrées (SLA affiché).
- Boucle quotidienne : vérifier `/admin` (preuves, anomalies), appeler les vendeurs à faible déclaration.
- Indicateurs : boutiques actives, produits publiés, clics WhatsApp, demandes → commandes, commandes payées, délai de validation, fraudes détectées, GMV, commissions générées/encaissées.

**Objectifs indicatifs :** 100 boutiques, 500 produits, 200 commandes/mois, ≥ 90 % des preuves traitées en < 2 h, taux de déclaration ≥ 60 %.

### Étape 2 — Automatisation paiement (mois 4–6)

- Activer Wave Checkout en production après tests sandbox réels (délai onéreux : obtenir l'accès API).
- Rapprochement quotidien avec l'API Balance [12](https://docs.wave.com/balance-api), bascule du parcours manuel en secours.
- Décider du modèle A (encaissement plateforme) ou B (sous-marchands agrégés) avec Wave [15](https://docs.wave.com/aggregated-merchants).
- Évaluer un agrégateur agréé (PayDunya/CinetPay) pour couvrir Orange Money, Free Money et les cartes [12](https://absitech.dev/blog/integrer-wave-orange-money-stripe-e-commerce-senegalais-2026).
- Mettre en place les reversements vendeurs (Payout Wave [8](https://docs.wave.com/payout) ou virement) et le suivi des commissions encaissées.

**Objectif :** ≥ 80 % des paiements confirmés automatiquement, commission captée sans recouvrement manuel.

### Étape 3 — Consolidation nationale (mois 6–12)

- Catégories et recherche avancée, recommandations, promotions/coupons automatisés.
- Programme de vendeurs vérifiés (badge, KYC manuel), avis modérés, litiges structurés.
- Partenariats : marchés, coopératives d'artisans, livraison locale, influenceurs.
- support client multicanal, FAQ, formation vendeurs en wolof/français.
- Deuxième ville (Thiès, Saint-Louis, Mbour) puis pays de l'UEMOA.

### Étape 4 — Internationalisation (après 12 mois)

- Multi-devises et taux de commission par pays, i18n FR/EN, comptes marchands locaux (Wave CI est une entité distincte [14](https://boldrails.com/payments/wave-api)).
- File de tâches, recherche externe, réplicas PostgreSQL, verrous distribués, médias S3/R2, observabilité avancée.
- Conformité par juridiction (protection des données, fiscalité, paiement).

## 3. Acquisition

| Canal | Tactique | Coût |
|---|---|---|
| WhatsApp (prioritaire) | Chaque boutique partage son lien ; message prérempli ; groupe de vendeurs | Faible |
| Terrain | Démonstration sur les marchés, création de la boutique sur place | Temps |
| Contenu | Tutoriels vidéo courts « Comment créer sa boutique en 5 min » | Faible |
| Réseaux sociaux | Pages Facebook/TikTok locales, posts produits, témoignages vendeurs | Faible |
| Parrainage | Crédit de commission pour un vendeur qui en amène un autre actif | Variable |
| SEO | Fiches boutiques/produits indexables, blog par catégorie | Moyen |
| Partenariats | Coopératives, associations de commerçants, incubateurs | Relationnel |

## 4. Modèle économique et viabilité

- Revenu principal : commission 10 % (configurable) sur les ventes vérifiées.
- Revenus secondaires possibles (plus tard, jamais cachés) : mise en avant payante, abonnement vendeur premium, services photo, logistique.
- Coûts : hébergement, SMS OTP, frais PSP, support/validation humaine, modération, marketing.
- Point mort simplifié : `commandes_payées_mois × panier_moyen × 10 % > coûts_fixes + coûts_variables`.
  Exemple : panier 10 000 F → 1 000 F/vente. Avec 150 000 F/mois de coûts fixes, il faut **150 ventes payées/mois** pour couvrir les frais (hors SMS et PSP).

## 5. KPIs à suivre dès le jour 1

1. Boutiques actives (au moins 1 produit) et produits par boutique.
2. Clics WhatsApp par boutique et taux de déclaration (ventes déclarées / demandes).
3. Taux de conversion commande → paiement vérifié.
4. Délai médian de validation des preuves.
5. GMV, commission générée, commission encaissée, commission en attente.
6. Taux de remboursement/litige et score de fraude moyen.
7. Rétention vendeur à 30/90 jours.
8. Disponibilité `/health`, intégrité des journaux, écarts de rapprochement.

## 6. Risques de croissance

| Risque | Mitigation |
|---|---|
| Vendeurs contournent la plateforme | Valeur réelle (visibilité, statistiques, commandes, confiance), code de demande, CGU, contrôle anti-fraude. |
| Validation manuelle des preuves devient goulot | Automatiser dès que Wave est actif ; SLA, plusieurs administrateurs, seuils de risque. |
| Fraude sur les preuves | Références uniques, contrôle du montant, rapprochement Balance, audit, bannissement. |
| Qualité des photos/descriptions | Assistant IA local, modèles de fiches, formation. |
| Pannes ou perte de données | Sauvegardes testées, `/health`, restauration documentée, monitoring. |
| Dépendance à WhatsApp | Diversifier : commandes directes, SEO, partage Facebook/TikTok, email/SMS. |
| Conformité | Revue juridique avant ouverture publique et avant activation des paiements. |
