"""Controle pre-deploiement Vercel : variables, base PostgreSQL, fichiers requis.

Usage :
    # avec les memes variables que dans le dashboard Vercel
    export JAAY_SECRET_KEY=... JAAY_DATABASE_URL=... JAAY_PUBLIC_BASE_URL=...
    python3 -m scripts.vercel_preflight
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOLD = "\033[1m"
RESET = "\033[0m"


def line(status: str, label: str, detail: str = "") -> None:
    colors = {"OK": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m"}
    print(f"  [{colors[status]}{status}{RESET}] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print(f"{BOLD}Jaay — pre-deploiement Vercel{RESET}\n")
    blockers = 0
    warnings = 0

    # Importer la configuration apres les controles bruts. load_settings() ne
    # leve plus d'exception : il collecte settings.config_error (voir plus bas).
    try:
        from app.config import settings
        config_error = settings.config_error or None
        if config_error:
            line("FAIL", "configuration applicative", config_error[:220])
            blockers += 1
    except Exception as exc:  # pragma: no cover
        settings = None
        config_error = f"{type(exc).__name__}: {exc}"
        line("FAIL", "configuration applicative", config_error)
        blockers += 1

    # 1. Secrets bruts (avant import de la config, qui exige la cle en production).
    secret = os.environ.get("JAAY_SECRET_KEY", "")
    if len(secret) >= 32:
        line("OK", "JAAY_SECRET_KEY", f"{len(secret)} caracteres")
    else:
        line("FAIL", "JAAY_SECRET_KEY", "absente ou < 32 caracteres. Generez : python3 -c \"import secrets; print(secrets.token_hex(32))\"")
        blockers += 1

    db_url = os.environ.get("JAAY_DATABASE_URL", "")
    if db_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://")):
        from app.db_network import is_direct_supabase, is_supabase_pooler, normalize_database_url
        normalized = normalize_database_url(db_url)
        if is_direct_supabase(normalized) and not os.environ.get("JAAY_ALLOW_IPV6_DB"):
            line("FAIL", "JAAY_DATABASE_URL",
                 "connexion DIRECTE Supabase (IPv6 seul) : Vercel est IPv4. Utilisez le pooler "
                 "...pooler.supabase.com (session 5432 / transaction 6543). Voir FIX-SUPABASE-IPV6.md")
            blockers += 1
        elif is_supabase_pooler(normalized):
            line("OK", "JAAY_DATABASE_URL",
                 "pooler Supabase IPv4 (port %s)" % (normalized.split("@")[-1].split("/")[0]))
        else:
            line("OK", "JAAY_DATABASE_URL", "PostgreSQL")
    elif db_url.startswith("sqlite"):
        line("FAIL", "JAAY_DATABASE_URL", "SQLite est incompatible avec Vercel (disque ephemeral)")
        blockers += 1
    else:
        line("FAIL", "JAAY_DATABASE_URL", "absente. Creez une base Neon/Supabase et renseignez-la")
        blockers += 1

    for name in ("JAAY_ENV", "JAAY_PUBLIC_BASE_URL"):
        val = os.environ.get(name, "")
        if name == "JAAY_ENV" and val != "production":
            line("WARN", name, f"={val or '(vide)'} — mettre JAAY_ENV=production sur Vercel")
            warnings += 1
        elif name == "JAAY_PUBLIC_BASE_URL" and not val.startswith("https://"):
            line("WARN", name, "absente ou non HTTPS — liens de partage et webhooks incomplets")
            warnings += 1
        else:
            line("OK", name, val)

    # 2. Fichiers de configuration du deploiement.
    for f in ("index.py", "vercel.json", "requirements.txt", ".python-version"):
        if (ROOT / f).exists():
            line("OK", f"fichier {f}")
        else:
            line("FAIL", f"fichier {f}", "manquant")
            blockers += 1

    # 3. Connexion reelle a la base.
    if settings is None:
        line("FAIL", "connexion a la base", "configuration indisponible")
    else:
        try:
            from sqlalchemy import create_engine, text
            args = {} if settings.database_url.startswith("sqlite") else {"connect_timeout": 8}
            with create_engine(settings.database_url, connect_args=args,
                               pool_pre_ping=True).connect() as conn:
                conn.execute(text("SELECT 1"))
            line("OK", "connexion a la base", "SELECT 1 reussi")
        except Exception as exc:
            line("FAIL", "connexion a la base", f"{type(exc).__name__}: {str(exc)[:160]}")
            blockers += 1

    # 4. Options d'exploitation.
    if settings is not None:
        if settings.demo_mode and settings.is_production:
            line("WARN", "JAAY_DEMO_MODE=1", "comptes de demonstration PUBLICS : a desactiver avant tout usage reel")
            warnings += 1
        else:
            line("OK", "mode demonstration", "actif (dev)" if settings.demo_mode else "desactive")

        if settings.media_is_object_storage:
            line("OK", "stockage objet", settings.s3_bucket)
        else:
            line("WARN", "JAAY_MEDIA_BACKEND", "local : les uploads de photos echoueront sur Vercel. Configurez S3/R2.")
            warnings += 1

    secure_cookies = (settings.cookie_secure if settings is not None
                      else os.environ.get("JAAY_COOKIE_SECURE") == "1")
    if secure_cookies or os.environ.get("JAAY_ENV") == "production":
        line("OK", "cookies securises", "HTTPS (SameSite=None + Partitioned geres par l'application)")
    else:
        line("WARN", "JAAY_COOKIE_SECURE", "mettre 1 en production")
        warnings += 1

    if shutil.which("vercel"):
        line("OK", "Vercel CLI", "installe")
    else:
        line("WARN", "Vercel CLI", "absent — npm i -g vercel (ou deployez via GitHub)")
        warnings += 1

    print()
    if blockers:
        print(f"{BOLD}{blockers} blocage(s) a corriger avant de deployer.{RESET}")
        return 1
    print(f"{BOLD}Pret a deployer.{RESET} {warnings} avertissement(s).")
    print("Commande : vercel --prod   (ou import GitHub sur vercel.com/new)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
