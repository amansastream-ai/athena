"""Routes HTML (rendu serveur, mobile-first, aucune librairie externe)."""
from __future__ import annotations

import csv
import io
import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import desc, select
from sqlalchemy.orm import Session as OrmSession

from .config import settings
from .db import get_db
from .models import (
    AuditLog,
    Category,
    Commission,
    CommissionSettlement,
    Coupon,
    Dispute,
    Favorite,
    Inquiry,
    LedgerEntry,
    Notification,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentMethod,
    PaymentProof,
    PaymentStatus,
    PlatformSetting,
    Product,
    ProductImage,
    ProductStatus,
    Review,
    Role,
    Shop,
    ShopStatus,
    User,
    WebhookEvent,
    uid,
    utcnow,
)
from .security import (
    SESSION_COOKIE,
    client_ip,
    apply_cookie,
    cookie_options,
    csrf_for,
    consume_otp,
    create_session,
    hash_password,
    new_otp,
    normalize_phone,
    rate_limit,
    require_admin,
    require_vendor,
    session_from_request,
    sign_payload,
    unsign_payload,
    validate_password,
    verify_csrf,
    verify_password,
)
from .services import ai, audit, fraud, ledger, media, notifications, orders, payments, stats, whatsapp
from .services.registry import all_settings, get_setting, parse_setting_value, set_setting
from .templating import flash_redirect, render
from . import schemas

router = APIRouter()


def _csrf(request: Request, db: OrmSession, token: str) -> None:
    verify_csrf(request, db, token)


def _shop(db: OrmSession, user: User) -> Shop | None:
    return db.execute(select(Shop).where(Shop.owner_id == user.id)).scalar_one_or_none()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:55] or "produit"


def _product_images(db: OrmSession, products: list[Product]) -> dict[str, str]:
    if not products:
        return {}
    rows = db.execute(
        select(ProductImage).where(ProductImage.product_id.in_([p.id for p in products]))
        .order_by(ProductImage.position)
    ).scalars()
    out: dict[str, str] = {}
    for im in rows:
        out.setdefault(im.product_id, im.url)
    return out


# =========================================================================== PUBLIC
@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: OrmSession = Depends(get_db)):
    feat = stats.catalog(db, sort="popular", per_page=8)
    new = stats.catalog(db, sort="recent", per_page=8)
    categories = list(db.execute(select(Category).order_by(Category.sort_order, Category.name)).scalars())
    shops = list(db.execute(select(Shop).where(Shop.status == ShopStatus.ACTIVE).limit(6)).scalars())
    return render(request, db, "home.html", title="La marketplace des boutiques africaines",
                  featured=feat["items"], newest=new["items"],
                  images={**feat["images"], **new["images"]},
                  slugs={**feat["shop_slugs"], **new["shop_slugs"]},
                  categories=categories, shops=shops,
                  rate_bps=int(get_setting(db, "commission_bps", 1000)))


@router.get("/catalogue", response_class=HTMLResponse)
def catalogue(request: Request, q: str = "", categorie: str = "", min_prix: int | None = None,
              max_prix: int | None = None, dispo: bool = False, tri: str = "recent",
              page: int = 1, db: OrmSession = Depends(get_db)):
    data = stats.catalog(db, q=q, category=categorie, min_price=min_prix, max_price=max_prix,
                         in_stock=dispo, sort=tri, page=page, per_page=12)
    categories = list(db.execute(select(Category).order_by(Category.name)).scalars())
    return render(request, db, "catalogue.html", title="Catalogue", result=data,
                  images=data["images"], slugs=data["shop_slugs"], categories=categories, q=q)


@router.get("/boutique/{slug}", response_class=HTMLResponse)
def shop_page(slug: str, request: Request, q: str = "", page: int = 1,
              db: OrmSession = Depends(get_db)):
    shop = db.execute(select(Shop).where(Shop.slug == slug, Shop.status == ShopStatus.ACTIVE)).scalar_one_or_none()
    if not shop:
        return render(request, db, "error.html", title="Boutique introuvable", code=404,
                      message="Cette boutique n'existe pas ou n'est pas encore active.", status_code=404)
    data = stats.catalog(db, q=q, shop_id=shop.id, page=page, per_page=12)
    shop.view_count += 1
    db.commit()
    reviews = list(
        db.execute(select(Review).where(Review.shop_id == shop.id, Review.status == "approved")
                   .order_by(Review.created_at.desc()).limit(5)).scalars()
    )
    return render(request, db, "boutique.html", title=shop.name, shop=shop, result=data,
                  images=data["images"], slugs={shop.id: shop.slug}, reviews=reviews,
                  wa_token=whatsapp.shop_token(shop),
                  share=whatsapp.share_links(request, url=whatsapp.absolute_url(request, f"/boutique/{shop.slug}"),
                                             text=f"Decouvrez {shop.name} sur Jaay"))


