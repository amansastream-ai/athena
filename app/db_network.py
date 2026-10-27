"""Compatibilite reseau de la base PostgreSQL (Vercel + Supabase).

Probleme concret rencontre en production ::

    connection to server at "db.<ref>.supabase.co" (2a05:...), port 5432 failed:
    Cannot assign requested address

La connexion DIRECTE Supabase ne publie qu'un enregistrement DNS AAAA (IPv6).
Vercel Functions n'ont pas de sortie IPv6 : libpq resout l'adresse IPv6 puis
echoue. Le seul correctif fiable est d'utiliser le **pooler partage** Supabase,
qui publie des adresses IPv4 :

  * Session pooler     : ``aws-<i>-<region>.pooler.supabase.com:5432``
  * Transaction pooler : ``aws-<i>-<region>.pooler.supabase.com:6543`` (conseille
    pour le serverless, avec ``NullPool`` cote SQLAlchemy)

Ce module ne contient aucun secret : il ne manipule que des noms d'hote, des
ports et des options de chaine de connexion. Il n'est jamais importe au
demarrage d'une requete utilisateur (sauf pour ``network_hint``).

Reference : https://supabase.com/docs/guides/database/connecting-to-postgres
            https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT
"""
from __future__ import annotations

import queue
import re
import socket
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Options de query propres a Prisma/Node et inconnues de libpq : les laisser
# passer provoquerait ``invalid connection option "pgbouncer"``.
_NON_LIBPQ_OPTIONS = {
    "pgbouncer", "connection_limit", "pool_timeout", "schema", "ip",
}
_SSL_HOSTS = ("supabase.co", "supabase.com", "pooler.supabase.com", "neon.tech")

_DIRECT_SUPABASE = re.compile(r"^db\.[a-z0-9]{10,}\.supabase\.co$", re.IGNORECASE)
_POOLER_SUPABASE = re.compile(r"(^|\.)pooler\.supabase\.com$", re.IGNORECASE)

POOLER_HINT = (
    "Vercel n'a pas de sortie IPv6. Dans Supabase : bouton « Connect » > "
    "Connection string > « Connection pooling », copiez l'URI du Session pooler "
    "(port 5432) ou du Transaction pooler (port 6543), hote ...pooler.supabase.com. "
    "Remplacez [YOUR-PASSWORD] par le mot de passe de la base, ajoutez "
    "?sslmode=require, puis mettez a jour JAAY_DATABASE_URL sur Vercel et REDEPLOYEZ."
)

# Cache DNS tres court : evite une resolution par connexion sans figer une
# adresse qui pourrait changer (failover, rotation du pooler).
_dns_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_dns_lock = threading.Lock()


def _split(url: str):
    try:
        return urlsplit(url)
    except ValueError:
        return None


def is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://", "postgresql+"))


def database_host(url: str) -> str:
    parts = _split(url)
    return (parts.hostname or "") if parts else ""


def database_port(url: str) -> int:
    parts = _split(url)
    if not parts:
        return 5432
    try:
        return parts.port or 5432
    except ValueError:
        return 5432


def is_direct_supabase(url: str) -> bool:
    """Vrai pour ``db.<ref>.supabase.co:5432`` (IPv6 uniquement par defaut)."""
    return bool(_DIRECT_SUPABASE.match(database_host(url))) and database_port(url) == 5432


def is_supabase_pooler(url: str) -> bool:
    """Vrai pour un pooler partage IPv4 (ou le pooler dedie sur le port 6543)."""
    host = database_host(url)
    port = database_port(url)
    return bool(_POOLER_SUPABASE.search(host)) or (
        bool(_DIRECT_SUPABASE.match(host)) and port == 6543
    )


def use_null_pool(url: str) -> bool:
    """Transaction pooler (6543) : Supabase recommande ``NullPool`` cote ORM.

    Le pooler fait deja le travail ; un pool SQLAlchemy par-dessus epuise les
    connexions et envoie des commandes de session non supportees.
    """
    return is_supabase_pooler(url) and database_port(url) == 6543


def normalize_database_url(url: str) -> str:
    """Rend l'URI utilisable par SQLAlchemy 2 + psycopg2, sans toucher au secret.

    * ``postgres://`` / ``postgresql://`` -> ``postgresql+psycopg2://``
    * ajoute ``sslmode=require`` sur les hotes geres qui l'ont oublie
    * retire les options Prisma/Node inconnues de libpq
    * conserve le mot de passe (eventuellement percent-encode) tel quel
    """
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if not is_postgres(url):
        return url

    parts = _split(url)
    if not parts:
        return url
    host = parts.hostname or ""
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    original = list(pairs)
    keys = {key.lower() for key, _ in pairs}
    pairs = [(k, v) for k, v in pairs if k.lower() not in _NON_LIBPQ_OPTIONS]
    if "sslmode" not in keys and any(h in host.lower() for h in _SSL_HOSTS):
        pairs.append(("sslmode", "require"))
    if pairs == original:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _resolve(host: str, port: int) -> dict:
    out: dict = {"ipv4": [], "ipv6": [], "dns_error": None}
    try:
        for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            host, port, 0, socket.SOCK_STREAM
        ):
            ip = sockaddr[0]
            key = "ipv4" if family == socket.AF_INET else "ipv6" if family == socket.AF_INET6 else None
            if key and ip not in out[key]:
                out[key].append(ip)
    except OSError as exc:
        out["dns_error"] = f"{type(exc).__name__}: {exc}"[:160]
    return out


