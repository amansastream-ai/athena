"""API JSON : capacites de paiement, instructions, initiation de checkout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..config import settings
from ..db import get_db
from ..models import Order, Shop
from ..security import verify_csrf
from ..services import payments, whatsapp

router = APIRouter(prefix="/api/payments", tags=["payments"])


@router.get("/capabilities")
def capabilities(db: OrmSession = Depends(get_db)):
    return {"providers": payments.provider_capabilities(db),
            "disclaimer": "Une integration n'est annoncee operationnelle qu'apres obtention des acces "
                          "marchands et tests sandbox reels. Voir docs/05-paiements-wave-orange-money.md."}


@router.get("/instructions/{reference}")
def instructions(reference: str, db: OrmSession = Depends(get_db)):
    order = db.execute(select(Order).where(Order.reference == reference)).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Commande introuvable.")
    shop = db.get(Shop, order.shop_id)
    return payments.manual_instructions(db, order, shop)


@router.post("/checkout/{provider}/{reference}")
def initiate(provider: str, reference: str, request: Request, db: OrmSession = Depends(get_db)):
    """Cree une session de paiement chez Wave ou Orange Money si (et seulement si) active."""
    verify_csrf(request, db, request.headers.get("x-csrf-token"))
    order = db.execute(select(Order).where(Order.reference == reference)).scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Commande introuvable.")
    if order.payment_status == "paid":
        raise HTTPException(409, "Commande deja payee.")
    public_base_url = whatsapp.absolute_url(request, "")
    try:
        if provider == "wave":
            result = payments.WaveCheckoutProvider().create_session(
                db, order, public_base_url=public_base_url)
        elif provider == "orange_money":
            result = payments.OrangeMoneyWebPayProvider().create_session(
                db, order, public_base_url=public_base_url)
        else:
            raise HTTPException(400, "Fournisseur inconnu.")
    except payments.PaymentError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Fournisseur indisponible : {exc}")
    return {"launch_url": result["launch_url"], "session_id": result["session_id"],
            "warning": "La redirection client ne confirme pas le paiement ; seul le webhook signe le fait."}
