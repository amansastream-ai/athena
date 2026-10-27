"""Stockage et optimisation des images (WebP, redimensionnement, validation MIME reelle).

Deux backends :
  * ``local`` : disque du projet (developpement / VPS). NE FONCTIONNE PAS sur
    Vercel : le systeme de fichiers y est en lecture seule et ephemeral.
  * ``s3``    : tout bucket compatible S3 (AWS S3, Cloudflare R2, MinIO...).
    Active avec JAAY_MEDIA_BACKEND=s3 + JAAY_S3_*.

L'interface ``save_image`` est identique dans les deux cas : les routes n'ont
pas a changer lors du passage au stockage objet.
"""
from __future__ import annotations

import io
import secrets

from PIL import Image, UnidentifiedImageError

from ..config import settings

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_PIXELS = 1600


class MediaError(Exception):
    pass


def _prepare(data: bytes) -> tuple[bytes, str]:
    """Valide reellement le contenu, convertit en WebP et retourne (octets, nom)."""
    if not data:
        raise MediaError("Fichier vide.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise MediaError(f"Fichier trop volumineux (max {settings.max_upload_mb} Mo).")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        if f"image/{fmt.lower()}" not in ALLOWED_MIME and fmt != "JPEG":
            raise MediaError("Format d'image non autorise (JPEG, PNG, WebP, GIF).")
        img = img.convert("RGB")
        img.thumbnail((MAX_PIXELS, MAX_PIXELS), Image.Resampling.LANCZOS)
    except UnidentifiedImageError:
        raise MediaError("Le contenu du fichier n'est pas une image valide.")
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=82, method=4)
    return buf.getvalue(), secrets.token_hex(12) + ".webp"


def _save_local(webp: bytes, *, shop_id: str, kind: str, name: str) -> str:
    target_dir = settings.media_dir / kind / shop_id
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / name).write_bytes(webp)
    except OSError as exc:
        raise MediaError(
            "Ecriture impossible sur ce serveur. Sur Vercel, configurez un stockage "
            "objet (JAAY_MEDIA_BACKEND=s3 + JAAY_S3_*)."
        ) from exc
    return f"/media/{kind}/{shop_id}/{name}"


def _save_s3(webp: bytes, *, shop_id: str, kind: str, name: str) -> str:
    if not settings.s3_enabled:
        raise MediaError(
            "JAAY_MEDIA_BACKEND=s3 mais JAAY_S3_BUCKET / JAAY_S3_ACCESS_KEY / "
            "JAAY_S3_SECRET_KEY sont incomplets."
        )
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover
        raise MediaError("boto3 n'est pas installe (pip install -r requirements.txt).") from exc

    key = f"{kind}/{shop_id}/{name}"
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint or None,
        region_name=settings.s3_region or None,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
    )
    kwargs = {
        "Bucket": settings.s3_bucket,
        "Key": key,
        "Body": webp,
        "ContentType": "image/webp",
        "CacheControl": "public, max-age=31536000",
    }
    if settings.s3_acl:  # R2 n'accepte pas les ACL S3 : laisser vide.
        kwargs["ACL"] = settings.s3_acl
    try:
        client.put_object(**kwargs)
    except Exception as exc:
        raise MediaError(f"Envoi vers le stockage objet impossible : {exc}") from exc

    if settings.s3_public_base_url:
        return settings.s3_public_base_url.rstrip("/") + "/" + key
    if settings.s3_endpoint:
        return f"{settings.s3_endpoint.rstrip('/')}/{settings.s3_bucket}/{key}"
    return f"https://{settings.s3_bucket}.s3.{settings.s3_region}.amazonaws.com/{key}"


def save_image(data: bytes, *, shop_id: str, kind: str = "products") -> str:
    webp, name = _prepare(data)
    if settings.media_is_object_storage:
        return _save_s3(webp, shop_id=shop_id, kind=kind, name=name)
    if settings.media_backend == "s3":
        raise MediaError(
            "Stockage objet demande mais non configure. Renseignez JAAY_S3_BUCKET, "
            "JAAY_S3_ACCESS_KEY, JAAY_S3_SECRET_KEY, JAAY_S3_ENDPOINT."
        )
    return _save_local(webp, shop_id=shop_id, kind=kind, name=name)
