"""Robustesse de la connexion (iframe / cookies bloques) et preparation Vercel."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, settings
from app.main import app
from tests.conftest import make_order

HTTPS_HEADERS = {
    "X-Forwarded-Proto": "https",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Dest": "iframe",
}


def test_login_works_without_any_cookie(client, data):
    """Regression : le POST /connexion ne doit plus exiger le cookie jaay_csrf."""
    c = TestClient(app, base_url="https://8000-preview.e2b.app")
    page = c.get("/connexion", headers=HTTPS_HEADERS)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    assert token.startswith("eyJ")  # jeton HMAC signe, pas une valeur de cookie

    c.cookies.clear()  # navigateur qui bloque les cookies tiers
    r = c.post("/connexion", data={
        "identifiant": "v1@test.sn", "mot_de_passe": "VendeurTest2026!",
        "csrf_token": token,
    }, headers=HTTPS_HEADERS, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    assert r.headers["location"] == "/vendeur"


def test_session_cookie_is_cross_site_capable(client, data):
    c = TestClient(app, base_url="https://8000-preview.e2b.app")
    token = re.search(r'name="csrf-token" content="([^"]+)"',
                      c.get("/connexion", headers=HTTPS_HEADERS).text).group(1)
    r = c.post("/connexion", data={
        "identifiant": "v1@test.sn", "mot_de_passe": "VendeurTest2026!",
        "csrf_token": token,
    }, headers=HTTPS_HEADERS, follow_redirects=False)
    cookies = r.headers.get_list("set-cookie")
    session = next((h for h in cookies if "jaay_sid" in h), "")
    assert "SameSite=none" in session
    assert "Secure" in session
    assert "Partitioned" in session  # CHIPS, meme sur Python < 3.14
    assert "HttpOnly" in session
    c.cookies.set("jaay_sid", r.cookies.get("jaay_sid"))
    assert c.get("/vendeur", headers=HTTPS_HEADERS, follow_redirects=False).status_code == 200


def test_tampered_signed_csrf_token_is_rejected(client, data):
    c = TestClient(app, base_url="https://8000-preview.e2b.app")
    page = c.get("/connexion", headers=HTTPS_HEADERS)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    r = c.post("/connexion", data={
        "identifiant": "v1@test.sn", "mot_de_passe": "VendeurTest2026!",
        "csrf_token": token[:-3] + "abc",
    }, headers=HTTPS_HEADERS, follow_redirects=False)
    assert r.status_code == 303
    assert "erreur=session_expiree" in r.headers["location"]


def test_csrf_error_message_is_human_readable(client, data):
    html = client.get("/connexion?erreur=session_expiree").text
    assert "session de sécurité a expiré" in html
    assert "acces_refuse" not in html


def test_health_reports_database_status(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert "demo_mode" in body and "runtime" in body
    assert "password" not in json.dumps(body).lower()


def test_health_exposes_only_variable_presence_never_values(client):
    """Diagnostic a distance : booleens de presence, aucune valeur de secret."""
    body = client.get("/health").json()
    presence = body.get("env_presence", {})
    assert "JAAY_SECRET_KEY" in presence
    assert "JAAY_DATABASE_URL" in presence
    assert all(isinstance(v, bool) for v in presence.values())
    raw = json.dumps(body)
    assert "test-secret-key-do-not-use" not in raw
    assert "postgresql+psycopg2://" not in raw


def test_vercel_entrypoint_exports_the_same_app():
    import index
    assert index.app is app


def test_vercel_config_is_valid():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["framework"] == "fastapi"
    assert "index.py" in cfg["functions"]
    assert cfg["functions"]["index.py"]["maxDuration"] >= 30
    assert "app/static" not in cfg["functions"]["index.py"]["excludeFiles"]
    assert (ROOT / ".python-version").read_text().strip() in {"3.12", "3.13", "3.14"}
    assert (ROOT / "requirements.txt").exists()


def test_vercel_reports_sqlite_as_config_error_without_breaking_import():
    """La config ne doit PLUS lever a l'import (sinon Vercel ne bundle pas app/)."""
    env = dict(os.environ)
    env.update({
        "VERCEL": "1", "JAAY_ENV": "production",
        "JAAY_SECRET_KEY": "x" * 64,
        "JAAY_DATABASE_URL": "sqlite:////tmp/jaay-vercel-test.db",
    })
    proc = subprocess.run(
        [sys.executable, "-c",
         "import json; from app.config import settings; "
         "print(json.dumps({'error': settings.config_error, 'url': settings.database_url}))"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout.strip())
    assert "PostgreSQL sur Vercel" in body["error"]
    assert body["url"].startswith("sqlite")


def test_postgres_url_normalization():
    env = dict(os.environ)
    env.pop("VERCEL", None)
    env.pop("JAAY_ON_VERCEL", None)
    env.update({
        "JAAY_ENV": "dev", "JAAY_DATABASE_URL": "postgres://u:p@h/db?sslmode=require",
        "JAAY_SECRET_KEY": "x" * 64,
    })
    proc = subprocess.run(
        [sys.executable, "-c", "from app.config import settings; print(settings.database_url)"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "postgresql+psycopg2://u:p@h/db?sslmode=require"


def test_repository_contains_no_real_secret():
    """Le controle pre-push doit passer sur le depot livre."""
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.check_before_push"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "depot publiable" in proc.stdout


def test_main_shim_exports_the_single_application():
    import index
    from app.main import app as shim_app
    assert shim_app is index.app
    paths = {getattr(route, "path", "") for route in index.app.routes}
    assert "/health" in paths
    assert any(p.startswith("/static") for p in paths)


def test_vercel_entrypoint_has_literal_fastapi_assignment():
    """Regression FASTAPI_ENTRYPOINT_NOT_FOUND : Vercel analyse index.py par AST."""
    import ast

    tree = ast.parse((ROOT / "index.py").read_text())
    found = False
    for node in tree.body:  # niveau module uniquement, pas dans un try
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "app" for t in node.targets):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name == "FastAPI":
            found = True
    assert found, "index.py doit contenir `app = FastAPI(...)` au niveau du module pour Vercel"


def test_missing_config_returns_explicit_diagnostic_instead_of_500():
    """Sans JAAY_SECRET_KEY en production, Vercel doit afficher la cause."""
    env = dict(os.environ)
    for key in ("JAAY_SECRET_KEY", "JAAY_DATABASE_URL", "JAAY_PUBLIC_BASE_URL"):
        env.pop(key, None)
    env.update({"VERCEL": "1", "JAAY_ENV": "production"})
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "import index\n"
        "r = TestClient(index.app).get('/health')\n"
        "print(json.dumps({'code': r.status_code, 'body': r.json()}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["code"] == 503
    body = payload["body"]
    assert body["status"] == "misconfigured"
    assert "JAAY_SECRET_KEY" in body["missing_variables"]
    assert body["runtime"] == "vercel"
    assert any("vercel --prod" in step for step in body["how_to_fix"])
    assert "AdminJaay2026" not in json.dumps(body)


def test_diag_probe_reports_only_presence_never_values(client):
    body = client.get("/__diag").json()
    assert body["status"] == "diagnostic"
    assert body["application_import"] == "ok"
    assert all(isinstance(v, bool) for v in body["env_presence"].values())
    assert "test-secret-key-do-not-use" not in json.dumps(body)


def test_diag_probe_survives_broken_configuration():
    """La sonde doit repondre 200 meme quand app.application ne peut pas importer."""
    env = dict(os.environ)
    for key in ("JAAY_SECRET_KEY", "JAAY_DATABASE_URL", "JAAY_PUBLIC_BASE_URL"):
        env.pop(key, None)
    env.update({"VERCEL": "1", "JAAY_ENV": "production"})
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "import index\n"
        "r = TestClient(index.app).get('/__diag')\n"
        "print(json.dumps({'code': r.status_code, 'body': r.json()}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["code"] == 200
    body = payload["body"]
    # L'import reussit desormais (config non bloquante) : l'erreur est exposee
    # par config_error et l'application repond 503 via configure_diagnostic().
    assert body["application_import"] == "ok"
    assert "JAAY_SECRET_KEY" in (body["config_error"] or "")
    assert body["env_presence"]["JAAY_SECRET_KEY"] is False
    assert body["package_present"] is True


def test_index_has_static_top_level_import_of_app_package():
    """Regression ModuleNotFoundError: No module named 'app' sur Vercel."""
    import ast

    tree = ast.parse((ROOT / "index.py").read_text())
    static_imports = []
    for node in tree.body:  # niveau module, hors de tout try
        if isinstance(node, ast.ImportFrom) and node.module:
            static_imports.append(node.module)
        elif isinstance(node, ast.Import):
            static_imports.extend(alias.name for alias in node.names)
    assert "app.application" in static_imports, \
        "L'import de app.application doit etre au niveau du module pour etre trace par Vercel"


def test_vercel_config_forces_app_package_into_function_bundle():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    for entry in cfg["functions"].values():
        assert "app/" in entry.get("includeFiles", "")
        assert "app/" not in entry.get("excludeFiles", "")


def test_vercel_task_layout_only_needs_index_and_app(tmp_path):
    """Simule /var/task : uniquement index.py + app/, depuis un autre cwd."""
    import shutil

    shutil.copy2(ROOT / "index.py", tmp_path / "index.py")
    shutil.copytree(
        ROOT / "app", tmp_path / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update({"JAAY_ENV": "dev", "JAAY_DATABASE_URL": f"sqlite:///{tmp_path/'var'/'demo.db'}"})
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "import index\n"
        "from fastapi.testclient import TestClient\n"
        "r = TestClient(index.app).get('/__diag')\n"
        "print(json.dumps({'code': r.status_code, 'body': r.json()}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd="/tmp", env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["code"] == 200
    assert payload["body"]["package_present"] is True
    assert payload["body"]["application_import"] == "ok"


def test_vercel_config_has_no_ignored_memory_setting():
    """Vercel CLI 65.x avertit que `memory` est ignore en Active CPU billing."""
    cfg = json.loads((ROOT / "vercel.json").read_text())
    for entry in cfg["functions"].values():
        assert "memory" not in entry
    ignore = (ROOT / ".vercelignore").read_text()
    assert ".git/**" in ignore


def test_diagnose_script_is_usable():
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.vercel_diagnose", "--help"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "vercel_diagnose" in proc.stdout or "usage:" in proc.stdout.lower()


def test_placeholder_detection_is_strict():
    from scripts.check_before_push import is_placeholder
    assert is_placeholder("CHANGER_MOI")
    assert is_placeholder("user")
    assert is_placeholder("sqlite:///var/jaay.db")
    assert not is_placeholder("0123456789abcdef0123456789abcdef0123456789abcdef")
    assert not is_placeholder("MonVraiMotDePasse123")


def test_media_backend_reports_clear_error_when_s3_incomplete(data):
    import io

    from PIL import Image

    from app.services import media
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (12, 34, 56)).save(buf, format="PNG")
    object.__setattr__(settings, "media_backend", "s3")
    object.__setattr__(settings, "s3_bucket", "")
    try:
        with pytest.raises(media.MediaError) as exc:
            media.save_image(buf.getvalue(), shop_id="s1")
        assert "JAAY_S3" in str(exc.value)
    finally:
        object.__setattr__(settings, "media_backend", "local")
