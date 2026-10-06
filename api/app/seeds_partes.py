"""Ejemplo de partes y aliados (oportunidad Yobel) para desarrollo local y pruebas. NUNCA en produccion.

Nodo Latam contrata a Crimson para Yobel; Crimson subcontrata a Hauset la fibra (costo pendiente) y Nodo aporta
el Fortinet. Uso local:  uv run python -m app.seeds_partes
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.config import settings
from .models import AllyCostRequest, AllyParticipation, Customer, Opportunity, Supplier
from .schemas.sales import DocumentIn, LineInSchema
from .services import documents as docsvc
from .services.sequences import next_number

TITLE = "Yobel · red y seguridad perimetral"


def seed_ejemplo_yobel(db: Session, tenant_id: int, user_id: int) -> Opportunity:
    if settings.is_prod:
        raise RuntimeError("El ejemplo Yobel es solo para desarrollo y pruebas: no se carga en produccion")
    ya = db.scalar(select(Opportunity).where(Opportunity.tenant_id == tenant_id, Opportunity.title == TITLE))
    if ya:
        return ya

    def cliente(nombre: str) -> Customer:
        c = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id, Customer.name == nombre))
        if not c:
            c = Customer(tenant_id=tenant_id, name=nombre, id_type="juridica")
            db.add(c)
            db.flush()
        return c

    nodo, yobel = cliente("Nodo Latam"), cliente("Yobel")
    hauset = db.scalar(select(Supplier).where(Supplier.tenant_id == tenant_id, Supplier.name == "Hauset"))
    if not hauset:
        hauset = Supplier(tenant_id=tenant_id, name="Hauset")
        db.add(hauset)
        db.flush()

    q = docsvc.create_quote(
        db,
        tenant_id,
        user_id,
        DocumentIn(
            customer_id=nodo.id,
            currency="USD",
            lines=[
                LineInSchema(name="Firewall Fortinet FortiGate 60F", quantity=Decimal(1), unit_price=Decimal(0), treatment="aportado", supplied_by="Nodo Latam"),
                LineInSchema(name="Configuración de firewall y VLAN", unit="servicio", quantity=Decimal(1), unit_price=Decimal(450)),
            ],
        ),
    )
    number, _ = next_number(db, tenant_id, "OPO")
    o = Opportunity(
        tenant_id=tenant_id,
        number=number,
        title=TITLE,
        kind="proyecto",
        customer_id=nodo.id,
        end_customer_id=yobel.id,
        site="Centro de distribución Yobel, Heredia",
        source="Alianza",
        solution="redes",
        owner_id=user_id,
        created_by=user_id,
        amount=q.total,
        currency="USD",
        probability=60,
        status="cotizando",
        quote_id=q.id,
        notes="Ejemplo: Nodo Latam contrata a Crimson para Yobel; Hauset hace la fibra.",
    )
    db.add(o)
    db.flush()
    a = AllyParticipation(
        tenant_id=tenant_id,
        opportunity_id=o.id,
        supplier_id=hauset.id,
        name=hauset.name,
        role="subcontratista",
        scope="Tendido y fusión de fibra entre bodegas",
        scope_lines=[],
        contacts={"tecnico": {"name": "Técnico Hauset", "email": None, "phone": None}},
        created_by=user_id,
    )
    db.add(a)
    db.flush()
    db.add(
        AllyCostRequest(
            tenant_id=tenant_id,
            participation_id=a.id,
            opportunity_id=o.id,
            what="Costo de fibra óptica (tendido y fusiones)",
            responsible_id=user_id,
            due_date=date.today() + timedelta(days=3),
            status="solicitado",
            currency="USD",
            attachments=[],
            created_by=user_id,
        )
    )
    db.flush()
    return o


if __name__ == "__main__":
    from .core.db import SessionLocal
    from .models import TenantUser

    with SessionLocal() as s:
        m = s.scalar(select(TenantUser).where(TenantUser.role_code == "admin", TenantUser.active))
        if not m:
            raise SystemExit("No hay un admin: corré primero las semillas base")
        opp = seed_ejemplo_yobel(s, m.tenant_id, m.user_id)
        s.commit()
        print(f"Oportunidad {opp.number} · {opp.title}")
