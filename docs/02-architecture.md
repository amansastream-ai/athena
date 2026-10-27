# Architecture technique justifiée

## 1. Comparaison des options (avant choix)

| Critère | FastAPI + Jinja2 + PostgreSQL (retenu) | Next.js / NestJS + PostgreSQL | Laravel + MySQL | Microservices + Kubernetes | MongoDB + Node |
|---|---|---|---|---|---|
| Fiabilité | ★★★★★ un seul processus, transactions SQL | ★★★★ | ★★★★ | ★★★ (pannes réseau) | ★★★ |
| Sécurité financière | ★★★★★ contraintes, triggers, hash chain | ★★★★ (à construire) | ★★★★ | ★★★ | ★★ (pas de contraintes fortes) |
| Coût d'hébergement | ★★★★★ 1 VPS ~10–25 €/mois | ★★★ (Node + build + CDN) | ★★★★ | ★ (plusieurs nœuds) | ★★★ |
| Maintenance | ★★★★★ 1 dépôt, 1 déploiement | ★★★ | ★★★★ | ★★ | ★★★ |
| Vivier développeurs (SN) | ★★★★ Python/JS courants | ★★★★ JS | ★★★★ PHP | ★★ | ★★★ |
| Montée en charge | ★★★★ jusqu'à ~100k produits, puis recherche dédiée | ★★★★ | ★★★★ | ★★★★★ | ★★★★ |
| Intégration paiements SN | ★★★★★ adaptateurs HTTP simples | ★★★★ | ★★★★ | ★★★★ | ★★★★ |
| Compatibilité domaine/hébergement | ★★★★★ standard WSGI/ASGI partout | ★★★ (Node requis) | ★★★★ | ★★ | ★★★ |

**Décision :** monolithe modulaire Python/FastAPI, rendu serveur, SQLAlchemy, SQLite en dev / PostgreSQL en prod. L'objectif est de livrer une base fiable et peu coûteuse, pas de démontrer une architecture à la mode. Les interfaces (`PaymentProvider`, `MediaBackend`, `SmsProvider`, `AiProvider`) permettent de remplacer des briques sans réécrire le domaine.

## 2. Vue d'ensemble

```
                ┌──────────────────────── navigateur mobile ───────────────────────┐
                │  HTML/CSS/JS locaux (aucun CDN) · boutons wa.me · formulaires CSRF │
                └───────────────────────────────┬──────────────────────────────────┘
                                                │ HTTPS
                                        ┌───────▼────────┐
                                        │  nginx (TLS)   │
                                        └───────┬────────┘
                                                │
   ┌───────────────────────────────────────────▼───────────────────────────────────────┐
   │                         Application FastAPI (ASGI, 2 workers)                     │
   │  ┌────────────┐  ┌──────────────┐  ┌───────────────┐  ┌────────────────────────┐ │
   │  │ Vues HTML  │  │ API JSON     │  │ Middleware    │  │ Webhooks entrants      │ │
   │  │ Jinja2     │  │ /api/*       │  │ CSRF/CSP/IP  │  │ /webhooks/{wave,om,wa} │ │
   │  └─────┬──────┘  └──────┬───────┘  └──────┬────────┘  └───────────┬────────────┘ │
   │        └──────────┬──────┴─────────────────┴──────────────────────┬┘              │
   │                   ▼            COUCHE SERVICES (domaine)            ▼              │
   │   orders · ledger/commissions · payments · fraud · whatsapp · notifications      │
   │   audit · registry · stats · ai · media · sms                                   │
   │                   │                                                               │
   │                   ▼                                                               │
   │        SQLAlchemy 2 ORM + Alembic (migrations versionnées)                       │
   └───────────────────┬───────────────────────────────┬───────────────────────────────┘
                       ▼                               ▼
        ┌──────────────────────────┐     ┌─────────────────────────────┐
        │ PostgreSQL 16 (prod)     │     │ Stockage médias              │
        │ SQLite (dev)             │     │ local → S3/R2 (interface)   │
        │ triggers append-only     │     │ WebP redimensionné          │
        └──────────────────────────┘     └─────────────────────────────┘
                       │
        ┌──────────────┴───────────────┐
        ▼                              ▼
  Fournisseurs externes        Notifications sortantes
  Wave / Orange / SMS / IA     SMTP · webhook admin · in-app
```

## 3. Décisions d'architecture (ADR)

**ADR-01 — Rendu serveur plutôt que SPA.** Les fiches boutiques/produits doivent être indexables et s'afficher vite sur un réseau 3G. Le HTML est rendu par Jinja2, le JS est progressif (aucune fonctionnalité critique n'en dépend). Une API JSON existe pour un futur client mobile.

