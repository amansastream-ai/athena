"""CLI d'amorcage : python -m scripts.seed --demo | --admin | --reset"""
from __future__ import annotations

import argparse

from app.config import settings
from app.db import Base, engine
from app.seed import bootstrap_admin, init_db, seed_demo


def main() -> None:
    parser = argparse.ArgumentParser(description="Amorcage de la base Jaay")
    parser.add_argument("--demo", action="store_true", help="Cree les comptes et produits de demonstration")
    parser.add_argument("--force", action="store_true", help="Autorise --demo en production (uniquement avec JAAY_DEMO_MODE=1)")
    parser.add_argument("--admin", action="store_true", help="Cree l'administrateur depuis les variables JAAY_BOOTSTRAP_ADMIN_*")
    parser.add_argument("--reset", action="store_true", help="SUPPRIME toutes les tables puis les recree (destructif)")
    args = parser.parse_args()

    if args.reset:
        if settings.is_production:
            raise SystemExit("Refuse : --reset est interdit en production.")
        confirm = input("Toutes les donnees seront perdues. Tapez RESET : ")
        if confirm.strip() != "RESET":
            raise SystemExit("Annule.")
        Base.metadata.drop_all(engine)

    init_db()
    print("[seed] schema pret (migrations ou create_all).")
    if args.admin:
        bootstrap_admin()
    if args.demo:
        if settings.is_production and not (args.force and settings.demo_mode):
            raise SystemExit(
                "Refus : --demo est interdit en production sauf avec --force ET "
                "JAAY_DEMO_MODE=1 (deploiement de demonstration uniquement)."
            )
        seed_demo()


if __name__ == "__main__":
    main()
