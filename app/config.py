"""Configuration centralisee. Aucun secret n'est ecrit dans le code source.

Toutes les valeurs sensibles viennent de variables d'environnement (voir .env.example).
En developpement uniquement, une cle de session locale est generee et stockee dans var/.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR_DIR = ROOT / "var"
MEDIA_DIR = ROOT / "media"


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _bool(key: str, default: bool = False) -> bool:
    return _env(key, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _load_dotenv() -> None:
    """Charge .env sans dependance externe. Les variables deja definies gagnent toujours."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if value.startswith(("'", '"')) and len(value) > 1 and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _writable_dir(path: Path) -> Path:
    """Retourne un repertoire existant et ecrivable, sinon un repli dans /tmp.

    Sur Vercel (et la plupart des plateformes serverless) le code source est en
    lecture seule : ecrire dans le projet fait planter l'import au demarrage.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.access(path, os.W_OK):
            return path
    except OSError:
        pass
    fallback = Path(tempfile.gettempdir()) / "jaay-runtime"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback


def _dev_secret() -> str:
    """Cle locale de developpement. En production, JAAY_SECRET_KEY est obligatoire."""
    directory = _writable_dir(VAR_DIR)
    path = directory / "dev_secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class Settings:
    app_name: str = "Jaay"
    env: str = _env("JAAY_ENV", "dev")                       # dev | test | staging | production
    debug: bool = _bool("JAAY_DEBUG", False)
    secret_key: str = _env("JAAY_SECRET_KEY", "")
    database_url: str = _env("JAAY_DATABASE_URL", f"sqlite:///{VAR_DIR / 'jaay.db'}")
    public_base_url: str = _env("JAAY_PUBLIC_BASE_URL", "")  # ex: https://jaay.sn
    media_dir: Path = Path(_env("JAAY_MEDIA_DIR", str(MEDIA_DIR)))
    media_backend: str = _env("JAAY_MEDIA_BACKEND", "local")  # local | s3
    max_upload_mb: int = _int("JAAY_MAX_UPLOAD_MB", 5)

    session_ttl_hours: int = _int("JAAY_SESSION_TTL_HOURS", 24 * 7)
    cookie_secure: bool = _bool("JAAY_COOKIE_SECURE", _env("JAAY_ENV", "dev") == "production")
    bcrypt_rounds: int = _int("JAAY_PBKDF2_ROUNDS", 260_000)
    login_max_attempts: int = _int("JAAY_LOGIN_MAX_ATTEMPTS", 5)
    rate_limit_per_minute: int = _int("JAAY_RATE_LIMIT_PER_MINUTE", 60)
    dev_show_otp: bool = _bool("JAAY_DEV_SHOW_OTP", True)

    # Paiements -- desactifs par defaut tant que les acces marchands ne sont pas obtenus.
    wave_api_key: str = _env("WAVE_API_KEY", "")
    wave_signing_secret: str = _env("WAVE_SIGNING_SECRET", "")
    wave_webhook_secret: str = _env("WAVE_WEBHOOK_SECRET", "")
    wave_base_url: str = _env("WAVE_BASE_URL", "https://api.wave.com")
    wave_aggregated_merchant_id: str = _env("WAVE_AGGREGATED_MERCHANT_ID", "")
    wave_mode: str = _env("WAVE_MODE", "off")                # off | sandbox | live

    om_client_id: str = _env("OM_CLIENT_ID", "")
    om_client_secret: str = _env("OM_CLIENT_SECRET", "")
    om_merchant_key: str = _env("OM_MERCHANT_KEY", "")
    om_token_url: str = _env("OM_TOKEN_URL", "")
    om_webpayment_url: str = _env("OM_WEBPAYMENT_URL", "")
    om_status_url: str = _env("OM_STATUS_URL", "")
    om_webhook_secret: str = _env("OM_WEBHOOK_SECRET", "")
    om_webhook_header: str = _env("OM_WEBHOOK_SIGNATURE_HEADER", "x-om-signature")
    om_mode: str = _env("OM_MODE", "off")                    # off | sandbox | live

    # WhatsApp Cloud API (optionnel, exige verification Meta)
    wa_cloud_token: str = _env("WA_CLOUD_TOKEN", "")
    wa_cloud_phone_number_id: str = _env("WA_CLOUD_PHONE_NUMBER_ID", "")
    wa_cloud_app_secret: str = _env("WA_CLOUD_APP_SECRET", "")
    wa_cloud_api_url: str = _env("WA_CLOUD_API_URL", "https://graph.facebook.com/v21.0")
    wa_cloud_verify_token: str = _env("WA_CLOUD_VERIFY_TOKEN", "")

    # SMS / OTP : console en dev, adaptateur HTTP configurable en production
    sms_api_url: str = _env("SMS_API_URL", "")
    sms_api_key: str = _env("SMS_API_KEY", "")
    sms_sender: str = _env("SMS_SENDER", "JAAY")

    # Email / notifications admin
    smtp_host: str = _env("SMTP_HOST", "")
    smtp_port: int = _int("SMTP_PORT", 587)
    smtp_user: str = _env("SMTP_USER", "")
    smtp_password: str = _env("SMTP_PASSWORD", "")
    admin_email: str = _env("JAAY_ADMIN_EMAIL", "")
    alert_webhook_url: str = _env("JAAY_ALERT_WEBHOOK_URL", "")

    # Assistance IA (optionnelle, sans cle = mode local deterministe)
    ai_api_key: str = _env("AI_API_KEY", "")
    ai_api_url: str = _env("AI_API_URL", "https://api.openai.com/v1/chat/completions")
    ai_model: str = _env("AI_MODEL", "gpt-4o-mini")

    # Amorçage (uniquement via la commande `seed`, jamais au demarrage)
    bootstrap_admin_email: str = _env("JAAY_BOOTSTRAP_ADMIN_EMAIL", "")
    bootstrap_admin_password: str = _env("JAAY_BOOTSTRAP_ADMIN_PASSWORD", "")
    bootstrap_admin_phone: str = _env("JAAY_BOOTSTRAP_ADMIN_PHONE", "")

    # Exploitation serverless
    auto_init_db: bool = _bool("JAAY_AUTO_INIT_DB", True)

    # Stockage objet S3 / R2 / compatible (obligatoire pour les uploads sur Vercel)
    s3_bucket: str = _env("JAAY_S3_BUCKET", "")
    s3_endpoint: str = _env("JAAY_S3_ENDPOINT", "")
    s3_region: str = _env("JAAY_S3_REGION", "auto")
    s3_access_key: str = _env("JAAY_S3_ACCESS_KEY", "")
    s3_secret_key: str = _env("JAAY_S3_SECRET_KEY", "")
    s3_public_base_url: str = _env("JAAY_S3_PUBLIC_BASE_URL", "")
    s3_acl: str = _env("JAAY_S3_ACL", "")  # laisser vide sur Cloudflare R2

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def on_vercel(self) -> bool:
        return bool(os.environ.get("VERCEL")) or _bool("JAAY_ON_VERCEL", False)

    @property
    def demo_mode(self) -> bool:
        """Active les comptes de demonstration (auto en dev, explicite en production)."""
        raw = os.environ.get("JAAY_DEMO_MODE")
        if raw is not None and raw.strip():
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return not self.is_production

    @property
    def s3_enabled(self) -> bool:
        return bool(self.s3_bucket and self.s3_access_key and self.s3_secret_key)

    @property
    def media_is_object_storage(self) -> bool:
        return self.media_backend == "s3" and self.s3_enabled

    @property
    def wave_enabled(self) -> bool:
        return self.wave_mode in {"sandbox", "live"} and bool(self.wave_api_key and self.wave_webhook_secret)

    @property
    def om_enabled(self) -> bool:
        return self.om_mode in {"sandbox", "live"} and bool(
            self.om_client_id and self.om_client_secret and self.om_webpayment_url and self.om_webhook_secret
        )

    @property
    def wa_cloud_enabled(self) -> bool:
        return bool(self.wa_cloud_token and self.wa_cloud_phone_number_id and self.wa_cloud_app_secret)


def load_settings() -> Settings:
    s = Settings()
    if s.is_production and not s.secret_key:
        raise RuntimeError("JAAY_SECRET_KEY est obligatoire en production (32+ caracteres aleatoires).")
    if not s.secret_key:
        object.__setattr__(s, "secret_key", _dev_secret())

    # Neon, Supabase et Heroku fournissent souvent postgres:// : SQLAlchemy 2 exige
    # le nom de driver explicite pour psycopg2.
    url = s.database_url
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    object.__setattr__(s, "database_url", url)

    # Sur une plateforme serverless le disque est ephemeral : SQLite perdrait les
    # donnees a chaque instance. On echoue tot avec un message actionnable.
    if s.on_vercel and url.startswith("sqlite"):
        raise RuntimeError(
            "JAAY_DATABASE_URL doit pointer vers PostgreSQL sur Vercel (Neon, Supabase, "
            "Neon Postgres, etc.). Exemple : "
            "postgresql+psycopg2://user:password@host/db?sslmode=require"
        )

    # Stabilise les chemins SQLite relatifs quel que soit le repertoire de lancement.
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        rel = url[len("sqlite:///"):]
        if rel and not os.path.isabs(rel):
            object.__setattr__(s, "database_url", "sqlite:///" + str((ROOT / rel).resolve()))

    media = s.media_dir if s.media_dir.is_absolute() else (ROOT / s.media_dir).resolve()
    # Ne jamais faire planter l'import si le repertoire du projet est en lecture seule.
    object.__setattr__(s, "media_dir", _writable_dir(media))
    return s


settings = load_settings()
