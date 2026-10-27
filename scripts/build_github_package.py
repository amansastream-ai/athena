"""Construit une archive prete a etre poussee sur GitHub, SANS aucun secret.

Usage : python3 -m scripts.build_github_package

Produit (dans le dossier parent du projet) :
  jaay-github.zip       -> dossier 'jaay/' pret a extraire
  jaay-github.tar.gz    -> alternative Linux/macOS
  jaay-github.sha256    -> empreintes pour verifier le telechargement
  jaay-package/jaay/    -> dossier propre, consultable tel quel
"""
from __future__ import annotations

import hashlib
import shutil
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_PARENT = ROOT.parent / "jaay-package"
OUT = PACKAGE_PARENT / "jaay"

ROOT_FILES = [
    ".env.example", ".env.vercel.example", ".gitignore", ".vercelignore",
    ".python-version", "AVANT-GITHUB.md", "DIAGNOSTIC-BUILD-VERCEL.md",
    "Dockerfile", "Makefile", "POUR-GITHUB.md", "RAPPORT.md", "README.md",
    "VARIABLES-VERCEL-DISPARUES.md", "ETAPE-SUIVANTE.md", "VERCEL.md", "alembic.ini", "docker-compose.yml", "index.py", "pytest.ini",
    "requirements.txt", "vercel.json",
]
DIRS = ["app", "docs", "tests", "scripts", "ops", "migrations", ".github"]

SKIP_PARTS = {
    "__pycache__", ".pytest_cache", ".git", ".vercel", ".venv", "venv",
    "node_modules", ".mypy_cache", ".ruff_cache", "htmlcov", ".cache",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".db-journal",
                 ".log", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx"}
# Ne jamais embarquer un fichier .env contenant des valeurs reelles.
SKIP_NAMES = {".env", ".env.local", ".env.vercel.local", ".env.production",
              "dev_secret.key"}


def safe(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in SKIP_PARTS for part in rel.parts):
        return False
    if path.name in SKIP_NAMES:
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    if path.name.startswith(".env") and path.name not in {".env.example", ".env.vercel.example"}:
        return False
    return True


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    missing = [f for f in ROOT_FILES if not (ROOT / f).exists()]
    if missing:
        raise SystemExit(f"Fichiers requis manquants : {missing}")

    count = 0
    for name in ROOT_FILES:
        shutil.copy2(ROOT / name, OUT / name)
        count += 1

    for dirname in DIRS:
        src = ROOT / dirname
        if not src.exists():
            continue
        for path in src.rglob("*"):
            if not path.is_file() or not safe(path):
                continue
            rel = path.relative_to(ROOT)
            dest = OUT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            count += 1

    # Repertoires de donnees : seulement les .gitkeep pour que var/ et media/ existent.
    for dirname in ("var", "media"):
        (OUT / dirname).mkdir(parents=True, exist_ok=True)
        (OUT / dirname / ".gitkeep").write_text("")
        count += 1

    # Droits executables sur les scripts shell.
    for path in OUT.rglob("*.sh"):
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    archive_base = str(ROOT.parent / "jaay-github")
    zip_path = shutil.make_archive(archive_base, "zip", root_dir=str(PACKAGE_PARENT), base_dir="jaay")
    tar_path = shutil.make_archive(archive_base, "gztar", root_dir=str(PACKAGE_PARENT), base_dir="jaay")

    checksums = []
    for archive in (zip_path, tar_path):
        digest = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
        checksums.append(f"{digest}  {Path(archive).name}  ({Path(archive).stat().st_size:,} octets)")

    (ROOT.parent / "jaay-github.sha256").write_text("\n".join(checksums) + "\n")

    print(f"Dossier propre : {OUT} ({count} fichiers)")
    for line in checksums:
        print(line)
    print("\nFichiers sensibles exclus : .env*, var/*.db, media/*, __pycache__, caches.")


if __name__ == "__main__":
    main()
