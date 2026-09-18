"""Semillas: tenant Crimson, roles, admin, impuestos, categorias y catalogo demo.

Uso:  uv run python -m app.seeds --admin-email gerencia@ejemplo.com --admin-password '<fuerte>'
Nunca crear cuentas reales con contrasenas triviales; el script rechaza passwords debiles.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.db import Base, SessionLocal, engine
from .core.deps import ROLE_PERMISSIONS
from .core.security import hash_password, password_is_strong
from .models import (
    BankAccount,
    Category,
    Currency,
    Customer,
    ExchangeRate,
    ExpenseCategory,
    Product,
    ProductTax,
    Role,
    StockMovement,
    Tax,
    Tenant,
    TenantUser,
    User,
    Warehouse,
)
from .services.sequences import DEFAULT_GROUPS, get_or_create_group

DEMO_PRODUCTS = [
    ("CAM-DOME-4MP", "Camara domo IP 4MP ColorVu", "producto", 68500, "4223200000100", "Camara de videovigilancia IP 4MP con vision nocturna a color"),
    ("CAM-PTZ-25X", "Camara PTZ 25x DarkFighter", "producto", 412000, "4223200000100", "Camara PTZ con zoom optico 25x"),
    ("NVR-8CH", "NVR 8 canales 4K PoE", "producto", 189000, "4223200000200", "Grabador de red 8 canales con PoE"),
    ("SW-POE-8", "Switch PoE 8 puertos", "producto", 74500, "4522700000300", "Switch administrable PoE+ 8 puertos"),
    ("AC-FACE", "Terminal control de acceso facial", "producto", 236000, "4223200000400", "Terminal de reconocimiento facial con tarjeta"),
    ("UPS-1500", "UPS 1500VA linea interactiva", "producto", 128000, "4211500000100", "Respaldo electrico para rack"),
    ("SRV-INST-CAM", "Instalacion y configuracion por camara", "servicio", 25000, "8121100000100", "Montaje, cableado, configuracion y prueba"),
    ("SRV-CAB", "Punto de red certificado (Cat6)", "servicio", 32000, "4322200000100", "Cableado estructurado con certificacion"),
    ("SRV-MANT", "Mantenimiento preventivo mensual", "servicio", 45000, "8121100000100", "Visita mensual, limpieza y revision de sistema"),
]

DEMO_CUSTOMERS = [
    ("juridica", "3-101-123456", "Condominio Hacienda Pinilla", "administracion@hpinilla.example", "+506 8888 0001"),
    ("juridica", "3-101-654321", "Residencial La Julieta Sur", "junta@lajulieta.example", "+506 8888 0002"),
    ("fisica", "2-0555-0888", "Andres Solano", "andres@ejemplo.com", "+506 8888 0003"),
    ("juridica", "3-102-777888", "Hotel Vista Volcan", "gerencia@vistavolcan.example", "+506 8888 0004"),
]


def seed(db: Session, admin_email: str, admin_password: str) -> None:
    if not password_is_strong(admin_password):
        raise SystemExit("La contrasena del admin debe tener >= 10 caracteres con letras y numeros")

    for code, perms in ROLE_PERMISSIONS.items():
        if not db.scalar(select(Role).where(Role.code == code)):
            db.add(Role(code=code, name=code.capitalize(), permissions=perms))
    db.flush()

    t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
    if not t:
        t = Tenant(
            slug="crimson",
            name="Crimson Consulting",
            legal_name="Grupo Empresarial Crimson SRL",
            tax_id="3-102-000000",
            sector="Seguridad y tecnologia",
            default_currency="CRC",
            plan="pro",
            settings={
                "invoice_valid_days": 8,
                "quote_valid_days": 15,
                "quote_footer": "Precios en colones. Vigencia 15 dias. Pago por SINPE Movil o transferencia.",
                "activity_codes": ["6202.0", "4322.0"],
                "bcc": [],
            },
        )
        db.add(t)
        db.flush()
        for code, default in (("CRC", True), ("USD", False)):
            db.add(Currency(tenant_id=t.id, code=code, is_default=default))
        for doc_type in DEFAULT_GROUPS:
            get_or_create_group(db, t.id, doc_type)

    u = db.scalar(select(User).where(User.email == admin_email.lower()))
    if not u:
        u = User(email=admin_email.lower(), full_name="Administrador Crimson", password_hash=hash_password(admin_password))
        db.add(u)
        db.flush()
    if not db.scalar(select(TenantUser).where(TenantUser.user_id == u.id, TenantUser.tenant_id == t.id)):
        db.add(TenantUser(tenant_id=t.id, user_id=u.id, role_code="admin"))

    if not db.scalar(select(Tax).where(Tax.tenant_id == t.id)):
        iva13 = Tax(tenant_id=t.id, name="IVA - Tarifa general 13%", code="01", rate_code="08", rate=13)
        db.add_all(
            [
                iva13,
                Tax(tenant_id=t.id, name="IVA - Tarifa reducida 4%", code="01", rate_code="03", rate=4),
                Tax(tenant_id=t.id, name="IVA - Tarifa reducida 2%", code="01", rate_code="02", rate=2),
                Tax(tenant_id=t.id, name="IVA - Tarifa reducida 1%", code="01", rate_code="01", rate=1),
                Tax(tenant_id=t.id, name="Exento 0%", code="01", rate_code="00", rate=0),
            ]
        )
        db.flush()
        cats = {n: Category(tenant_id=t.id, name=n) for n in ("Videovigilancia", "Redes", "Seguridad y acceso", "Energia y respaldo", "Servicios")}
        db.add_all(cats.values())
        db.flush()
        catmap = {
            "CAM": "Videovigilancia",
            "NVR": "Videovigilancia",
            "SW-": "Redes",
            "AC-": "Seguridad y acceso",
            "UPS": "Energia y respaldo",
            "SRV": "Servicios",
        }
        for code, name, kind, price, cabys, desc in DEMO_PRODUCTS:
            cat = next((cats[v] for k, v in catmap.items() if code.startswith(k)), None)
            pr = Product(
                tenant_id=t.id,
                code=code,
                name=name,
                item_type=kind,
                price=price,
                cabys_code=cabys,
                description_invoice=desc,
                category_id=cat.id if cat else None,
                show_on_web=kind == "producto",
                min_stock=2 if kind == "producto" else 0,
            )
            pr.taxes.append(ProductTax(tax_id=iva13.id))
            db.add(pr)
        for id_type, id_number, name, email, phone in DEMO_CUSTOMERS:
            db.add(Customer(tenant_id=t.id, id_type=id_type, id_number=id_number, name=name, email=email, phone=phone, whatsapp=phone))

    if not db.scalar(select(Warehouse).where(Warehouse.tenant_id == t.id)):
        w = Warehouse(tenant_id=t.id, name="Bodega principal", location="Alajuela", is_default=True)
        db.add(w)
        db.add(Warehouse(tenant_id=t.id, name="Vehiculo tecnico", location="Ruta", is_default=False))
        db.add_all(
            [
                ExpenseCategory(tenant_id=t.id, name=n)
                for n in ("Administracion", "Combustible", "Planilla", "Servicios y subcontratistas", "Equipos y materiales", "Alquiler")
            ]
        )
        db.add(BankAccount(tenant_id=t.id, name="Cuenta principal CRC", bank="BAC", currency="CRC", number="CR00000000000000000000"))
        db.flush()
        for pr in db.scalars(select(Product).where(Product.tenant_id == t.id, Product.item_type == "producto")):
            db.add(
                StockMovement(
                    tenant_id=t.id,
                    product_id=pr.id,
                    warehouse_id=w.id,
                    kind="entrada",
                    quantity=6 if "CAM" in pr.code else 3,
                    reference="INV-INICIAL",
                    at=datetime.now(UTC),
                )
            )
    if not db.scalar(select(ExchangeRate).where(ExchangeRate.date == date.today())):
        db.add(
            ExchangeRate(
                date=date.today(), currency="USD", sell=Decimal("512.35"), buy=Decimal("505.10"), source="manual", note="semilla; el worker BCCR lo reemplaza"
            )
        )
    db.commit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--create-tables", action="store_true", help="solo desarrollo (sin Alembic)")
    args = ap.parse_args()
    if args.create_tables:
        Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, args.admin_email, args.admin_password)
    print("Semillas listas: tenant 'crimson', admin", args.admin_email)
