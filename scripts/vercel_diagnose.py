"""Diagnostique un deploiement Jaay (Vercel, staging ou local).

Un build Vercel reussi ne garantit pas que l'application fonctionne : les
variables d'environnement sont lues au DEMARRAGE de la fonction. Ce script
interroge le site deploye et dit exactement ou ca coince.

Usage :
    python3 -m scripts.vercel_diagnose https://mon-projet.vercel.app
    python3 -m scripts.vercel_diagnose http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message

OK = "\033[32mOK\033[0m"
WARN = "\033[33mWARN\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"


@dataclass
class Resp:
    status: int
    headers: Message
    body: str
    url: str
    error: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _opener(jar: http.cookiejar.CookieJar):
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _NoRedirect()
    )


def call(opener, url: str, *, data: dict | None = None, timeout: int = 25) -> Resp:
    headers = {"User-Agent": "jaay-diagnose/1.0", "Accept": "text/html,application/json"}
    payload = None
    if data is not None:
        payload = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        url, data=payload, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        r = opener.open(req, timeout=timeout)
        return Resp(r.status, r.headers, r.read().decode("utf-8", "replace"), r.geturl())
    except urllib.error.HTTPError as exc:
        return Resp(exc.code, exc.headers, exc.read().decode("utf-8", "replace"), exc.geturl())
    except Exception as exc:  # DNS, TLS, timeout...
        return Resp(0, Message(), "", url, f"{type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnostique un deploiement Jaay")
    ap.add_argument("url", help="URL du deploiement, ex : https://jaay-xxx.vercel.app")
    ap.add_argument("--email", default="admin@jaay.local")
    ap.add_argument("--password", default="AdminJaay2026!")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    https = base.startswith("https://")

    print(f"\033[1mJaay — diagnostic de {base}\033[0m\n")
    blockers = 0
    warnings = 0
    hints: list[str] = []

    def line(level: str, label: str, detail: str = "") -> None:
        nonlocal blockers, warnings
        if level == FAIL:
            blockers += 1
        elif level == WARN:
            warnings += 1
        print(f"  [{level}] {label}" + (f" — {detail}" if detail else ""))

    jar = http.cookiejar.CookieJar()
    opener = _opener(jar)

    # ---------------------------------------------------------------- sonde
    print("[0] Sonde /__diag (repond meme si l'application ne demarre pas)")
    diag = call(opener, base + "/__diag")
    diag_info = {}
    if diag.status == 200:
        try:
            diag_info = json.loads(diag.body)
        except json.JSONDecodeError:
            diag_info = {}
    if diag_info:
        line(OK, "sonde disponible",
             f"python={diag_info.get('python')} routes={diag_info.get('routes_count')}")
        config_error = diag_info.get("config_error_at_import")
        if config_error:
            line(FAIL, "erreur a l'import de l'application", str(config_error)[:180])
            hints.append("Corrigez la variable indiquee dans Vercel > Environment Variables, "
                         "puis REDEPLOYEZ (les changements ne s'appliquent qu'au prochain deploy).")
        if diag_info.get("application_import") == "error":
            line(FAIL, "import app.application", str(diag_info.get("error", ""))[:180])
        elif diag_info.get("application_import") == "ok":
            line(OK, "import app.application", "ok")
        presence = diag_info.get("env_presence", {})
        watched = ["JAAY_ENV", "JAAY_SECRET_KEY", "JAAY_DATABASE_URL", "JAAY_PUBLIC_BASE_URL"]
        absent = [k for k in watched if presence.get(k) is False]
        if absent:
            line(FAIL, "variables absentes de la fonction", ", ".join(absent))
            hints.append("Verifiez le PROJET et l'EQUIPE (team) dans Vercel, et cochez bien "
                         "la portee PRODUCTION pour chaque variable.")
        elif presence:
            line(OK, "variables presentes", ", ".join(watched))
        trace = str(diag_info.get("traceback", ""))
        if trace and "activez" not in trace:
            print(f"       trace : {trace[:300]}")
    else:
        line(WARN, "sonde /__diag indisponible", f"HTTP {diag.status}")
        hints.append("Le code deploye ne contient pas la sonde /__diag : poussez la derniere "
                     "archive puis redeployez. Sans elle, seul `vercel logs --follow` peut aider.")

    # ---------------------------------------------------------------- sante
    print("\n[1] Sante de la fonction")
    h = call(opener, base + "/health")
    if h.error:
        line(FAIL, "site injoignable", h.error)
        hints.append("Verifiez l'URL, le DNS et que le deploiement est bien en Production.")
    elif h.status in (401, 403):
        line(FAIL, "acces protege", f"HTTP {h.status}")
        hints.append("Vercel Deployment Protection / SSO bloque l'acces. Desactivez-le "
                     "dans Project Settings > Deployment Protection, ou fournissez un token.")
    elif h.status == 404 and h.headers.get("x-vercel-error") == "DEPLOYMENT_NOT_FOUND":
        line(FAIL, "aucun deploiement actif sur ce domaine", "DEPLOYMENT_NOT_FOUND")
        print("\n[5] Bilan")
        print("  Ce domaine ne pointe vers aucun deploiement. Causes frequentes :")
        print("   - deploiement supprime, annule ou echec du build ;")
        print("   - vous avez deploye en PREVIEW (`vercel` sans `--prod`) : le domaine")
        print("     de production reste alors sans deploiement ;")
        print("   - un NOUVEAU projet Vercel a ete cree : l'ancien domaine est vide ;")
        print("   - vous regardez une URL differente de celle testee ici.")
        print("\n  Actions :")
        print("   1. Dans Vercel > Deployments, copiez l'URL EXACTE du deploiement qui")
        print("      affiche l'erreur 500 (ex : https://projet-abc123.vercel.app).")
        print("   2. Verifiez que le projet, l'equipe (team) et la branche sont les bons.")
        print("   3. Lancez `vercel --prod` (ou poussez sur la branche de production).")
        print("   4. Relancez ce diagnostic avec la bonne URL.")
        return 1
    elif h.status >= 500:
        snippet = re.sub(r"\s+", " ", h.body)[:180]
        vercel_error = h.headers.get("x-vercel-error", "")
        if vercel_error:
            line(FAIL, "la fonction plante au demarrage",
                 f"HTTP {h.status} x-vercel-error={vercel_error}")
            hints.append(
                "FUNCTION_INVOCATION_FAILED = le module ASGI n'a pas pu etre importe. "
                "Ce n'est ni un bug de route ni un probleme de CSS : verifiez D'ABORD "
                "JAAY_SECRET_KEY et JAAY_DATABASE_URL dans Vercel."
            )
        else:
            line(FAIL, "la fonction plante au demarrage", f"HTTP {h.status} {snippet}")
        hints.append("Cause la plus frequente : JAAY_SECRET_KEY absent (obligatoire quand "
                     "JAAY_ENV=production) ou JAAY_DATABASE_URL absent/SQLite.")
        hints.append("Regardez les logs : vercel logs <projet> --follow")
    elif h.status != 200:
        line(FAIL, "routage /health", f"HTTP {h.status}")
        hints.append("Le point d'entree n'est pas detecte : verifiez index.py et "
                     "\"framework\": \"fastapi\" dans vercel.json.")
    else:
        try:
            info = json.loads(h.body)
        except json.JSONDecodeError:
            line(FAIL, "/health ne renvoie pas du JSON", h.body[:120])
            info = {}
        if info:
            line(OK, "application importee", f"env={info.get('env')} version={info.get('version')}")
            if info.get("status") != "ok" or info.get("database") != "ok":
                line(FAIL, "base de donnees", str(info.get("database")))
                hints.append("JAAY_DATABASE_URL incorrecte, base en pause, SSL requis "
                             "(ajoutez ?sslmode=require) ou firewall du fournisseur.")
            else:
                line(OK, "base de donnees", info.get("database_backend", "?"))
            if info.get("database_backend") == "sqlite":
                line(WARN, "SQLite sur le deploiement", "les donnees seront perdues au prochain demarrage")
            if info.get("runtime") != "vercel" and https:
                line(WARN, "runtime", str(info.get("runtime")))
            if info.get("env") != "production" and https:
                line(WARN, "JAAY_ENV", f"={info.get('env')} (attendu production)")
                hints.append("Mettez JAAY_ENV=production dans les variables Vercel.")
            if not info.get("demo_mode"):
                line(WARN, "JAAY_DEMO_MODE=0", "les comptes de demonstration n'existent pas")
                hints.append("Pour une demo : JAAY_DEMO_MODE=1 puis redéployez, "
                             "ou lancez bash scripts/vercel_bootstrap.sh.")
            presence = info.get("env_presence", {})
            if presence:
                watched = ["JAAY_SECRET_KEY", "JAAY_DATABASE_URL",
                           "JAAY_PUBLIC_BASE_URL", "JAAY_ENV"]
                if https:
                    watched.append("VERCEL")  # injecte uniquement par la plateforme
                absent = [k for k in watched if presence.get(k) is False]
                if absent:
                    line(FAIL, "variables absentes dans la fonction", ", ".join(absent))
                    hints.append("Ces variables n'atteignent PAS la fonction : verifiez le projet, "
                                 "l'equipe (team) et la portee PRODUCTION dans Vercel.")
                    hints.append("Les valeurs de type Secret sont masquees dans le dashboard : "
                                 "c'est normal. Pour etre sur, supprimez la ligne et recreez-la, puis redeployez.")
                else:
                    line(OK, "variables presentes dans la fonction", ", ".join(sorted(presence)))
            if info.get("media_backend") != "s3":
                line(WARN, "photos", f"backend={info.get('media_backend')} (uploads locaux = echec sur Vercel)")

    # ---------------------------------------------------------------- pages
    print("\n[2] Pages et ressources")
    for path, expected in [("/", 200), ("/catalogue", 200), ("/connexion", 200),
                           ("/static/app.css", 200), ("/api/docs", 200)]:
        r = call(opener, base + path)
        if r.status == expected:
            line(OK, path, "200")
        elif r.status >= 500:
            line(FAIL, path, f"HTTP {r.status}")
            hints.append(f"{path} plante : regardez les Runtime Logs Vercel.")
        else:
            line(FAIL, path, f"HTTP {r.status} (attendu {expected})")
            if path == "/static/app.css":
                hints.append("CSS absent : app/static a ete exclu du bundle. Retirez "
                             "toute regle 'static/**' de vercel.json / .vercelignore.")

    # ---------------------------------------------------------------- cookies
    print("\n[3] Cookies (connexion en iframe / HTTPS)")
    lp = call(opener, base + "/connexion")
    set_cookies = lp.headers.get_all("Set-Cookie") or []
    csrf_cookie = next((c for c in set_cookies if c.startswith("jaay_csrf=")), "")
    if https:
        missing = [a for a in ("SameSite=none", "Secure", "Partitioned") if a not in csrf_cookie]
        if missing:
            line(WARN, "attributs manquants", ", ".join(missing))
            hints.append("Les cookies doivent etre SameSite=None; Secure; Partitioned (gere par l'app).")
        else:
            line(OK, "cookies cross-site", "SameSite=none, Secure, Partitioned")
    else:
        line(OK, "HTTP local", "Lax attendu (pas de verification HTTPS)")

    # ---------------------------------------------------------------- login
    print("\n[4] Connexion au compte de demonstration")
    token_m = re.search(r'name="csrf-token" content="([^"]+)"', lp.body)
    if not token_m:
        line(FAIL, "jeton CSRF absent de /connexion")
        hints.append("La page de connexion n'est pas rendue correctement (voir [2]).")
    else:
        jar.clear()  # simule un navigateur qui bloque les cookies tiers
        r = call(opener, base + "/connexion", data={
            "identifiant": args.email,
            "mot_de_passe": args.password,
            "csrf_token": token_m.group(1),
        })
        loc = r.headers.get("Location", "")
        if r.status in (302, 303) and loc.endswith(("/admin", "/vendeur", "/")) and "erreur" not in loc:
            line(OK, "POST /connexion sans cookies", f"{r.status} -> {loc}")
        elif r.status >= 500:
            line(FAIL, "POST /connexion", f"HTTP {r.status}")
            hints.append("Erreur serveur a la connexion : base inaccessible ou JAAY_SECRET_KEY "
                         "qui change entre deux instances (mettez une valeur FIXE dans Vercel).")
        elif "erreur=session_expiree" in loc or "erreur=acces_refuse" in loc:
            line(FAIL, "jeton CSRF refuse", loc)
            hints.append("JAAY_SECRET_KEY doit etre identique sur toutes les instances ; "
                         "ne la regenerez pas a chaque deploiement.")
        elif "erreur=Identifiants" in loc or "connexion" in loc:
            line(FAIL, "identifiants refuses", loc)
            hints.append("Comptes demo absents : JAAY_DEMO_MODE=1 + redéploiement, "
                         "ou bash scripts/vercel_bootstrap.sh.")
        else:
            line(FAIL, "POST /connexion", f"{r.status} -> {loc}")

        if r.status in (302, 303) and loc.endswith("/admin"):
            admin = call(opener, base + "/admin")
            if admin.status == 200:
                line(OK, "GET /admin avec session", "200")
            elif admin.status in (302, 303):
                line(FAIL, "session non conservee", f"redirection vers {admin.headers.get('Location')}")
                hints.append("Cookies de session refuses : verifiez SameSite=None; Secure; "
                             "Partitioned et JAAY_COOKIE_SECURE=1.")
            else:
                line(FAIL, "GET /admin", f"HTTP {admin.status}")

    # ---------------------------------------------------------------- bilan
    print("\n[5] Bilan")
    if blockers == 0:
        print(f"  [{OK}] Le deploiement repond correctement. {warnings} avertissement(s).")
    else:
        print(f"  [{FAIL}] {blockers} probleme(s) detecte(s).")
        print(f"\n  \033[1mCauses probables et actions :\033[0m")
        seen = set()
        for hint in hints:
            if hint not in seen:
                print(f"   - {hint}")
                seen.add(hint)
        print(f"\n  Logs en direct : vercel logs <projet> --follow")
        print(f"  Variables      : vercel env ls   (puis vercel --prod pour redéployer)")
        print(f"  Controle local : python3 -m scripts.vercel_preflight")
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