@router.get("/boutique/{slug}/produit/{pslug}", response_class=HTMLResponse)
def product_page(slug: str, pslug: str, request: Request, db: OrmSession = Depends(get_db)):
    shop = db.execute(select(Shop).where(Shop.slug == slug)).scalar_one_or_none()
    product = db.execute(
        select(Product).where(Product.slug == pslug, Product.shop_id == shop.id) if shop else select(Product).where(Product.id == "")
    ).scalar_one_or_none()
    if not shop or not product or shop.status != ShopStatus.ACTIVE or product.status != ProductStatus.ACTIVE:
        return render(request, db, "error.html", title="Produit introuvable", code=404,
                      message="Ce produit n'est pas disponible.", status_code=404)
    product.view_count += 1
    db.commit()
    images = list(db.execute(select(ProductImage).where(ProductImage.product_id == product.id)
                             .order_by(ProductImage.position)).scalars())
    reviews = list(db.execute(select(Review).where(Review.product_id == product.id,
                                                   Review.status == "approved")
                              .order_by(Review.created_at.desc()).limit(10)).scalars())
    related = ai.recommend(db, product, limit=4)
    related_slugs = {sid: slug for sid, slug in db.execute(
        select(Shop.id, Shop.slug).where(Shop.id.in_({p.shop_id for p in related} or {""}))).all()}
    url = whatsapp.absolute_url(request, f"/boutique/{shop.slug}/produit/{product.slug}")
    methods = list(db.execute(select(PaymentMethod).where(PaymentMethod.shop_id == shop.id,
                                                          PaymentMethod.is_active.is_(True))).scalars())
    return render(request, db, "produit.html", title=product.title, shop=shop, product=product,
                  images=images, reviews=reviews, related=related, related_images=_product_images(db, related),
                  slugs=related_slugs,
                  wa_token=whatsapp.product_token(product),
                  share=whatsapp.share_links(request, url=url, text=f"{product.title} - {product.price_xof} F sur Jaay"),
                  product_url=url, methods=methods,
                  rate_bps=int(get_setting(db, "commission_bps", 1000)))


@router.get("/wa/{token}")
def whatsapp_redirect(token: str, request: Request, source: str = "", db: OrmSession = Depends(get_db)):
    rate_limit(request, "wa", limit=90)
    return whatsapp.tracked_redirect(request, db, token, source=source)


@router.get("/pages/{slug}", response_class=HTMLResponse)
def page(slug: str, request: Request, db: OrmSession = Depends(get_db)):
    if slug not in {"conditions", "confidentialite", "commission", "aide"}:
        return render(request, db, "error.html", title="Page introuvable", code=404, message="Page inconnue.", status_code=404)
    return render(request, db, f"page_{slug}.html", title=slug.capitalize(),
                  rate_bps=int(get_setting(db, "commission_bps", 1000)),
                  settings_all=all_settings(db))


# =========================================================================== AUTH
@router.get("/connexion", response_class=HTMLResponse)
def login_page(request: Request, db: OrmSession = Depends(get_db)):
    return render(request, db, "auth.html", title="Connexion", mode="login")


