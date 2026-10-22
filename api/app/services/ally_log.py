"""Bitacora de la oportunidad compartida con el aliado (reunion 2, 21/10, pedido 15).

La bitacora es texto (Opportunity.notes): una linea por entrada, "dd/mm/aaaa hh:mm · autor: texto", la mas nueva
arriba (routers/pipeline._log). Cada entrada tiene su interruptor "Visible para el aliado" (por defecto oculta): lo
que se comparte se guarda como huella de la linea (AllySharedNote.line_key). Asi ningun camino que escribe la
bitacora tiene que cambiar, y una linea editada o borrada deja de verse sola (deny by default).

Al aliado le sale la fecha, el texto y quien lo escribio: su empresa con su nombre; de Crimson, el nombre de pila y
"Crimson" (como las notas de los tickets). Nunca las entradas no compartidas.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AllyCompany, AllyParticipation, AllySharedNote, Opportunity

LINE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}) · ([^:]+): (.*)$")


def line_key(line: str) -> str:
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


def lines_of(notes: str | None) -> list[str]:
    """Las entradas tal como las dibuja el panel (Opportunities.tsx logLines): una por linea, sin vacias."""
    return [x.strip() for x in (notes or "").split("\n") if x.strip()]


def shared_keys(db: Session, o: Opportunity) -> set[str]:
    return set(db.scalars(select(AllySharedNote.line_key).where(AllySharedNote.tenant_id == o.tenant_id, AllySharedNote.opportunity_id == o.id)))


def ally_companies(db: Session, o: Opportunity) -> list[str]:
    """Empresas aliadas que ven la oportunidad en su portal (participacion visible): a quienes llega lo compartido."""
    rows = db.scalars(
        select(AllyCompany.name)
        .join(AllyParticipation, AllyParticipation.ally_company_id == AllyCompany.id)
        .where(AllyParticipation.tenant_id == o.tenant_id, AllyParticipation.opportunity_id == o.id, AllyParticipation.visible_to_ally.is_(True))
    ).all()
    return sorted(set(rows))


def state(db: Session, o: Opportunity) -> dict:
    """Para el panel: que lineas de la bitacora estan compartidas hoy y con quien."""
    keys = shared_keys(db, o)
    companies = ally_companies(db, o)
    return {"can_share": bool(companies), "companies": companies, "shared": [x for x in lines_of(o.notes) if line_key(x) in keys]}


def set_visible(db: Session, o: Opportunity, line: str, visible: bool, user_id: int | None) -> bool:
    """Prende o apaga una entrada. La linea tiene que existir hoy en la bitacora (404 lo decide quien llama).
    Devuelve True si cambio algo."""
    key = line_key(line)
    row = db.scalar(select(AllySharedNote).where(AllySharedNote.tenant_id == o.tenant_id, AllySharedNote.opportunity_id == o.id, AllySharedNote.line_key == key))
    if visible and row is None:
        db.add(AllySharedNote(tenant_id=o.tenant_id, opportunity_id=o.id, line_key=key, created_by=user_id))
        return True
    if not visible and row is not None:
        db.delete(row)
        return True
    return False


def for_ally(db: Session, o: Opportunity, company_name: str) -> list[dict]:
    """Lo que ve el aliado: solo las entradas compartidas, en el orden de la bitacora (la mas nueva arriba)."""
    keys = shared_keys(db, o)
    if not keys:
        return []
    # una linea escrita a nombre de OTRA empresa aliada (p. ej. "envio su precio") sale como de Crimson, sin su nombre
    otras = set(
        db.scalars(
            select(AllyCompany.name)
            .join(AllyParticipation, AllyParticipation.ally_company_id == AllyCompany.id)
            .where(AllyParticipation.tenant_id == o.tenant_id, AllyParticipation.opportunity_id == o.id)
        )
    ) - {company_name}
    out = []
    for x in lines_of(o.notes):
        if line_key(x) not in keys:
            continue
        m = LINE_RE.match(x)
        if not m:
            out.append({"when": None, "author": None, "text": x})
            continue
        quien = m.group(2).strip()
        if quien == company_name:
            autor = company_name
        elif quien in otras:
            autor = "Crimson"
        else:
            pila = quien.split()[0] if quien.split() else ""
            autor = "Crimson" if not pila or pila == "Crimson" else f"{pila} · Crimson"
        out.append({"when": m.group(1), "author": autor, "text": m.group(3)})
    return out
