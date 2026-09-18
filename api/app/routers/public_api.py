"""API publica v1 (plan 2.3 / 8): credenciales por tenant (kid + secret), CRUD basico y checkout por JWT; supera a Fygaro
al exponer facturas, clientes y productos ademas de pagos. Webhooks salientes firmados HMAC 't=..,v1=..'."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.crypto import decrypt, encrypt
from ..core.db import get_db
from ..core.deps import Principal, require
from ..core.security import hash_token
from ..models import ApiCredential, Customer, Invoice, Product, Tenant
from ..schemas.crm import CustomerIn, CustomerOut, ProductOut
from ..schemas.sales import DocumentIn, InvoiceOut
from ..services import documents as docsvc
from ..services import inventory as inv

admin = APIRouter(prefix="/settings/api-credentials", tags=["ajustes"])
v1 = APIRouter(prefix="/v1", tags=["api publica"])


# ---------- Admin: credenciales ----------
class CredIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    webhook_url: str | None = None


@admin.get("")
def list_creds(p: Principal = Depends(require("settings", "ver")), db: Session = Depends(get_db)):
    return [
        {"id": c.id, "kid": c.kid, "name": c.name, "webhook_url": c.webhook_url, "active": c.active, "last_used_at": c.last_used_at, "created_at": c.created_at}
        for c in db.scalars(select(ApiCredential).where(ApiCredential.tenant_id == p.tenant.id).order_by(ApiCredential.id.desc()))
    ]


@admin.post("", status_code=201)
def create_cred(data: CredIn, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    kid = "pk_" + secrets.token_hex(8)
    secret = "sk_" + secrets.token_urlsafe(32)
    c = ApiCredential(
        tenant_id=p.tenant.id, kid=kid, name=data.name, secret_hash=hash_token(secret), secret_encrypted=encrypt(secret), webhook_url=data.webhook_url
    )
    db.add(c)
    db.commit()
    return {"id": c.id, "kid": kid, "secret": secret, "note": "El secreto se muestra una sola vez."}


@admin.delete("/{cid}", status_code=204)
def revoke_cred(cid: int, p: Principal = Depends(require("settings", "configurar")), db: Session = Depends(get_db)):
    c = db.get(ApiCredential, cid)
    if not c or c.tenant_id != p.tenant.id:
        raise HTTPException(404, "Credencial no encontrada")
    c.active = False
    db.commit()


# ---------- Autenticacion de la API publica ----------
def api_tenant(
    db: Session = Depends(get_db), x_api_key: str | None = Header(None, alias="X-Api-Key"), x_api_secret: str | None = Header(None, alias="X-Api-Secret")
) -> tuple[Tenant, ApiCredential]:
    if not x_api_key or not x_api_secret:
        raise HTTPException(401, "Faltan X-Api-Key / X-Api-Secret")
    c = db.scalar(select(ApiCredential).where(ApiCredential.kid == x_api_key, ApiCredential.active))
    if not c or not hmac.compare_digest(c.secret_hash, hash_token(x_api_secret)):
        raise HTTPException(401, "Credenciales inválidas")
    c.last_used_at = datetime.now(UTC)
    t = db.get(Tenant, c.tenant_id)
    if not t or not t.active:
        raise HTTPException(403, "Empresa inactiva")
    return t, c


@v1.get("/customers", response_model=list[CustomerOut])
def v1_customers(q: str | None = None, ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    stmt = select(Customer).where(Customer.tenant_id == t.id, Customer.active)
    if q:
        stmt = stmt.where(Customer.name.ilike(f"%{q}%"))
    return db.scalars(stmt.limit(100)).all()


@v1.post("/customers", response_model=CustomerOut, status_code=201)
def v1_customer_create(data: CustomerIn, ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    c = Customer(tenant_id=t.id, **data.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@v1.get("/products", response_model=list[ProductOut])
def v1_products(q: str | None = None, ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    stmt = select(Product).where(Product.tenant_id == t.id, Product.active)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    out = []
    for p in db.scalars(stmt.limit(200)):
        o = ProductOut.model_validate(p)
        o.tax_ids = [x.tax_id for x in p.taxes]
        o.tax_rate = float(p.taxes[0].tax.rate) if p.taxes else None
        out.append(o)
    return out


@v1.get("/inventory")
def v1_inventory(ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    return inv.stock_levels(db, t.id)


@v1.get("/invoices", response_model=list[InvoiceOut])
def v1_invoices(status: str | None = None, ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    stmt = select(Invoice).where(Invoice.tenant_id == t.id)
    if status:
        stmt = stmt.where(Invoice.status == status)
    return db.scalars(stmt.order_by(Invoice.id.desc()).limit(100)).all()


@v1.post("/invoices", response_model=InvoiceOut, status_code=201)
def v1_invoice_create(data: DocumentIn, doc_type: str = "FE", ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    i = docsvc.create_invoice(db, t.id, None, data, doc_type)
    inv.deduct_for_invoice(db, i, None)
    db.commit()
    return i


@v1.post("/payment-links/{invoice_id}")
def v1_payment_link(invoice_id: int, ctx=Depends(api_tenant), db: Session = Depends(get_db)):
    t, _ = ctx
    i = db.get(Invoice, invoice_id)
    if not i or i.tenant_id != t.id:
        raise HTTPException(404, "Factura no encontrada")
    link = docsvc.get_or_create_payment_link(db, t.id, i)
    db.commit()
    return {"url": link.url, "expires_at": link.expires_at}


# ---------- Checkout por JWT (mismo estandar que exige el plan: HS256 + kid; amount, currency, custom_reference, exp, nbf) ----------
class CheckoutJwtIn(BaseModel):
    token: str


@v1.post("/checkout")
def v1_checkout(data: CheckoutJwtIn, db: Session = Depends(get_db)):
    try:
        header = jwt.get_unverified_header(data.token)
        kid = header.get("kid")
        c = db.scalar(select(ApiCredential).where(ApiCredential.kid == kid, ApiCredential.active)) if kid else None
        if not c:
            raise HTTPException(401, "kid desconocido")
        payload = jwt.decode(data.token, decrypt(c.secret_encrypted), algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, f"JWT inválido: {e}") from e
    for k in ("amount", "currency", "custom_reference"):
        if k not in payload:
            raise HTTPException(422, f"Falta {k}")
    # Se crea una factura minima (tiquete) por el monto y se devuelve la URL de pago publica
    t = db.get(Tenant, c.tenant_id)
    doc = DocumentIn(
        currency=payload["currency"],
        external_order=str(payload["custom_reference"]),
        lines=[
            {
                "name": payload.get("description") or f"Pago {payload['custom_reference']}",
                "quantity": 1,
                "unit_price": payload["amount"],
                "tax_rate": payload.get("tax", 0),
            }
        ],
    )
    i = docsvc.create_invoice(db, t.id, None, doc, "TE")
    link = docsvc.get_or_create_payment_link(db, t.id, i)
    db.commit()
    return {"invoice_id": i.id, "number": i.number, "checkout_url": link.url, "expires_at": link.expires_at}


# ---------- Webhooks salientes ----------
def sign_outgoing(secret: str, body: bytes) -> str:
    ts = int(time.time())
    return f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + body, hashlib.sha256).hexdigest()}"


def notify_webhooks(db: Session, tenant_id: int, event: str, data: dict) -> int:
    """Envia el evento a cada credencial con webhook_url (mejor esfuerzo; el worker reintenta en produccion)."""
    n = 0
    body = json.dumps({"type": event, "created": int(time.time()), "data": data}, default=str).encode()
    for c in db.scalars(select(ApiCredential).where(ApiCredential.tenant_id == tenant_id, ApiCredential.active, ApiCredential.webhook_url.is_not(None))):
        try:
            httpx.post(
                c.webhook_url,
                content=body,
                headers={"Content-Type": "application/json", "X-Signature": sign_outgoing(decrypt(c.secret_encrypted), body)},
                timeout=10,
            )
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n