@router.post("/connexion")
def login_post(request: Request, identifiant: str = Form(...), mot_de_passe: str = Form(...),
               csrf_token: str = Form(""), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    rate_limit(request, "login", limit=10)
    ident = identifiant.strip().lower()
    user = db.execute(select(User).where((User.email == ident) | (User.phone_e164 == (normalize_phone(identifiant) or "__")))).scalar_one_or_none()
    if not user or not user.is_active or (user.locked_until and user.locked_until > utcnow()):
        return flash_redirect("/connexion", error="Identifiants incorrects ou compte verrouille.")
    if not verify_password(mot_de_passe, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_attempts:
            from datetime import timedelta
            user.locked_until = utcnow() + timedelta(minutes=15)
            user.failed_logins = 0
        db.commit()
        return flash_redirect("/connexion", error="Identifiants incorrects.")
    if not user.phone_verified:
        return flash_redirect("/verifier-telephone", error="Verifiez d'abord votre numero de telephone.")
    raw, _ = create_session(db, user, request)
    resp = flash_redirect("/vendeur" if user.role == Role.VENDOR else
                          "/admin" if user.role == Role.ADMIN else "/", ok=f"Bienvenue {user.full_name} !")
    apply_cookie(resp, SESSION_COOKIE, raw, max_age=settings.session_ttl_hours * 3600,
                 **cookie_options(request, httponly=True))
    return resp


@router.get("/inscription", response_class=HTMLResponse)
def register_page(request: Request, db: OrmSession = Depends(get_db)):
    return render(request, db, "auth.html", title="Creer ma boutique", mode="register")


@router.post("/inscription")
def register_post(request: Request, nom: str = Form(...), telephone: str = Form(...),
                  email: str = Form(""), mot_de_passe: str = Form(...),
                  nom_boutique: str = Form(""), slug_boutique: str = Form(""),
                  whatsapp: str = Form(""), csrf_token: str = Form(""),
                  db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    rate_limit(request, "register", limit=6)
    try:
        data = schemas.RegisterIn(full_name=nom, phone=telephone, email=email or None,
                                  password=mot_de_passe, role="vendor" if nom_boutique else "customer")
    except Exception as exc:
        return flash_redirect("/inscription", error=f"Donnees invalides : {exc}")
    problem = validate_password(data.password)
    if problem:
        return flash_redirect("/inscription", error=problem)
    if db.execute(select(User).where((User.phone_e164 == data.phone) |
                                     (User.email == data.email) if data.email else User.phone_e164 == data.phone)).scalar_one_or_none():
        return flash_redirect("/inscription", error="Ce numero ou cet e-mail est deja inscrit.")
    if data.role == "vendor":
        if len(nom_boutique.strip()) < 2:
            return flash_redirect("/inscription", error="Indiquez le nom de votre boutique.")
        slug = re.sub(r"[^a-z0-9-]", "", (slug_boutique or "").lower().strip()) or _slugify(nom_boutique)
        if len(slug) < 3 or db.execute(select(Shop).where(Shop.slug == slug)).scalar_one_or_none():
            return flash_redirect("/inscription", error="Identifiant de boutique invalide ou deja pris.")
    user = User(
        id=uid(), email=data.email, phone_e164=data.phone, full_name=data.full_name,
        password_hash=hash_password(data.password), role=data.role,
        phone_verified=False, created_at=utcnow(),
    )
    db.add(user)
    db.flush()
    if data.role == "vendor":
        wa = normalize_phone(whatsapp) or data.phone
        db.add(Shop(
            id=uid(), owner_id=user.id, slug=slug, name=nom_boutique.strip(),
            whatsapp_e164=wa, description="", status=ShopStatus.PENDING,
            created_at=utcnow(),
        ))
    db.flush()
    code = new_otp(db, data.phone)
    from .services.sms import send_otp
    send_otp(data.phone, code)
    audit.log_action(db, action="user.registered", actor=user, entity_type="user", entity_id=user.id,
                     after={"role": data.role, "phone": data.phone}, ip=client_ip(request))
    db.commit()
    resp = flash_redirect("/verifier-telephone", ok="Compte cree. Entrez le code recu par SMS.")
    apply_cookie(resp, "jaay_otp_phone", sign_payload({"p": data.phone}, ttl_seconds=900),
                 max_age=900, **cookie_options(request, httponly=True))
    if settings.dev_show_otp and not settings.is_production:
        apply_cookie(resp, "jaay_dev_otp", code, max_age=600, **cookie_options(request))
    return resp


@router.get("/verifier-telephone", response_class=HTMLResponse)
def verify_page(request: Request, db: OrmSession = Depends(get_db)):
    return render(request, db, "auth.html", title="Verifier mon numero", mode="otp",
                  dev_otp=request.cookies.get("jaay_dev_otp", ""))


@router.post("/verifier-telephone")
def verify_post(request: Request, code: str = Form(...), csrf_token: str = Form(""),
                db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    rate_limit(request, "otp", limit=8)
    payload = unsign_payload(request.cookies.get("jaay_otp_phone", ""))
    if not payload:
        return flash_redirect("/inscription", error="Session de verification expiree.")
    phone = payload["p"]
    if not consume_otp(db, phone, code.strip()):
        return flash_redirect("/verifier-telephone", error="Code incorrect ou expire.")
    user = db.execute(select(User).where(User.phone_e164 == phone)).scalar_one_or_none()
    if not user:
        return flash_redirect("/inscription", error="Compte introuvable.")
    user.phone_verified = True
    shop = _shop(db, user)
    if shop:
        shop.status = ShopStatus.ACTIVE
        shop.approved_at = utcnow()
    db.flush()
    raw, _ = create_session(db, user, request)
    audit.log_action(db, action="user.phone_verified", actor=user, entity_type="user", entity_id=user.id,
                     ip=client_ip(request))
    db.commit()
    dest = "/vendeur/boutique" if shop else "/"
    resp = flash_redirect(dest, ok="Numero verifie. Votre boutique est en ligne !" if shop else "Numero verifie.")
    apply_cookie(resp, SESSION_COOKIE, raw, max_age=settings.session_ttl_hours * 3600,
                 **cookie_options(request, httponly=True))
    resp.delete_cookie("jaay_otp_phone", path="/")
    resp.delete_cookie("jaay_dev_otp", path="/")
    return resp


@router.post("/deconnexion")
def logout(request: Request, csrf_token: str = Form(""), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    sess = session_from_request(request, db)
    if sess:
        db.delete(sess)
        db.commit()
    resp = flash_redirect("/", ok="Vous etes deconnecte.")
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# =========================================================================== COMMANDES CLIENT
@router.get("/commandes/{reference}", response_class=HTMLResponse)
def order_page(reference: str, request: Request, db: OrmSession = Depends(get_db)):
    order = db.execute(select(Order).where(Order.reference == reference)).scalar_one_or_none()
    if not order:
        return render(request, db, "error.html", title="Commande introuvable", code=404, message="Reference inconnue.", status_code=404)
    shop = db.get(Shop, order.shop_id)
    view = orders.order_view(db, order)
    instructions = payments.manual_instructions(db, order, shop)
    caps = {p["code"]: p for p in payments.provider_capabilities(db)}
    return render(request, db, "commande.html", title=f"Commande {reference}", order=order,
                  shop=shop, view=view, instructions=instructions, caps=caps,
                  wa_token=whatsapp.shop_token(shop) if shop else "")


@router.post("/commandes/{reference}/preuve")
def proof_post(reference: str, request: Request, fournisseur: str = Form("wave"),
               reference_paiement: str = Form(...), montant: int = Form(...),
               note: str = Form(""), csrf_token: str = Form(""),
               db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    rate_limit(request, "proof", limit=10)
    order = db.execute(select(Order).where(Order.reference == reference)).scalar_one_or_none()
    if not order:
        return flash_redirect("/", error="Commande introuvable.")
    from .security import current_user_optional
    user = current_user_optional(request, db)
    try:
        payments.submit_proof(db, order, user=user, provider=fournisseur,
                              provider_reference=reference_paiement.strip(), amount_xof=montant,
                              note=note, ip=client_ip(request))
    except payments.PaymentError as exc:
        return flash_redirect(f"/commandes/{reference}", error=str(exc))
    return flash_redirect(f"/commandes/{reference}", ok="Preuve envoyee. Un administrateur va la verifier.")


@router.post("/favoris/{product_id}")
def favorite_toggle(product_id: str, request: Request, csrf_token: str = Form(""),
                    db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    from .security import current_user_optional
    user = current_user_optional(request, db)
    if not user:
        return flash_redirect("/connexion", error="Connectez-vous pour ajouter un favori.")
    fav = db.get(Favorite, (user.id, product_id))
    if fav:
        db.delete(fav)
    else:
        db.add(Favorite(user_id=user.id, product_id=product_id, created_at=utcnow()))
    db.commit()
    p = db.get(Product, product_id)
    return flash_redirect(request.headers.get("referer", "/"), ok="Favoris mis a jour." if p else "")


@router.post("/commandes/{reference}/avis")
def review_post(reference: str, request: Request, note: int = Form(...), commentaire: str = Form(""),
                csrf_token: str = Form(""), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    from .security import current_user_optional
    user = current_user_optional(request, db)
    order = db.execute(select(Order).where(Order.reference == reference)).scalar_one_or_none()
    if not user or not order or order.customer_id != user.id:
        return flash_redirect("/connexion", error="Seul l'acheteur peut noter cette commande.")
    if order.payment_status != PaymentStatus.PAID:
        return flash_redirect(f"/commandes/{reference}", error="Avis reserve aux achats confirmes.")
    items = list(db.execute(select(OrderItem).where(OrderItem.order_id == order.id)).scalars())
    for item in items[:1]:
        if db.execute(select(Review).where(Review.order_id == order.id, Review.product_id == item.product_id)).first():
            continue
        db.add(Review(id=uid(), product_id=item.product_id, shop_id=order.shop_id, order_id=order.id,
                      user_id=user.id, rating=note, comment=commentaire, status="approved",
                      created_at=utcnow()))
    db.commit()
    return flash_redirect(f"/commandes/{reference}", ok="Merci pour votre avis !")


# =========================================================================== VENDEUR
@router.get("/vendeur", response_class=HTMLResponse)
def vendor_dashboard(request: Request, user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    if not shop:
        return flash_redirect("/vendeur/boutique", error="Creez d'abord votre boutique.")
    data = stats.shop_dashboard(db, shop.id)
    notifs = list(db.execute(select(Notification).where(Notification.user_id == user.id)
                             .order_by(Notification.created_at.desc()).limit(8)).scalars())
    return render(request, db, "vendeur/tableau-de-bord.html", title="Tableau de bord vendeur",
                  shop=shop, data=data, notifs=notifs)


@router.get("/vendeur/boutique", response_class=HTMLResponse)
def vendor_shop_edit(request: Request, user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    return render(request, db, "vendeur/boutique.html", title="Ma boutique", shop=shop,
                  share=whatsapp.share_links(request, url=whatsapp.absolute_url(request, f"/boutique/{shop.slug}"),
                                             text=f"Ma boutique {shop.name} sur Jaay") if shop else {})


@router.post("/vendeur/boutique")
def vendor_shop_save(request: Request, nom: str = Form(...), description: str = Form(""),
                     whatsapp_num: str = Form(""), ville: str = Form("Dakar"),
                     couleur: str = Form("#0f766e"), csrf_token: str = Form(""),
                     user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = _shop(db, user)
    before = {"name": shop.name, "whatsapp": shop.whatsapp_e164} if shop else None
    wa = normalize_phone(whatsapp_num) or (shop.whatsapp_e164 if shop else user.phone_e164)
    if not shop:
        slug = _slugify(nom)
        if db.execute(select(Shop).where(Shop.slug == slug)).scalar_one_or_none():
            slug = f"{slug}-{uid()[:4]}"
        shop = Shop(id=uid(), owner_id=user.id, slug=slug, whatsapp_e164=wa,
                    status=ShopStatus.ACTIVE, created_at=utcnow(), approved_at=utcnow())
        db.add(shop)
    shop.name = nom.strip()[:150]
    shop.description = description.strip()[:4000]
    shop.whatsapp_e164 = wa
    shop.city = ville.strip()[:100]
    if re.fullmatch(r"#[0-9a-fA-F]{6}", couleur or ""):
        shop.theme_color = couleur
    db.flush()
    audit.log_action(db, action="shop.updated", actor=user, entity_type="shop", entity_id=shop.id,
                     before=before, after={"name": shop.name, "whatsapp": shop.whatsapp_e164},
                     ip=client_ip(request))
    db.commit()
    return flash_redirect("/vendeur/boutique", ok="Boutique enregistree.")


@router.get("/vendeur/produits", response_class=HTMLResponse)
def vendor_products(request: Request, user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    products = list(db.execute(select(Product).where(Product.shop_id == shop.id)
                               .order_by(Product.created_at.desc())).scalars()) if shop else []
    categories = list(db.execute(select(Category).order_by(Category.name)).scalars())
    return render(request, db, "vendeur/produits.html", title="Mes produits", shop=shop,
                  products=products, images=_product_images(db, products), categories=categories)


@router.post("/vendeur/produits")
def vendor_product_create(request: Request, titre: str = Form(...), prix: int = Form(...),
                          stock: int = Form(1), description: str = Form(""),
                          categorie: str = Form(""), livraison: int = Form(0),
                          csrf_token: str = Form(""), user: User = Depends(require_vendor),
                          db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = _shop(db, user)
    if not shop:
        return flash_redirect("/vendeur/boutique", error="Creez d'abord votre boutique.")
    try:
        data = schemas.ProductIn(title=titre, description=description, price_xof=prix,
                                 stock=stock, delivery_fee_xof=livraison,
                                 category_id=categorie or None)
    except Exception as exc:
        return flash_redirect("/vendeur/produits", error=f"Donnees invalides : {exc}")
    slug = _slugify(data.title)
    base = slug
    n = 1
    while db.execute(select(Product).where(Product.shop_id == shop.id, Product.slug == slug)).scalar_one_or_none():
        n += 1
        slug = f"{base}-{n}"
    p = Product(id=uid(), shop_id=shop.id, category_id=data.category_id, slug=slug,
                title=data.title, description=data.description, price_xof=data.price_xof,
                stock=data.stock, delivery_fee_xof=data.delivery_fee_xof,
                status=ProductStatus.ACTIVE, created_at=utcnow())
    db.add(p)
    db.flush()
    audit.log_action(db, action="product.created", actor=user, entity_type="product", entity_id=p.id,
                     after={"title": p.title, "price": p.price_xof}, ip=client_ip(request))
    db.commit()
    return flash_redirect("/vendeur/produits", ok=f"Produit « {p.title} » publie.")


@router.get("/vendeur/produits/{product_id}", response_class=HTMLResponse)
def vendor_product_edit(product_id: str, request: Request, user: User = Depends(require_vendor),
                        db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    p = db.get(Product, product_id)
    if not shop or not p or p.shop_id != shop.id:
        return render(request, db, "error.html", title="Produit introuvable", code=404, message="Produit inconnu.", status_code=404)
    images = list(db.execute(select(ProductImage).where(ProductImage.product_id == p.id)
                             .order_by(ProductImage.position)).scalars())
    cats = list(db.execute(select(Category).order_by(Category.name)).scalars())
    suggestion = ai.suggest_price(db, title=p.title, category_id=p.category_id)
    return render(request, db, "vendeur/produit-edit.html", title="Modifier le produit", shop=shop,
                  product=p, images=images, categories=cats, suggestion=suggestion,
                  wa_token=whatsapp.product_token(p))


@router.post("/vendeur/produits/{product_id}")
def vendor_product_update(product_id: str, request: Request, titre: str = Form(...),
                          prix: int = Form(...), stock: int = Form(1), description: str = Form(""),
                          categorie: str = Form(""), livraison: int = Form(0),
                          statut: str = Form("active"), csrf_token: str = Form(""),
                          user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = _shop(db, user)
    p = db.get(Product, product_id)
    if not shop or not p or p.shop_id != shop.id:
        raise HTTPException(status_code=404)
    before = {"title": p.title, "price": p.price_xof, "stock": p.stock}
    p.title, p.price_xof, p.stock = titre.strip()[:200], max(0, prix), max(0, stock)
    p.description, p.delivery_fee_xof = description.strip()[:8000], max(0, livraison)
    p.category_id = categorie or None
    p.status = statut if statut in {"active", "draft", "archived"} else "active"
    db.flush()
    audit.log_action(db, action="product.updated", actor=user, entity_type="product", entity_id=p.id,
                     before=before, after={"title": p.title, "price": p.price_xof}, ip=client_ip(request))
    db.commit()
    return flash_redirect(f"/vendeur/produits/{p.id}", ok="Produit mis a jour.")


@router.post("/vendeur/produits/{product_id}/image")
async def vendor_product_image(product_id: str, request: Request, image: UploadFile = File(...),
                               csrf_token: str = Form(""), user: User = Depends(require_vendor),
                               db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = _shop(db, user)
    p = db.get(Product, product_id)
    if not shop or not p or p.shop_id != shop.id:
        return flash_redirect("/vendeur/produits", error="Produit introuvable.")
    try:
        url = media.save_image(await image.read(), shop_id=shop.id)
    except media.MediaError as exc:
        return flash_redirect(f"/vendeur/produits/{p.id}", error=str(exc))
    count = int(db.execute(select(ProductImage.id).where(ProductImage.product_id == p.id)).all().__len__())
    db.add(ProductImage(id=uid(), product_id=p.id, url=url, alt=p.title, position=count, created_at=utcnow()))
    db.commit()
    return flash_redirect(f"/vendeur/produits/{p.id}", ok="Image ajoutee (convertie en WebP).")


@router.get("/vendeur/commandes", response_class=HTMLResponse)
def vendor_orders(request: Request, user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    rows = list(db.execute(select(Order).where(Order.shop_id == shop.id)
                           .order_by(Order.created_at.desc()).limit(100)).scalars()) if shop else []
    return render(request, db, "vendeur/commandes.html", title="Mes commandes", shop=shop, orders=rows)


@router.get("/vendeur/commandes/{order_id}", response_class=HTMLResponse)
def vendor_order_detail(order_id: str, request: Request, user: User = Depends(require_vendor),
                        db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    try:
        order = orders.order_for_vendor(db, order_id, shop.id)
    except orders.OrderError:
        return render(request, db, "error.html", title="Commande introuvable", code=404,
                      message="Cette commande ne vous appartient pas.", status_code=404)
    return render(request, db, "vendeur/commande.html", title=f"Commande {order.reference}",
                  shop=shop, view=orders.order_view(db, order),
                  instructions=payments.manual_instructions(db, order, shop))


@router.post("/vendeur/commandes/{order_id}/statut")
def vendor_order_status(order_id: str, request: Request, statut: str = Form(...),
                        note: str = Form(""), csrf_token: str = Form(""),
                        user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = _shop(db, user)
    try:
        order = orders.order_for_vendor(db, order_id, shop.id)
        # Un vendeur peut faire avancer la logistique, jamais confirmer un paiement.
        allowed = {"confirmed", "preparing", "shipped", "delivered", "cancelled"}
        if statut not in allowed:
            raise orders.OrderError("Transition non autorisee pour un vendeur.")
        orders.transition(db, order, statut, actor=user, note=note, ip=client_ip(request))
    except (orders.OrderError, ledger.LedgerError) as exc:
        return flash_redirect(f"/vendeur/commandes/{order_id}", error=str(exc))
    return flash_redirect(f"/vendeur/commandes/{order_id}", ok="Statut mis a jour.")


@router.get("/vendeur/declarations", response_class=HTMLResponse)
def vendor_declarations(request: Request, user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    products = list(db.execute(select(Product).where(Product.shop_id == shop.id)
                               .order_by(Product.title)).scalars()) if shop else []
    rows = list(db.execute(select(Order).where(Order.shop_id == shop.id,
                                               Order.origin == "vendor_declaration")
                           .order_by(Order.created_at.desc()).limit(50)).scalars()) if shop else []
    recent_inquiries = list(db.execute(select(Inquiry).where(Inquiry.shop_id == shop.id)
                                       .order_by(Inquiry.created_at.desc()).limit(10)).scalars()) if shop else []
    return render(request, db, "vendeur/declarations.html", title="Declarer une vente WhatsApp",
                  shop=shop, products=products, declarations=rows, inquiries=recent_inquiries,
                  rate_bps=ledger.effective_rate_bps(db, shop) if shop else 1000)


@router.post("/vendeur/declarations")
def vendor_declaration_post(request: Request, produit: str = Form(...), quantite: int = Form(1),
                            montant: int = Form(...), nom_client: str = Form(...),
                            telephone_client: str = Form(...), code_demande: str = Form(""),
                            fournisseur: str = Form("wave"), reference_paiement: str = Form(""),
                            note: str = Form(""), csrf_token: str = Form(""),
                            user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    rate_limit(request, "declare", limit=20)
    shop = _shop(db, user)
    try:
        data = schemas.DeclarationIn(
            product_id=produit, quantity=quantite, sale_amount_xof=montant,
            customer_name=nom_client, customer_phone=telephone_client,
            claim_code=code_demande.strip().upper(), payment_provider=fournisseur,
            payment_reference=reference_paiement.strip(), note=note,
        )
        order = orders.declare_sale(db, shop=shop, vendor=user, **data.model_dump(), ip=client_ip(request))
    except Exception as exc:
        return flash_redirect("/vendeur/declarations", error=f"Declaration refusee : {exc}")
    rate = ledger.effective_rate_bps(db, shop)
    fee = (data.sale_amount_xof * rate + 5000) // 10000
    return flash_redirect("/vendeur/declarations",
                          ok=f"Vente {order.reference} declaree. Commission estimee {fee} F CFA - en attente de verification.")


@router.get("/vendeur/paiements", response_class=HTMLResponse)
def vendor_payment_methods(request: Request, user: User = Depends(require_vendor),
                           db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    methods = list(db.execute(select(PaymentMethod).where(PaymentMethod.shop_id == shop.id)
                              .order_by(PaymentMethod.priority)).scalars()) if shop else []
    caps = payments.provider_capabilities(db)
    return render(request, db, "vendeur/paiements.html", title="Mes moyens de paiement",
                  shop=shop, methods=methods, caps=caps)


@router.post("/vendeur/paiements")
def vendor_payment_method_post(request: Request, fournisseur: str = Form(...),
                               libelle: str = Form(...), numero: str = Form(""),
                               reference_compte: str = Form(""), csrf_token: str = Form(""),
                               user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = _shop(db, user)
    if fournisseur not in {"wave", "orange_money", "free_money", "bank", "cash"}:
        return flash_redirect("/vendeur/paiements", error="Fournisseur invalide.")
    phone = normalize_phone(numero) if numero else ""
    if numero and not phone:
        return flash_redirect("/vendeur/paiements", error="Numero invalide.")
    db.add(PaymentMethod(
        id=uid(), shop_id=shop.id, provider=fournisseur, label=libelle.strip()[:120],
        phone_e164=phone or "", account_ref=reference_compte.strip()[:150],
        is_verified=False, is_active=True, created_at=utcnow(),
    ))
    audit.log_action(db, action="payment_method.added", actor=user, entity_type="shop", entity_id=shop.id,
                     after={"provider": fournisseur}, ip=client_ip(request))
    db.commit()
    return flash_redirect("/vendeur/paiements", ok="Moyen de paiement ajoute. Verification par l'administrateur en attente.")


@router.get("/vendeur/commissions", response_class=HTMLResponse)
def vendor_commissions(request: Request, user: User = Depends(require_vendor),
                       db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    rows = list(db.execute(select(Commission).where(Commission.shop_id == shop.id)
                           .order_by(Commission.calculated_at.desc()).limit(200)).scalars()) if shop else []
    settlements = list(db.execute(select(CommissionSettlement).where(CommissionSettlement.shop_id == shop.id)
                                  .order_by(CommissionSettlement.created_at.desc())).scalars()) if shop else []
    fin = ledger.financial_stats(db, shop.id) if shop else {}
    return render(request, db, "vendeur/commissions.html", title="Mes commissions",
                  shop=shop, commissions=rows, settlements=settlements, fin=fin,
                  rate_bps=ledger.effective_rate_bps(db, shop) if shop else 1000)


@router.get("/vendeur/assistant", response_class=HTMLResponse)
def vendor_assistant(request: Request, user: User = Depends(require_vendor),
                     db: OrmSession = Depends(get_db)):
    return render(request, db, "vendeur/assistant.html", title="Assistant IA",
                  ai_enabled=bool(settings.ai_api_key))


@router.get("/vendeur/export.csv")
def vendor_export(user: User = Depends(require_vendor), db: OrmSession = Depends(get_db)):
    shop = _shop(db, user)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "date", "client", "telephone", "montant", "statut", "paiement", "commission"])
    for o in db.execute(select(Order).where(Order.shop_id == shop.id).order_by(Order.created_at.desc())).scalars():
        c = db.execute(select(Commission).where(Commission.order_id == o.id)).scalar_one_or_none()
        w.writerow([o.reference, o.created_at.isoformat(), o.customer_name, o.customer_phone_e164,
                    o.total_xof, o.status, o.payment_status, c.platform_fee_xof if c else 0])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="jaay-ventes.csv"'})


# =========================================================================== ADMIN
@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    data = stats.admin_dashboard(db, user.id)
    shops = list(db.execute(select(Shop).order_by(Shop.name)).scalars())
    return render(request, db, "admin/tableau-de-bord.html", title="Administration", data=data,
                  shops_for_settlement=shops, anomaly=fraud.anomaly_report(db, limit=8))


@router.get("/admin/boutiques", response_class=HTMLResponse)
def admin_shops(request: Request, user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    shops = list(db.execute(select(Shop).order_by(Shop.created_at.desc())).scalars())
    owners = {u.id: u for u in db.execute(select(User)).scalars()}
    return render(request, db, "admin/boutiques.html", title="Boutiques", shops=shops, owners=owners,
                  rate_bps=int(get_setting(db, "commission_bps", 1000)))


@router.post("/admin/boutiques/{shop_id}/statut")
def admin_shop_status(shop_id: str, request: Request, statut: str = Form(...),
                      csrf_token: str = Form(""), user: User = Depends(require_admin),
                      db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = db.get(Shop, shop_id)
    if not shop or statut not in {"active", "pending", "suspended"}:
        return flash_redirect("/admin/boutiques", error="Donnees invalides.")
    before = shop.status
    shop.status = statut
    if statut == "active" and not shop.approved_at:
        shop.approved_at = utcnow()
    audit.log_action(db, action="shop.status_changed", actor=user, entity_type="shop", entity_id=shop.id,
                     before={"status": before}, after={"status": statut}, ip=client_ip(request))
    db.commit()
    return flash_redirect("/admin/boutiques", ok=f"Boutique {shop.name} : {statut}.")


@router.post("/admin/boutiques/{shop_id}/taux")
def admin_shop_rate(shop_id: str, request: Request, taux_bps: str = Form(...),
                    csrf_token: str = Form(""), user: User = Depends(require_admin),
                    db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = db.get(Shop, shop_id)
    if not shop:
        return flash_redirect("/admin/boutiques", error="Boutique inconnue.")
    raw = taux_bps.strip()
    value = None if raw == "" else int(raw)
    if value is not None and not 0 <= value <= 10000:
        return flash_redirect("/admin/boutiques", error="Taux invalide (0 a 10000 points de base).")
    before = shop.commission_bps_override
    shop.commission_bps_override = value
    audit.log_action(db, action="shop.commission_override", actor=user, entity_type="shop", entity_id=shop.id,
                     before={"bps": before}, after={"bps": value}, ip=client_ip(request))
    db.commit()
    return flash_redirect("/admin/boutiques", ok="Taux specifique enregistre.")


@router.get("/admin/commandes", response_class=HTMLResponse)
def admin_orders(request: Request, statut: str = "", origine: str = "",
                 user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    q = select(Order).order_by(Order.created_at.desc()).limit(200)
    if statut:
        q = q.where(Order.status == statut)
    if origine:
        q = q.where(Order.origin == origine)
    rows = list(db.execute(q).scalars())
    shops = {s.id: s for s in db.execute(select(Shop)).scalars()}
    return render(request, db, "admin/commandes.html", title="Commandes", orders=rows, shops=shops,
                  statuses=orders.ALL_STATUSES)


@router.get("/admin/commandes/{order_id}", response_class=HTMLResponse)
def admin_order_detail(order_id: str, request: Request, user: User = Depends(require_admin),
                       db: OrmSession = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        return render(request, db, "error.html", title="Commande introuvable", code=404, message="Inconnue.", status_code=404)
    shop = db.get(Shop, order.shop_id)
    return render(request, db, "admin/commande.html", title=f"Commande {order.reference}",
                  view=orders.order_view(db, order), shop=shop,
                  statuses=orders.ALL_STATUSES,
                  balance=ledger.commission_balance(db, shop.id))


@router.post("/admin/commandes/{order_id}/statut")
def admin_order_status(order_id: str, request: Request, statut: str = Form(...),
                       note: str = Form(""), csrf_token: str = Form(""),
                       user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    order = db.get(Order, order_id)
    if not order:
        return flash_redirect("/admin/commandes", error="Commande inconnue.")
    try:
        orders.transition(db, order, statut, actor=user, note=note, ip=client_ip(request))
    except (orders.OrderError, ledger.LedgerError) as exc:
        return flash_redirect(f"/admin/commandes/{order_id}", error=str(exc))
    return flash_redirect(f"/admin/commandes/{order_id}", ok="Statut mis a jour et journalise.")


@router.post("/admin/preuves/{proof_id}")
def admin_proof_review(proof_id: str, request: Request, decision: str = Form(...),
                       note: str = Form(""), csrf_token: str = Form(""),
                       user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    proof = db.get(PaymentProof, proof_id)
    if not proof:
        return flash_redirect("/admin", error="Preuve introuvable.")
    try:
        payments.review_proof(db, proof, admin=user, approve=decision == "accept",
                              review_note=note, ip=client_ip(request))
    except payments.PaymentError as exc:
        return flash_redirect(f"/admin/commandes/{proof.order_id}", error=str(exc))
    return flash_redirect(f"/admin/commandes/{proof.order_id}",
                          ok="Paiement confirme et commission calculee." if decision == "accept"
                          else "Preuve rejetee.")


@router.post("/admin/reglements")
def admin_settlement(request: Request, boutique: str = Form(...), montant: int = Form(...),
                     methode: str = Form("wave"), reference: str = Form(""), note: str = Form(""),
                     csrf_token: str = Form(""), user: User = Depends(require_admin),
                     db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    shop = db.get(Shop, boutique)
    if not shop:
        return flash_redirect("/admin", error="Boutique inconnue.")
    try:
        ledger.record_settlement(db, shop, amount_xof=montant, method=methode,
                                 reference=reference, note=note, actor=user)
        audit.log_action(db, action="commission.settlement", actor=user, entity_type="shop", entity_id=shop.id,
                         after={"amount": montant, "reference": reference}, ip=client_ip(request))
        db.commit()
    except ledger.LedgerError as exc:
        return flash_redirect("/admin", error=str(exc))
    return flash_redirect("/admin", ok=f"Encaissement de {montant} F CFA enregistre.")


@router.get("/admin/reglages", response_class=HTMLResponse)
def admin_settings_page(request: Request, user: User = Depends(require_admin),
                        db: OrmSession = Depends(get_db)):
    return render(request, db, "admin/reglages.html", title="Parametres plateforme",
                  settings_all=all_settings(db), caps=payments.provider_capabilities(db))


@router.post("/admin/reglages")
def admin_settings_save(request: Request, cle: str = Form(...), valeur: str = Form(...),
                        csrf_token: str = Form(""), user: User = Depends(require_admin),
                        db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    before = get_setting(db, cle)
    try:
        value = parse_setting_value(cle, valeur)
        if cle == "commission_bps" and not 0 <= value <= 10000:
            raise ValueError("Taux hors bornes.")
        if cle == "commission_trigger" and value not in {"payment_confirmed", "delivered"}:
            raise ValueError("Declencheur invalide.")
        set_setting(db, cle, value, actor_id=user.id, label=cle)
        audit.log_action(db, action="setting.changed", actor=user, entity_type="setting", entity_id=cle,
                         before={"value": before}, after={"value": value}, ip=client_ip(request))
        db.commit()
    except (ValueError, TypeError) as exc:
        return flash_redirect("/admin/reglages", error=str(exc))
    return flash_redirect("/admin/reglages", ok=f"Parametre « {cle} » mis a jour (trace d'audit).")


@router.get("/admin/journal", response_class=HTMLResponse)
def admin_ledger(request: Request, user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    rows = list(db.execute(select(LedgerEntry).order_by(desc(LedgerEntry.id)).limit(200)).scalars())
    ok, msg = ledger.verify_chain(db)
    aok, amsg = audit.verify_chain(db)
    return render(request, db, "admin/journal.html", title="Journal financier", entries=rows,
                  chain_ok=ok, chain_msg=msg, audit_ok=aok, audit_msg=amsg,
                  fin=ledger.financial_stats(db))


@router.get("/admin/audit", response_class=HTMLResponse)
def admin_audit(request: Request, user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    rows = list(db.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(200)).scalars())
    webhooks = list(db.execute(select(WebhookEvent).order_by(desc(WebhookEvent.received_at)).limit(50)).scalars())
    return render(request, db, "admin/audit.html", title="Journal d'audit", logs=rows, webhooks=webhooks)


@router.get("/admin/paiements", response_class=HTMLResponse)
def admin_payments(request: Request, user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    caps = payments.provider_capabilities(db)
    methods = list(db.execute(select(PaymentMethod).order_by(PaymentMethod.created_at.desc()).limit(100)).scalars())
    shops = {s.id: s for s in db.execute(select(Shop)).scalars()}
    return render(request, db, "admin/paiements.html", title="Configuration des paiements",
                  caps=caps, methods=methods, shops=shops, cfg=settings)


@router.post("/admin/paiements/{method_id}/verifier")
def admin_verify_method(method_id: str, request: Request, csrf_token: str = Form(""),
                        user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    _csrf(request, db, csrf_token)
    m = db.get(PaymentMethod, method_id)
    if m:
        m.is_verified = True
        audit.log_action(db, action="payment_method.verified", actor=user, entity_type="payment_method",
                         entity_id=m.id, after={"shop": m.shop_id, "provider": m.provider},
                         ip=client_ip(request))
        db.commit()
    return flash_redirect("/admin/paiements", ok="Moyen de paiement verifie.")


@router.get("/admin/notifications", response_class=HTMLResponse)
def admin_notifications(request: Request, user: User = Depends(require_admin),
                        db: OrmSession = Depends(get_db)):
    rows = list(db.execute(select(Notification).where(Notification.user_id == user.id)
                           .order_by(desc(Notification.created_at)).limit(200)).scalars())
    for n in rows:
        n.is_read = True
    db.commit()
    return render(request, db, "admin/notifications.html", title="Notifications", notifications=rows)


@router.get("/admin/export.csv")
def admin_export(user: User = Depends(require_admin), db: OrmSession = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["reference", "date", "boutique", "montant", "statut", "paiement", "commission", "frais_paiement", "net_vendeur"])
    shops = {s.id: s.name for s in db.execute(select(Shop)).scalars()}
    for o in db.execute(select(Order).order_by(Order.created_at.desc())).scalars():
        c = db.execute(select(Commission).where(Commission.order_id == o.id)).scalar_one_or_none()
        w.writerow([o.reference, o.created_at.isoformat(), shops.get(o.shop_id, ""), o.total_xof,
                    o.status, o.payment_status, c.platform_fee_xof if c else 0,
                    c.payment_fee_xof if c else 0, c.net_vendor_xof if c else 0])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="jaay-finance.csv"'})
