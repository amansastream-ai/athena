"""Moteur de base de donnees, sessions et contraintes d'immutabilite.

SQLite pour le developpement local, PostgreSQL pour la production (meme modele SQLAlchemy).
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings
from .db_network import use_null_pool


class Base(DeclarativeBase):
    pass


def _is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")


connect_args: dict = {"check_same_thread": False, "timeout": 20} if _is_sqlite() else {}

# Sur Vercel, chaque instance serverless ouvre son propre pool : garder des
# petits pools evite d'epuiser les connexions d'un PostgreSQL manag (Neon,
# Supabase). pool_recycle < 5 min contourne la fermeture des connexions inactives.
#
# Exception : le Transaction pooler Supabase (port 6543) fait DEJA le pooling.
# Supabase recommande alors NullPool cote SQLAlchemy, sinon on obtient un double
# pool, des commandes de session non supportees et une exhaustion des connexions.
if _is_sqlite():
    _pool_kwargs: dict = {}
elif use_null_pool(settings.database_url):
    _pool_kwargs = {"poolclass": NullPool}
else:
    _pool_kwargs = {
        "pool_size": 3,
        "max_overflow": 2,
        "pool_recycle": 280,
        "pool_timeout": 10,
    }

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    echo=False,
    future=True,
    **_pool_kwargs,
)

if _is_sqlite():

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver level
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


_SQLITE_TRIGGERS = [
    "CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_entries "
    "BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_entries "
    "BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_logs "
    "BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END",
]

_POSTGRES_TRIGGERS = [
    "CREATE OR REPLACE FUNCTION jaay_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN RAISE EXCEPTION 'append-only table'; END $$",
    "DROP TRIGGER IF EXISTS ledger_no_update ON ledger_entries",
    "CREATE TRIGGER ledger_no_update BEFORE UPDATE OR DELETE ON ledger_entries "
    "FOR EACH ROW EXECUTE FUNCTION jaay_append_only()",
    "DROP TRIGGER IF EXISTS audit_no_update ON audit_logs",
    "CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION jaay_append_only()",
]


def ensure_append_only_protection() -> None:
    """Empeche la modification du journal financier et du journal d'audit, meme en SQL direct."""
    statements = _SQLITE_TRIGGERS if _is_sqlite() else _POSTGRES_TRIGGERS
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
