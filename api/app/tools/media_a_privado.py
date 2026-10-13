"""Pasa a PRIVADOS los archivos que ya existian y que solo usa la evidencia interna. OPCIONAL: no lo corre nadie
automaticamente (la migracion p1seg deja todo lo existente publico para no romper ningun enlace).

    uv run python -m app.tools.media_a_privado                  # vista previa (no escribe)
    uv run python -m app.tools.media_a_privado --apply          # aplica, en una sola transaccion
    uv run python -m app.tools.media_a_privado --tenant crimson --apply

Regla conservadora: un archivo pasa a privado solo si lo referencia un registro interno (levantamiento y sus
puntos, OT, proyecto, gasto, ticket y sus notas, equipo instalado) y NINGUN contexto publico (productos, variantes
logo, eventos, paginas de la tienda, archivos de aliados). Su modulo de origen queda segun quien lo usa.

Que deja de funcionar despues de aplicar: enlaces directos ya copiados fuera del sistema (WhatsApp, correos viejos
con la foto incrustada). Dentro del sistema todo sigue: el portal pide firma, PDF/informes y portal del cliente firman.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from sqlalchemy import select

from ..core.db import SessionLocal
from ..models import (
    AllyCostRequest,
    CustomerAsset,
    Event,
    Expense,
    Media,
    Product,
    Project,
    StorePage,
    SupportNote,
    SupportTicket,
    Survey,
    SurveyPoint,
    Tenant,
    WorkOrder,
)
from ..services.media_access import key_of

_KEY = re.compile(r"/media/f/([A-Za-z0-9_\-]+)")


def _keys(v) -> set[str]:
    """Todas las llaves de multimedia que aparezcan en un valor (string, lista o JSON anidado)."""
    return set(_KEY.findall(json.dumps(v, default=str))) if v else set()


def plan(db, tenant_id: int) -> dict[str, str]:
    """{llave: modulo} de lo que pasaria a privado."""
    internas: dict[str, str] = {}

    def marcar(v, module: str):
        for k in _keys(v):
            internas.setdefault(k, module)

    for s in db.scalars(select(Survey).where(Survey.tenant_id == tenant_id)):
        marcar(s.photos, "field")
        for p in db.scalars(select(SurveyPoint).where(SurveyPoint.survey_id == s.id)):
            marcar(p.photos, "field")
    for o in db.scalars(select(WorkOrder).where(WorkOrder.tenant_id == tenant_id)):
        marcar(o.photos, "field")
    for pr in db.scalars(select(Project).where(Project.tenant_id == tenant_id)):
        marcar(pr.photos, "projects")
    for e in db.scalars(select(Expense).where(Expense.tenant_id == tenant_id)):
        k = key_of(e.attachment_url)
        if k:
            internas.setdefault(k, "accounting")
    for t in db.scalars(select(SupportTicket).where(SupportTicket.tenant_id == tenant_id)):
        marcar(t.photos, "support_desk")
    for n in db.scalars(select(SupportNote).where(SupportNote.tenant_id == tenant_id)):
        marcar(n.photos, "support_desk")
    for a in db.scalars(select(CustomerAsset).where(CustomerAsset.tenant_id == tenant_id)):
        marcar(a.photos, "assets")

    publicas: set[str] = set()
    t = db.get(Tenant, tenant_id)
    publicas |= _keys(t.logo_url) | _keys(t.settings)
    for p in db.scalars(select(Product).where(Product.tenant_id == tenant_id)):
        publicas |= _keys(p.images)
    for ev in db.scalars(select(Event).where(Event.tenant_id == tenant_id)):
        publicas |= _keys(ev.image_url)
    for pg in db.scalars(select(StorePage).where(StorePage.tenant_id == tenant_id)):
        publicas |= _keys(pg.blocks)
    for r in db.scalars(select(AllyCostRequest).where(AllyCostRequest.tenant_id == tenant_id)):
        publicas |= _keys(r.attachments)  # aliados: sus adjuntos se comparten con terceros

    return {k: m for k, m in internas.items() if k not in publicas}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tenant", help="slug de la empresa (por defecto todas)")
    ap.add_argument("--apply", action="store_true", help="escribe (sin esto es vista previa)")
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        q = select(Tenant)
        if args.tenant:
            q = q.where(Tenant.slug == args.tenant)
        tenants = db.scalars(q).all()
        if not tenants:
            sys.exit("No hay empresas que procesar")
        total = 0
        for t in tenants:
            p = plan(db, t.id)
            rows = db.scalars(select(Media).where(Media.tenant_id == t.id, Media.key.in_(p), Media.visibility == "public")).all() if p else []
            print(f"{t.slug}: {len(rows)} archivo(s) pasarian a privado")
            for m in rows:
                print(f"  {m.id:>6} {m.key} {m.filename} -> {p[m.key]}")
                if args.apply:
                    m.visibility, m.module = "private", p[m.key]
            total += len(rows)
        if args.apply:
            db.commit()
            print(f"Listo: {total} archivo(s) ahora privados")
        else:
            print("Vista previa: nada se escribio (use --apply)")


if __name__ == "__main__":
    main()
