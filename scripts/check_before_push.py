"""Controle de securite avant de pousser le depot sur GitHub.

Usage : python3 -m scripts.check_before_push

Verifie :
  1. que .env, les bases locales et les cles sont bien ignores par git ;
  2. qu'aucun secret reel ne se trouve dans les fichiers destines a etre commites ;
  3. que les modeles .env.example / .env.vercel.example ne contiennent que des
     valeurs vides ou des exemples fictifs.

Code de sortie : 0 = OK (avec avertissements possibles), 1 = blocage.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK, WARN, FAIL = "\033[32mOK\033[0m", "\033[33mWARN\033[0m", "\033[31mFAIL\033[0m"

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
             ".vercel", "node_modules", "var", "media", "htmlcov", ".cache"}
TEXT_EXT = {".py", ".sh", ".md", ".txt", ".html", ".css", ".js", ".json", ".yml", ".yaml",
            ".ini", ".cfg", ".toml", ".example", ".conf", ""}

# Sont consideres comme des exemples fictifs, pas des secrets.
# On utilise des mots ENTIERS (`\b...\b`) et non une simple recherche de sous-chaine :
# sinon "abc" present dans une vraie cle hexadecimale serait pris pour un exemple.
_PLACEHOLDER_RE = re.compile(
    r"(?i)\b(changer[_-]?moi|remplacer|exemple|example|placeholder|dummy|fake|"
    r"username|password|passwd|pwd|user|test|votre|xxx+|yyy+|zzz+)\b"
)


def is_placeholder(value: str) -> bool:
    v = (value or "").strip().strip("\"'")
    if not v or len(v) <= 3:
        return True
    if v.startswith(("<", "${", "sqlite:///", "var/", "...", "http://exemple", "https://exemple")):
        return True
    return bool(_PLACEHOLDER_RE.search(v))


# Motifs de secrets reels. Chaque entree : (nom, regex, groupe de la valeur).
PATTERNS = [
    ("cle applicative JAAY_SECRET_KEY",
     re.compile(r"(?i)JAAY_SECRET_KEY\s*=\s*[\"']?([0-9a-fA-F]{32,})")),
    ("URI de base de donnees avec mot de passe",
     re.compile(r"(?i)(?:postgres(?:ql)?(?:\+psycopg2)?|mysql|mongodb(?:\+srv)?|redis)://"
                r"([^\s:/]+):([^\s@/]+)@[^\s/]+")),
    ("cle AWS / R2", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("cle API OpenAI", re.compile(r"\bsk-[A-Za-z0-9]{24,}\b")),
    ("token GitHub", re.compile(r"\b(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("cle privee", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("variable d'integration non vide",
     re.compile(r"(?i)^[ \t]*(WAVE_API_KEY|WAVE_WEBHOOK_SECRET|WAVE_SIGNING_SECRET|OM_CLIENT_SECRET|"
                r"OM_WEBHOOK_SECRET|WA_CLOUD_TOKEN|WA_CLOUD_APP_SECRET|SMS_API_KEY|AI_API_KEY|"
                r"JAAY_S3_ACCESS_KEY|JAAY_S3_SECRET_KEY|JAAY_BOOTSTRAP_ADMIN_PASSWORD|"
                r"POSTGRES_PASSWORD)[ \t]*=[ \t]*([^\s#]+)",
                re.M)),
]

MUST_IGNORE = [
    ".env", ".env.local", ".env.production", "var/jaay.db", "var/dev_secret.key",
    "media/photo_test.webp", "var/backup.sql",
]
TEMPLATES = [".env.example", ".env.vercel.example"]
SENSITIVE_KEYS = re.compile(
    r"(?i)^[ \t]*(JAAY_SECRET_KEY|JAAY_DATABASE_URL|JAAY_S3_ACCESS_KEY|JAAY_S3_SECRET_KEY|"
    r"WAVE_API_KEY|WAVE_WEBHOOK_SECRET|OM_CLIENT_SECRET|OM_WEBHOOK_SECRET|WA_CLOUD_TOKEN|"
    r"SMS_API_KEY|AI_API_KEY|JAAY_BOOTSTRAP_ADMIN_PASSWORD)[ \t]*=[ \t]*([^\s#]*)", re.M)


def is_repo() -> bool:
    return (ROOT / ".git").exists()


def ignored_by_git(rel: str) -> bool:
    proc = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT,
                          capture_output=True)
    return proc.returncode == 0


def iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in TEXT_EXT:
            continue
        try:
            yield rel, path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def main() -> int:
    print("\033[1mJaay — verification avant GitHub\033[0m\n")
    blockers = 0
    warnings = 0

    # 1. .gitignore
    gitignore = (ROOT / ".gitignore").read_text()
    print("[1] Fichiers sensibles exclus du depot")
    env_file = ROOT / ".env"
    if env_file.exists():
        if is_repo() and ignored_by_git(".env"):
            print(f"  [{OK}] .env existe localement et est ignore par git (ne pas le forcer avec git add -f)")
        elif is_repo():
            print(f"  [{FAIL}] .env existe et N'est PAS ignore — ajoutez-le a .gitignore")
            blockers += 1
        else:
            print(f"  [{WARN}] .env existe localement ; git n'est pas encore initialise. Verifiez avant le premier commit.")
            warnings += 1
    else:
        print(f"  [{OK}] aucun fichier .env present (rien de secret a pousser)")

    required = [".env", ".env.*", "var/*.db", "var/dev_secret.key", "media/*", ".vercel/"]
    missing = [p for p in required if p not in gitignore]
    if missing:
        print(f"  [{FAIL}] motifs absents de .gitignore : {missing}")
        blockers += len(missing)
    else:
        print(f"  [{OK}] .gitignore couvre .env, var/*.db, var/dev_secret.key, media/*, .vercel/")

    if is_repo():
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        leaked = [f for f in tracked.stdout.splitlines()
                  if f == ".env" or f.startswith(("var/", "media/")) and not f.endswith(".gitkeep")
                  or f.endswith((".pem", ".key"))]
        if leaked:
            print(f"  [{FAIL}] fichiers sensibles deja suivis par git : {leaked}")
            blockers += 1
        else:
            print(f"  [{OK}] aucun fichier sensible present dans l'index git")
    else:
        print(f"  [{WARN}] pas encore de depot git initialise — relancez ce controle apres `git init` et `git add .`")
        warnings += 1

    # 2. Modeles
    print("\n[2] Modeles .env.example / .env.vercel.example")
    for name in TEMPLATES:
        path = ROOT / name
        if not path.exists():
            print(f"  [{FAIL}] {name} manquant")
            blockers += 1
            continue
        found = []
        for key, value in SENSITIVE_KEYS.findall(path.read_text()):
            if value and not is_placeholder(value):
                found.append(f"{key}={value[:24]}...")
        if found:
            print(f"  [{FAIL}] {name} contient une valeur reelle : {found[:3]}")
            blockers += 1
        else:
            print(f"  [{OK}] {name} : valeurs vides ou exemples fictifs uniquement")

    # 3. Scan des secrets
    print("\n[3] Recherche de secrets dans les fichiers du depot")
    hits = []
    for rel, text in iter_text_files():
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                groups = match.groups()
                # Le dernier groupe est toujours la VALEUR ; les groupes
                # precedents sont des noms de variables ou un utilisateur.
                values = [groups[-1]] if groups and groups[-1] else ([match.group(0)] if not groups else [])
                if any(not is_placeholder(v) for v in values):
                    line = text.count("\n", 0, match.start()) + 1
                    hits.append((str(rel), line, label))
    if hits:
        for rel, line, label in hits[:12]:
            print(f"  [{FAIL}] {rel}:{line} — {label}")
        print(f"  [{FAIL}] {len(hits)} occurrence(s) a verifier avant de pousser")
        blockers += len(hits)
    else:
        print(f"  [{OK}] aucun secret reel detecte (placeholders exclus)")

    # 4. Rappels
    print("\n[4] Rappels")
    if (ROOT / ".env.vercel.example").exists():
        print(f"  [{WARN}] Les identifiants de demonstration sont publics dans le code (mode demo).")
        print("         Mettez JAAY_DEMO_MODE=0 et changez les mots de passe avant tout usage reel.")
        warnings += 1
    print("  [INFO] Variables reelles a mettre dans Vercel > Settings > Environment Variables,")
    print("         JAMAIS dans un fichier du depot GitHub.")

    print()
    if blockers:
        print(f"\033[1m{blockers} blocage(s) : ne poussez pas tant qu'ils ne sont pas corriges.\033[0m")
        return 1
    print(f"\033[1mOK — depot publiable.\033[0m {warnings} rappel(s).")
    print("Commandes : git add . && git status --short && git commit -m 'Jaay MVP' && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