def _resolve_cached(host: str, port: int, timeout: float) -> dict:
    key = (host, port)
    now = time.monotonic()
    with _dns_lock:
        cached = _dns_cache.get(key)
        if cached and now - cached[0] < 120:
            return cached[1]
    result: dict = {"ipv4": [], "ipv6": [], "dns_error": "resolution timeout"}
    box: queue.Queue[dict] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            box.put_nowait(_resolve(host, port))
        except Exception as exc:  # pragma: no cover - defensive
            box.put_nowait({"ipv4": [], "ipv6": [], "dns_error": type(exc).__name__})

    threading.Thread(target=worker, daemon=True, name="jaay-dns").start()
    try:
        result = box.get(timeout=timeout)
    except queue.Empty:
        pass
    with _dns_lock:
        _dns_cache[key] = (now, result)
    return result


def network_hint(error: Exception | str) -> str | None:
    """Reconnait une panne de sortie IPv6 et renvoie la marche a suivre."""
    text = str(error).lower()
    markers = (
        "cannot assign requested address", "network is unreachable",
        "no route to host", "enotfound", "eai_again", "name or service not known",
    )
    if any(marker in text for marker in markers):
        return POOLER_HINT
    return None


def config_error(url: str, *, on_vercel: bool, allow_ipv6: bool = False) -> str | None:
    """Bloque tot, avec un message clair, une URI directe Supabase sur Vercel.

    ``allow_ipv6`` (``JAAY_ALLOW_IPV6_DB=1``) desactive la protection pour les
    projets disposant de l'add-on IPv4 de Supabase.
    """
    if not on_vercel or allow_ipv6 or not is_postgres(url):
        return None
    if not is_direct_supabase(url):
        return None
    return (
        "JAAY_DATABASE_URL utilise la connexion DIRECTE Supabase "
        "(db.<ref>.supabase.co:5432), qui n'est joignable qu'en IPv6. "
        "Vercel Functions sont IPv4 uniquement : la connexion echoue avec "
        "'Cannot assign requested address'. " + POOLER_HINT
        + " (Si votre projet a l'add-on IPv4 de Supabase, posez JAAY_ALLOW_IPV6_DB=1.)"
    )


def diagnostics(url: str, *, timeout: float = 1.5) -> dict:
    """Rapport sans secret pour ``/__diag`` : hote, port, DNS IPv4/IPv6, conseil."""
    # On rapporte l'URI REELLEMENT utilisee par le moteur (driver + sslmode),
    # pas la chaine brute copiee depuis le dashboard Supabase.
    url = normalize_database_url(url)
    parts = _split(url)
    host = (parts.hostname or "") if parts else ""
    port = database_port(url) if parts else 5432
    query = dict(parse_qsl(parts.query, keep_blank_values=True)) if parts else {}
    info: dict = {
        "is_postgres": is_postgres(url),
        "host": host or None,
        "port": port if host else None,
        "sslmode": query.get("sslmode"),
        "pooler": is_supabase_pooler(url),
        "direct_supabase_ipv6": is_direct_supabase(url),
        "null_pool": use_null_pool(url),
        "ipv4": [],
        "ipv6": [],
        "dns_error": None,
        "hint": None,
    }
    if not host or not info["is_postgres"]:
        return info
    resolved = _resolve_cached(host, port, timeout)
    info["ipv4"] = resolved["ipv4"]
    info["ipv6"] = resolved["ipv6"]
    info["dns_error"] = resolved["dns_error"]
    if info["direct_supabase_ipv6"] and not resolved["ipv4"]:
        info["hint"] = POOLER_HINT
    elif info["direct_supabase_ipv6"]:
        info["hint"] = "Hote direct Supabase avec IPv4 : verifiez quand meme le pooler sur Vercel."
    elif info["pooler"]:
        info["hint"] = "Pooler Supabase IPv4 detecte : configuration reseau compatible Vercel."
    elif resolved["dns_error"]:
        info["hint"] = "Resolution DNS impossible depuis la fonction : verifiez le nom d'hote."
    return info