**ADR-02 — Montants entiers.** Tous les montants sont des `INTEGER` en francs CFA. Aucun `float` n'entre dans un calcul financier. Le taux est en points de base, la formule est `(montant * bps + 5000) // 10000`.

**ADR-03 — Journal financier comme source de vérité.** Les statistiques ne sont pas des compteurs mis à jour : elles sont des `SUM()` sur `ledger_entries`, table en écriture seule protégée par triggers SQL et chaînée par SHA-256. Toute annulation produit une écriture négative.

**ADR-04 — Idempotence par contraintes.** `UNIQUE(commissions.order_id)`, `UNIQUE(payments.provider, provider_reference)`, `UNIQUE(webhook_events.provider, event_id)`, `UNIQUE(orders.idempotency_key)`. Les doublons sont absorbés par la base, pas par un flag applicatif.

**ADR-05 — Adaptateurs de paiement désactivés par défaut.** Les classes Wave/Orange Money contiennent de vrais appels HTTP, mais `provider_capabilities()` n'annonce un fournisseur actif que si les secrets ET l'activation admin sont présents. Le parcours manuel reste la solution de repli.

**ADR-06 — Fail-closed.** Si la re-vérification distante d'un paiement est impossible (URL de statut absente, fournisseur injoignable), le paiement n'est **jamais** marqué comme confirmé.

**ADR-07 — Environnements séparés.** `JAAY_ENV=dev|test|staging|production`. Les données de démonstration ne sont créées qu'en dev/test. En production, `JAAY_SECRET_KEY` est obligatoire et le code OTP n'est jamais renvoyé à l'interface.

## 4. Montée en charge (chemin réaliste)

| Étape | Volume | Action |
|---|---|---|
| MVP | < 10 000 produits, < 100 cmd/jour | 1 VPS, 2 workers, SQLite ou PostgreSQL, recherche `ILIKE` indexée, médias locaux. |
| Croissance | 100 000 produits | PostgreSQL plein texte (`tsvector`) ou Meilisearch, index composites, cache des pages catalogue, CDN pour les médias. |
| Scale-up | > 1 M produits, > 10 000 cmd/jour | File de tâches (Celery/Arq + Redis) pour notifications/webhooks, réplicas en lecture, partitionnement des commandes par année, objets S3/R2, observabilité (Sentry + Prometheus), `pg_advisory_xact_lock` pour la chaîne de hachage multi-instances. |
| International | Multi-pays | Table `countries`/`currencies`, taux de commission par pays, conversion des montants, plusieurs comptes marchands (Wave SN et CI sont des entités distinctes), i18n. |

**Limite connue du MVP :** la chaîne de hachage du journal est sérialisée par un verrou applicatif dans un seul processus. En multi-workers, utiliser un verrou advisory PostgreSQL — c'est documenté, pas encore implémenté (voir `docs/11-etat-des-lieux.md`).

## 5. Composants et responsabilités

| Dossier | Rôle |
|---|---|
| `app/application.py` | `configure_app(app)` : middlewares de sécurité, montages statiques, routers, `/health`, lifespan. |
| `index.py` | Entrée Vercel : `app = FastAPI(...)` littéral + configuration. |
| `app/main.py` | Ré-export de l'instance unique (`uvicorn app.main:app`). |
| `app/diagnostic.py` | Application de secours 503 quand une variable d'environnement manque. |
| `app/config.py` | Configuration par variables d'environnement, aucune clé en dur. |
| `app/db.py` | Moteur SQLAlchemy, sessions, triggers d'immutabilité. |
| `app/models.py` | 26 tables du domaine. |
| `app/schemas.py` | Validation Pydantic des entrées. |
| `app/security.py` | Hachage, sessions, CSRF, OTP, rate limiting, RBAC. |
| `app/services/` | Logique métier (commissions, commandes, paiements, fraude, WhatsApp, IA, stats, audit). |
| `app/api/` | Endpoints JSON + webhooks. |
| `app/web.py` | Routes HTML (public, vendeur, admin). |
| `app/templates/` + `static/` | Interface mobile-first sans dépendance réseau. |
| `migrations/` | Migrations Alembic versionnées. |
| `tests/` | 59 tests automatisés. |
| `scripts/`, `ops/` | Amorçage, sauvegarde, restauration, intégrité, Docker, nginx. |

## 6. Observabilité et exploitation

- `GET /health` pour les sondes ; `X-Request-Id` sur chaque réponse.
- `scripts/verify_integrity.py` au démarrage et après restauration.
- Journaux d'audit pour toute action sensible ; événements webhook conservés avec leur résultat.
- Sauvegarde quotidienne PostgreSQL (`pg_dump`) + médias ; restauration testée mensuellement sur une copie.
- À ajouter en production : Sentry (erreurs), UptimeRobot/Better Stack (disponibilité), Grafana (métriques), rotation des logs, alertes sur `commission_outstanding` anormal.
