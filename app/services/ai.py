"""Assistance IA vendeur + recommandations.

Mode local deterministe par defaut (aucune cle, aucun cout, fonctionne hors ligne).
Si AI_API_KEY est fournie, appel a une API compatible OpenAI ; en cas d'echec,
retombee automatique sur le mode local. Aucune invente de prix : la suggestion
de prix est uneMEDIANE des produits comparables deja en base.
"""
from __future__ import annotations

import json
import re
import statistics

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..config import settings
from ..models import Product

LANG_NAMES = {"fr": "francais", "en": "anglais", "wo": "wolof"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _local_assist(title: str, keywords: str, language: str) -> dict:
    t = _clean(title)
    words = [w.strip() for w in re.split(r"[,;]", keywords or "") if w.strip()]
    tags = list(dict.fromkeys([w.lower() for w in words] + [t.split()[0].lower()] if t else []))[:8]
    bullets = [
        f"Produit : {t}." if t else "Produit.",
        f"Caracteristiques : {', '.join(words[:5]) if words else 'a preciser par le vendeur'}.",
        "Disponible au Senegal, livraison ou retrait selon la boutique.",
        "Contact direct du vendeur par WhatsApp pour toute question avant achat.",
    ]
    if language == "wo":
        bullets.append("Jàmbat ak jënd : bindal ci WhatsApp ngir gis ndax am na.")
    elif language == "en":
        bullets = [
            f"Product: {t}." if t else "Product.",
            f"Key features: {', '.join(words[:5]) if words else 'to be completed by the seller'}.",
            "Available in Senegal, delivery or pickup depending on the shop.",
            "Contact the seller directly on WhatsApp before ordering.",
        ]
    return {
        "title": t[:90],
        "short_title": t[:55],
        "description": "\n".join(f"• {b}" for b in bullets),
        "tags": tags,
        "seo": f"{t} - {', '.join(tags[:3])} | Jaay",
    }


def _ai_assist(title: str, category: str, keywords: str, language: str) -> dict | None:
    if not settings.ai_api_key:
        return None
    prompt = (
        f"Tu es un assistant e-commerce. Redige en {LANG_NAMES.get(language, 'francais')} une fiche produit "
        "pour une marketplace senegalaise. Reponds UNIQUEMENT en JSON valide avec les cles : "
        "title, short_title, description (4 phrases max, concretes, pas d'invention de caracteristiques "
        "techniques), tags (5 mots), seo. N'invente aucun prix ni aucune marque.\n"
        f"Produit : {title}\nCategorie : {category}\nMots-cles fournis par le vendeur : {keywords}"
    )
    try:
        resp = httpx.post(
            settings.ai_api_url,
            headers={"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"},
            json={"model": settings.ai_model, "messages": [{"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"}, "temperature": 0.4, "max_tokens": 500},
            timeout=20,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        return {
            "title": _clean(str(data.get("title", title)))[:90],
            "short_title": _clean(str(data.get("short_title", title)))[:55],
            "description": str(data.get("description", ""))[:2000],
            "tags": list(data.get("tags", []))[:8],
            "seo": str(data.get("seo", title))[:160],
        }
    except Exception as exc:  # pragma: no cover - depend du reseau
        print(f"[ia] API indisponible, mode local : {exc}")
        return None


def assist_product(title: str, *, category: str = "", keywords: str = "", language: str = "fr") -> dict:
    ai_data = _ai_assist(title, category, keywords, language) if settings.ai_api_key else None
    if ai_data and ai_data.get("description"):
        ai_data["mode"] = "ai"
        return ai_data
    data = _local_assist(title, keywords, language)
    data["mode"] = "local"
    return data


def suggest_price(db: OrmSession, *, title: str, category_id: str | None = None) -> dict:
    """Estimation transparente : mediane des prix des produits comparables en base."""
    q = select(Product.price_xof).where(Product.status == "active", Product.price_xof > 0)
    if category_id:
        q = q.where(Product.category_id == category_id)
    prices = [int(p) for p in db.execute(q.limit(500)).scalars()]
    token = (re.findall(r"[a-z0-9]{4,}", title.lower()) or [""])[0]
    similar = list(
        db.execute(
            select(Product.price_xof).where(Product.title.ilike(f"%{token}%"), Product.price_xof > 0).limit(100)
        ).scalars()
    ) if token else []
    sample = similar or prices
    if not sample:
        return {"available": False, "reason": "Pas encore assez de produits comparables en base."}
    return {
        "available": True,
        "median_xof": int(statistics.median(sample)),
        "min_xof": min(sample),
        "max_xof": max(sample),
        "sample_size": len(sample),
        "disclaimer": "Estimation statistique locale, pas une recommandation commerciale.",
    }


def recommend(db: OrmSession, product: Product, limit: int = 4) -> list[Product]:
    q = select(Product).where(
        Product.status == "active", Product.id != product.id,
        Product.price_xof.between(product.price_xof * 0.5, product.price_xof * 1.8),
    )
    if product.category_id:
        q = q.where(Product.category_id == product.category_id)
    q = q.order_by(Product.view_count.desc(), Product.created_at.desc()).limit(limit)
    items = list(db.execute(q).scalars())
    if len(items) < limit:
        extra = list(
            db.execute(
                select(Product).where(Product.status == "active", Product.id != product.id)
                .order_by(Product.created_at.desc()).limit(limit - len(items))
            ).scalars()
        )
        items.extend(p for p in extra if p.id not in {i.id for i in items})
    return items[:limit]
