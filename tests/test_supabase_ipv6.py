"""Regression : Vercel (IPv4 seulement) + connexion DIRECTE Supabase (IPv6).

Erreur observee en production ::

    OperationalError: (psycopg2.OperationalError) connection to server at
    "db.gxybuhczjfzpjvaduhqq.supabase.co" (2a05:...), port 5432 failed:
    Cannot assign requested address

Le correctif : utiliser le pooler partage Supabase (IPv4), et faire remonter
une consigne claire des le demarrage au lieu d'une erreur reseau obscure.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from app.config import ROOT
from app.db_network import (
    POOLER_HINT,
    config_error,
    database_host,
    diagnostics,
    is_direct_supabase,
    is_supabase_pooler,
    network_hint,
    normalize_database_url,
    use_null_pool,
)

REF = "gxybuhczjfzpjvaduhqq"
PASSWORD = "MotDePasseTresSecret123!"
DIRECT = f"postgres://postgres:{PASSWORD}@db.{REF}.supabase.co:5432/postgres"
SESSION_POOLER = (
    f"postgresql://postgres.{REF}:{PASSWORD}@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
)
TRANSACTION_POOLER = (
    f"postgresql://postgres.{REF}:{PASSWORD}@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
)
IPV6_ERROR = (
    f'connection to server at "db.{REF}.supabase.co" '
    "(2a05:d018:1b65:3000:974:b421:ac47:4ddd), port 5432 failed: "
    "Cannot assign requested address"
)


# ------------------------------------------------------------------ detection

def test_direct_supabase_host_is_ipv6_only():
    assert is_direct_supabase(DIRECT) is True
    assert is_supabase_pooler(DIRECT) is False
    assert database_host(DIRECT) == f"db.{REF}.supabase.co"


def test_pooler_hosts_are_ipv4_compatible():
    assert is_supabase_pooler(SESSION_POOLER) is True
    assert is_supabase_pooler(TRANSACTION_POOLER) is True
    assert is_direct_supabase(SESSION_POOLER) is False
    assert is_direct_supabase(TRANSACTION_POOLER) is False


def test_the_real_failing_host_has_no_ipv4_record():
    """Le hote de l'incident ne publie effectivement que de l'IPv6."""
    info = diagnostics(DIRECT, timeout=3)
    assert info["direct_supabase_ipv6"] is True
    assert info["sslmode"] == "require"
    if info["dns_error"]:
        pytest.skip(f"DNS indisponible dans cet environnement : {info['dns_error']}")
    assert info["ipv4"] == []
    assert info["ipv6"], "une adresse IPv6 doit etre resolue"
    assert "pooler" in (info["hint"] or "")


# ------------------------------------------------------------- normalisation

def test_url_normalization_keeps_secret_and_adds_sslmode():
    url = normalize_database_url(DIRECT)
    assert url.startswith("postgresql+psycopg2://")
    assert "sslmode=require" in url
    assert PASSWORD in url                       # aucune perte du secret
    assert f"db.{REF}.supabase.co:5432" in url  # ni de l'hote


def test_url_normalization_strips_prisma_only_options():
    url = normalize_database_url(TRANSACTION_POOLER + "?pgbouncer=true&connection_limit=1")
    assert "pgbouncer" not in url
    assert "connection_limit" not in url
    assert "sslmode=require" in url
    assert ":6543/postgres" in url


def test_sqlite_url_is_left_untouched():
    url = "sqlite:////tmp/jaay-test.db"
    assert normalize_database_url(url) == url
    assert is_direct_supabase(url) is False


# ------------------------------------------------------------------- pooling

def test_null_pool_only_for_transaction_pooler():
    assert use_null_pool(TRANSACTION_POOLER) is True
    assert use_null_pool(SESSION_POOLER) is False
    assert use_null_pool(DIRECT) is False


def test_engine_uses_null_pool_with_transaction_pooler():
    """Isolation par sous-processus : create_engine est lazy, aucune connexion."""
    code = (
        "import json\n"
        "from app.db import engine\n"
        "print(json.dumps({'pool': type(engine.pool).__name__}))\n"
    )
    payload = _run_in_vercel_env(TRANSACTION_POOLER, code)
    assert payload["pool"] == "NullPool"


# --------------------------------------------------------------- diagnostics

def test_network_hint_recognises_ipv6_egress_failure():
    assert network_hint(IPV6_ERROR) == POOLER_HINT
    assert network_hint("Network is unreachable") == POOLER_HINT
    assert network_hint("syntax error at or near SELECT") is None


def test_config_error_blocks_direct_supabase_only_on_vercel():
    assert "pooler" in (config_error(DIRECT, on_vercel=True) or "")
    assert config_error(DIRECT, on_vercel=False) is None
    assert config_error(DIRECT, on_vercel=True, allow_ipv6=True) is None
    assert config_error(SESSION_POOLER, on_vercel=True) is None
    assert config_error(TRANSACTION_POOLER, on_vercel=True) is None


def test_diag_reports_database_layout_without_any_secret(client, monkeypatch):
    monkeypatch.setattr(
        "app.db_network._resolve_cached",
        lambda host, port, timeout: {"ipv4": [], "ipv6": ["2a05::1"], "dns_error": None},
    )
    monkeypatch.setenv("JAAY_DATABASE_URL", DIRECT)
    body = client.get("/__diag").json()
    db = body["database"]
    assert db["direct_supabase_ipv6"] is True
    assert db["ipv4"] == [] and db["ipv6"] == ["2a05::1"]
    assert "pooler" in (db["hint"] or "")
    raw = json.dumps(body)
    assert PASSWORD not in raw
    assert "postgres://" not in raw


def test_health_surfaces_pooler_hint_when_connection_fails(client, monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise OSError(IPV6_ERROR)

    monkeypatch.setattr("app.application.engine", BrokenEngine())
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["database"].startswith("error:")
    assert "pooler" in (body["database_hint"] or "")
    assert PASSWORD not in json.dumps(body)


def test_runtime_middleware_labels_ipv6_failures():
    import index

    async def boom(_request):
        raise OSError(IPV6_ERROR)

    response = asyncio.run(index._runtime_error_middleware(None, boom))
    body = json.loads(response.body)
    assert body["status"] == "runtime_error"
    assert body["diagnosis"] == "ipv6_egress_unavailable"
    assert "pooler" in body["network_hint"]


# ----------------------------------------------------------- bout en bout Vercel

def _vercel_env(url: str) -> dict:
    env = dict(os.environ)
    env.update({
        "VERCEL": "1", "JAAY_ENV": "production", "JAAY_SECRET_KEY": "x" * 64,
        "JAAY_DATABASE_URL": url, "JAAY_PUBLIC_BASE_URL": "https://athena-market.vercel.app",
    })
    env.pop("JAAY_ALLOW_IPV6_DB", None)
    return env


def _run_in_vercel_env(url: str, code: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), env=_vercel_env(url),
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_direct_supabase_on_vercel_returns_actionable_503():
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "import index\n"
        "c = TestClient(index.app)\n"
        "r = c.get('/')\n"
        "d = c.get('/__diag')\n"
        "print(json.dumps({'code': r.status_code, 'body': r.json(), "
        "'diag': d.json()}))\n"
    )
    payload = _run_in_vercel_env(DIRECT, code)
    assert payload["code"] == 503
    body = payload["body"]
    assert body["status"] == "misconfigured"
    assert "pooler" in body["error"]
    assert any("SUPABASE" in step or "pooler" in step for step in body["how_to_fix"])
    assert PASSWORD not in json.dumps(body)
    assert payload["diag"]["database"]["direct_supabase_ipv6"] is True
    assert "pooler" in (payload["diag"]["config_error"] or "")


def test_pooler_url_on_vercel_does_not_trigger_config_error():
    code = (
        "import json\n"
        "from app.config import settings\n"
        "from app.db_network import use_null_pool\n"
        "print(json.dumps({'error': settings.config_error, "
        "'null_pool': use_null_pool(settings.database_url), 'url': settings.database_url}))\n"
    )
    payload = _run_in_vercel_env(TRANSACTION_POOLER, code)
    assert payload["error"] == ""
    assert payload["null_pool"] is True
    assert ":6543/postgres" in payload["url"]
    assert "sslmode=require" in payload["url"]


def test_preflight_refuses_direct_supabase_url():
    env = _vercel_env(DIRECT)
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.vercel_preflight"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )
    assert "IPv6 seul" in proc.stdout or "pooler" in proc.stdout
    assert "FIX-SUPABASE-IPV6.md" in proc.stdout


def test_diagnose_script_mentions_pooler_for_ipv6_diag():
    """Le script doit lire la bonne cle `config_error` de /__diag."""
    source = (ROOT / "scripts" / "vercel_diagnose.py").read_text()
    assert 'diag_info.get("config_error")' in source
    assert "direct_supabase_ipv6" in source
