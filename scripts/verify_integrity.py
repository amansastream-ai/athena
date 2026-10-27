"""Verifie les chaines de hachage financieres et d'audit. Code de sortie non nul si corruption."""
from __future__ import annotations

import sys

from app.db import SessionLocal
from app.services import audit, ledger


def main() -> int:
    with SessionLocal() as db:
        fin = ledger.financial_stats(db)
        ok_l, msg_l = ledger.verify_chain(db)
        ok_a, msg_a = audit.verify_chain(db)
        print(f"Journal financier : {fin['ledger_entries']} ecritures — chaine {'OK' if ok_l else 'ROMPUE'} ({msg_l})")
        print(f"Journal d'audit   : chaine {'OK' if ok_a else 'ROMPUE'} ({msg_a})")
        print(f"GMV {fin['gmv_xof']} F | commission generee {fin['commission_generated_xof']} F | "
              f"en attente {fin['commission_outstanding_xof']} F")
        return 0 if ok_l and ok_a else 1


if __name__ == "__main__":
    sys.exit(main())
