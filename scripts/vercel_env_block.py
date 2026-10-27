"""Genere le bloc de variables a coller dans le dashboard Vercel.

Ce script NE communique PAS avec Vercel : il lit un .env local (ignore par git),
verifie les valeurs et affiche un bloc `CLE=VALEUR` pret a copier-coller dans
Vercel > Settings > Environment Variables (import en masse).

Usage :
    cp .env.example .env          # puis remplir .env
    python3 -m scripts.vercel_env_block
    python3 -m scripts.vercel_env_block --env /chemin/vers/.env
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED = [
    "JAAY_ENV", "JAAY_SECRET_KEY", "JAAY_DATABASE_URL", "JAAY_PUBLIC_BASE_URL",
    "JAAY_COOKIE_SECURE", "JAAY_DEV_SHOW_OTP", "JAAY_DEMO_MODE", "JAAY_AUTO_INIT_DB",
]
OPTIONAL = [
    "JAAY_MEDIA_BACKEND", "JAAY_S3_BUCKET", "JAAY_S3_ENDPOINT", "JAAY_S3_REGION",
    "JAAY_S3_ACCESS_KEY", "JAAY_S3_SECRET_KEY", "JAAY_S3_PUBLIC_BASE_URL", "JAAY_S3_ACL",
    "WAVE_MODE", "WAVE_API_KEY", "OM_MODE", "OM_CLIENT_ID", "WA_CLOUD_TOKEN",
    "SMS_API_KEY", "AI_API_KEY",
]
DEFAULTS = {
    "JAAY_ENV": "production",
    "JAAY_COOKIE_SECURE": "1",
    "JAAY_DEV_SHOW_OTP": "0",
    "JAAY_DEMO_MODE": "1",
    "JAAY_AUTO_INIT_DB": "1",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def main() -> int:
    ap = argparse.ArgumentParser(description="Genere le bloc Vercel depuis un .env local")
    ap.add_argument("--env", default=str(ROOT / ".env"), help="chemin du fichier .env local")
    ap.add_argument("--domain", default="", help="domaine Vercel (remplace JAAY_PUBLIC_BASE_URL)")
    args = ap.parse_args()

    path = Path(args.env)
    values = parse_env(path) if path.exists() else {}
    if not path.exists():
        print(f"[INFO] {path} introuvable : seules les valeurs par defaut seront affichees.")
    if args.domain:
        values["JAAY_PUBLIC_BASE_URL"] = args.domain.rstrip("/")

    errors = []
    if not values.get("JAAY_SECRET_KEY"):
        errors.append("JAAY_SECRET_KEY manquante (python3 -c \"import secrets; print(secrets.token_hex(32))\").")
    elif len(values["JAAY_SECRET_KEY"]) < 32:
        errors.append("JAAY_SECRET_KEY trop courte (32 caracteres minimum).")

    db = values.get("JAAY_DATABASE_URL", "")
    if not db:
        errors.append("JAAY_DATABASE_URL manquante (PostgreSQL Neon/Supabase obligatoire).")
    elif not db.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://")):
        errors.append("JAAY_DATABASE_URL doit etre PostgreSQL (SQLite est refuse sur Vercel).")

    if not values.get("JAAY_PUBLIC_BASE_URL", "").startswith("https://"):
        errors.append("JAAY_PUBLIC_BASE_URL doit etre une URL HTTPS (ex : https://mon-projet.vercel.app).")
    if values.get("JAAY_DEMO_MODE") == "1":
        print("[WARN] JAAY_DEMO_MODE=1 : les identifiants de demo seront publics. A desactiver avant la mise en production reelle.")

    merged = {**DEFAULTS, **{k: v for k, v in values.items() if v}}
    print("\n" + "=" * 68)
    print("BLOC A COLLER DANS VERCEL > Settings > Environment Variables")
    print("(cocher Production ; ne jamais committer ce bloc)")
    print("=" * 68)
    for key in REQUIRED:
        print(f"{key}={merged.get(key, '')}")
    printed_optional = False
    for key in OPTIONAL:
        if merged.get(key):
            if not printed_optional:
                print("\n# Optionnels (renseignes dans votre .env)")
                printed_optional = True
            print(f"{key}={merged[key]}")
    print("=" * 68)

    if errors:
        print("\n[ERREURS] Corrigez votre .env avant de coller le bloc :")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("\n[OK] Bloc valide. Apres collage dans Vercel, REDEPLOYEZ : vercel --prod")
    print("     Puis : python3 -m scripts.vercel_diagnose https://VOTRE-PROJET.vercel.app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
