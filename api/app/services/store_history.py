"""Mi Tienda (O4-02): version optimista por seccion e historial seguro con «Restaurar».

Version optimista
- Tres fuentes de ajustes de la tienda, cada una con su numero: "store" (PUT /store), "showcase" (PUT /store/showcase)
  y "checkout" (PUT /store/checkout). Vive en tenant.settings["store_v"] (fuera de settings["store"], que GET /store
  devuelve entero).
- El panel manda la version que leyo; si otra pestana o persona guardo en el medio, 409 con CONFLICT. Sin version
  (pestanas viejas, el worker) se guarda como siempre: el contrato es aditivo.
- La comparacion se hace con la fila de la empresa ya bloqueada (SELECT ... FOR UPDATE de cada PUT, O1-11b).

Historial (GET /store/history, POST /store/history/{id}/restore)
- Una fila de audit_log por guardado (entity "store_history"; en Postgres no admite UPDATE ni DELETE) con quien,
  cuando y que campos cambiaron, y el valor ANTERIOR solo de los campos restaurables (lista blanca RESTORABLE).
- Nunca se guarda ni se restaura: publicacion, dominio, tipo de tienda, moneda, la bandera del panel, metodos de pago
  (cuentas SINPE o IBAN), datos del comercio, WhatsApp ni kits. De los textos legales y las paginas de politicas solo
  queda su huella (largo y resumen sha256), nunca el texto.
- Un valor restaurable que trae un numero de cuenta o de 8 cifras o mas (mask_accounts lo cambiaria) se guarda
  enmascarado y NO se ofrece para restaurar.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditLog, Tenant, User
from .documents import audit

VERSION_KEY = "store_v"
SECTIONS = ("store", "showcase", "checkout")
CONFLICT = "Alguien más guardó cambios en esta sección. Recargue para verlos."
ENTITY = "store_history"
LIMIT = 20

RESTORABLE: dict[str, tuple[str, ...]] = {
    "store": ("name", "tagline", "logo_url", "favicon_url", "primary", "secondary", "font", "theme", "shipping_rates"),
    "showcase": (
        "headline",
        "subline",
        "seo_title",
        "seo_description",
        "share_image",
        "banners",
        "featured",
        "trust",
        "faq",
        "sections",
        "categories",
        "collections",
    ),
    "checkout": (
        "free_shipping_from",
        "shipping_cabys",
        "auto_invoice",
        "pickup",
        "notify_roles",
        "verify_email",
        "delivery_notice",
        "seller_choice",
        "carriers",
    ),
}
# solo la huella (textos legales y politicas): se sabe que cambio, nunca se guarda el texto
FINGERPRINT: dict[str, tuple[str, ...]] = {"store": ("legal",), "showcase": ("pages",), "checkout": ()}
NEVER = frozenset({"published", "domain", "kind", "currency", "panel_v2", "methods", "business", "whatsapp", "kits", "checkout", "showcase"})

LABELS = {
    "name": "Nombre",
    "tagline": "Frase",
    "logo_url": "Logo",
    "favicon_url": "Ícono",
    "primary": "Color principal",
    "secondary": "Color secundario",
    "font": "Tipografía",
    "theme": "Apariencia",
    "shipping_rates": "Tarifas de envío",
    "legal": "Textos legales",
    "whatsapp": "WhatsApp",
    "business": "Datos del comercio",
    "published": "Publicación",
    "domain": "Dominio",
    "kind": "Tipo de tienda",
    "currency": "Moneda",
    "panel_v2": "Panel",
    "headline": "Titular",
    "subline": "Bajada",
    "seo_title": "Título para Google",
    "seo_description": "Descripción para Google",
    "share_image": "Imagen para compartir",
    "banners": "Banners",
    "featured": "Destacados",
    "trust": "Sellos",
    "faq": "Preguntas frecuentes",
    "pages": "Páginas",
    "kits": "Kits",
    "sections": "Secciones de la portada",
    "categories": "Categorías",
    "collections": "Colecciones",
    "methods": "Formas de pago",
    "free_shipping_from": "Envío gratis",
    "shipping_cabys": "CABYS del envío",
    "auto_invoice": "Factura automática",
    "pickup": "Retiro en sitio",
    "notify_roles": "Avisos de pedidos",
    "verify_email": "Revisión de correo",
    "delivery_notice": "Aviso de envíos",
    "seller_choice": "Vendedor en la compra",
    "carriers": "Transportistas",
}
SECTION_LABELS = {"store": "Mi Tienda", "showcase": "Vitrina", "checkout": "Pagos y entrega"}


# ---------------------------------------------------------------- version
def version_of(t: Tenant, section: str) -> int:
    return int(((t.settings or {}).get(VERSION_KEY) or {}).get(section) or 0)


def check_version(t: Tenant, section: str, sent: int | None) -> None:
    """409 si el panel leyo otra version. Llamar con la fila de la empresa ya bloqueada."""
    if sent is not None and int(sent) != version_of(t, section):
        raise HTTPException(409, CONFLICT)


def bump_version(st: dict, section: str) -> int:
    """Sube la version de la seccion en la copia de ajustes que se va a guardar (st = dict(t.settings))."""
    vs = dict(st.get(VERSION_KEY) or {})
    vs[section] = int(vs.get(section) or 0) + 1
    st[VERSION_KEY] = vs
    return vs[section]


# ---------------------------------------------------------------- historial
def _fingerprint(v) -> dict:
    raw = json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
    largo = sum(len(str(x or "")) for x in v.values()) if isinstance(v, dict) else len(str(v or ""))
    return {"huella": hashlib.sha256(raw.encode()).hexdigest()[:12], "caracteres": largo}


def record(db: Session, t: Tenant, user_id: int | None, ip: str | None, section: str, before: dict, after: dict, *, restored_from: int | None = None) -> None:
    """Anota un guardado de la seccion: los campos que cambiaron y el valor anterior de los restaurables."""
    from ..routers.store import mask_accounts

    changed = sorted(k for k in set(before) | set(after) if k != "version" and before.get(k) != after.get(k))
    if not changed:
        return
    values: dict = {}
    restorable: list[str] = []
    huellas: dict = {}
    for k in changed:
        if k in NEVER:
            continue
        if k in RESTORABLE.get(section, ()):
            v = before.get(k)
            masked = mask_accounts(v)
            if masked == v:
                values[k] = v
                restorable.append(k)
            else:
                values[k] = masked  # se ve que cambio, pero no se ofrece para restaurar
        elif k in FINGERPRINT.get(section, ()):
            huellas[k] = {"antes": _fingerprint(before.get(k) or {}), "despues": _fingerprint(after.get(k) or {})}
    diff = {"seccion": section, "campos": changed, "restaurables": restorable}
    if restored_from:
        diff["restaurado_de"] = restored_from
    audit(db, t.id, user_id, "history", ENTITY, t.id, diff, ip=ip, before=values or None, after=huellas or None)


def history(db: Session, t: Tenant, limit: int = LIMIT) -> list[dict]:
    rows = db.execute(
        select(AuditLog, User.full_name, User.email)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.tenant_id == t.id, AuditLog.entity == ENTITY)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    out = []
    for log, nombre, correo in rows:
        d = log.diff or {}
        sec = d.get("seccion")
        out.append(
            {
                "id": log.id,
                "at": log.at if log.at.tzinfo else log.at.replace(tzinfo=UTC),
                "quien": nombre or correo or "Sistema",
                "seccion": sec,
                "seccion_label": SECTION_LABELS.get(sec, sec),
                "campos": [LABELS.get(k, k) for k in d.get("campos") or []],
                "restaurables": [LABELS.get(k, k) for k in d.get("restaurables") or []],
                "restaurado_de": d.get("restaurado_de"),
            }
        )
    return out


def restore(db: Session, p, hid: int) -> dict:
    """Vuelve a poner los valores anteriores (solo los restaurables) de un guardado del historial."""
    from ..routers.store import STORE_DEFAULTS
    from .store_showcase import bump_catalog_version
    from .store_snapshot import mark_dirty_after_commit

    log = db.get(AuditLog, hid)
    if not log or log.tenant_id != p.tenant.id or log.entity != ENTITY:
        raise HTTPException(404, "Cambio no encontrado")
    d = log.diff or {}
    section = d.get("seccion")
    keys = [k for k in d.get("restaurables") or [] if k in RESTORABLE.get(section, ()) and k not in NEVER]
    vals = {k: (log.before or {}).get(k) for k in keys if k in (log.before or {})}
    if section not in SECTIONS or not vals:
        raise HTTPException(422, "Ese cambio no tiene nada que se pueda restaurar")
    t = db.get(Tenant, p.tenant.id, with_for_update=True, populate_existing=True)
    st = {**(t.settings or {})}
    store = {**STORE_DEFAULTS, **(st.get("store") or {})}
    if section == "store":
        target = store
    else:
        target = dict(store.get(section) or {})
    before = {k: target.get(k) for k in vals}
    target.update(vals)
    if section != "store":
        store[section] = target
    st["store"] = store
    v = bump_version(st, section)
    t.settings = st
    record(db, t, p.user.id, p.ip, section, before, {**before, **vals}, restored_from=hid)
    audit(db, t.id, p.user.id, "restore", "store", t.id, {"historial": hid, "seccion": section, "campos": sorted(vals)}, ip=p.ip)
    bump_catalog_version(db, t)  # la vitrina (304 por version) y el snapshot muestran lo restaurado en la siguiente visita
    mark_dirty_after_commit(db, t.id)
    db.commit()
    return {"seccion": section, "campos": [LABELS.get(k, k) for k in sorted(vals)], "version": v, "at": datetime.now(UTC)}
