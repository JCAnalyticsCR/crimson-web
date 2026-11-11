"""Mi Tienda -> Catalogo -> edicion en lote (O4-05).

POST /store/catalog/bulk con confirm=false devuelve el antes y el despues de cada fila y los que se saltan con su
motivo; con confirm=true aplica EXACTAMENTE eso (la misma funcion arma la vista previa y el cambio), pasa por el
guardian de margen, deja UNA entrada de bitacora (entity "store_bulk", con el antes de cada producto) y marca el
catalogo una vez. POST /store/catalog/bulk/{op}/undo lo deshace: vuelve cada producto a como estaba, salvo los que
alguien cambio despues (se informan y no se tocan). La bitacora no admite UPDATE en Postgres: el deshacer queda como
otra fila (action "bulk_undo") y una operacion no se deshace dos veces.

Precios (restriccion del dueno: los precios de Crimson se conservan): la herramienta existe, siempre con vista previa
y confirmacion explicita; nunca crea reglas automaticas de precio ni ofertas con fecha. El precio se escribe FINAL con
IVA y se guarda neto en Product.web_price con 5 decimales (models/catalog.py): 12 500 con IVA 13 % -> 11 061.94690
-> de vuelta 12 500.00, igual que /quote y la factura (services/totals). Un precio que dejaria el producto bajo el
margen minimo se salta con su motivo (el lote respeta al guardian). Productos en otra moneda que la tienda, se saltan.
No existe el precio de comparacion manual ni la «Oferta» en lote: una oferta solo se muestra con historial propio.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import AuditLog, Category, Product, ProductTax
from .documents import audit

ACCIONES = {
    "publicar": "Publicar en la tienda",
    "ocultar": "Ocultar de la tienda",
    "categoria": "Cambiar la categoría",
    "marca": "Cambiar la marca",
    "fijar": "Fijar arriba",
    "soltar": "Quitar de arriba",
    "precio_web": "Precio web",
}
# campo de Product que toca cada accion y el permiso que pide (ademas de catalog.editar)
CAMPO = {
    "publicar": "show_on_web",
    "ocultar": "show_on_web",
    "categoria": "category_id",
    "marca": "brand",
    "fijar": "web_featured",
    "soltar": "web_featured",
    "precio_web": "web_price",
}
PERMISO = {"publicar": ("catalog", "publicar"), "ocultar": ("catalog", "publicar"), "precio_web": ("catalog", "precios")}
MAX_FILAS = 1000  # filas de la vista previa que viajan al panel (el conteo es el total)
MAX_PRODUCTOS = 5000
ENTITY = "store_bulk"
Q5 = Decimal("0.00001")
Q2 = Decimal("0.01")


def net_from_gross(gross: Decimal, rate: Decimal) -> Decimal:
    """Precio final con IVA -> neto con 5 decimales (como se guarda Product.price/web_price)."""
    return (Decimal(gross) / (1 + Decimal(rate) / 100)).quantize(Q5, rounding=ROUND_HALF_UP)


def gross_from_net(net: Decimal, rate: Decimal) -> Decimal:
    """Neto -> final con IVA a 2 decimales: la misma cuenta de la tienda (routers/store._gross) y del comprobante."""
    return (Decimal(net) * (1 + Decimal(rate) / 100)).quantize(Q2, rounding=ROUND_HALF_UP)


def _rate(p: Product) -> Decimal:
    return Decimal(str(p.taxes[0].tax.rate)) if p.taxes else Decimal(13)


def _money(v, cur: str) -> str:
    if v is None:
        return "—"
    s = f"{Decimal(v):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    s = s[:-3] if s.endswith(",00") else s
    return f"{'₡' if cur == 'CRC' else '$' if cur == 'USD' else cur + ' '}{s}"


def _products(db: Session, p, ids: list[int] | None, filtro: dict | None) -> list[Product]:
    from . import store_catalog as scat

    if ids is not None:
        rows = list(
            db.scalars(
                select(Product)
                .where(Product.tenant_id == p.tenant.id, Product.active, Product.id.in_(set(ids)))
                .options(selectinload(Product.taxes).selectinload(ProductTax.tax))
                .order_by(Product.name)
            )
        )
    else:
        f = filtro or {}
        lst = scat.listing(
            db, p, q=f.get("q"), category_id=int(f["categoria"]) if f.get("categoria") else None, brand=f.get("marca") or None,
            estado=f.get("estado") or None, publicado=f.get("publicado") or "si", falta=f.get("falta") or None, page=1, per_page=MAX_PRODUCTOS,
        )  # fmt: skip
        want = [x["id"] for x in lst["items"]]
        rows = (
            list(
                db.scalars(select(Product).where(Product.id.in_(want)).options(selectinload(Product.taxes).selectinload(ProductTax.tax)).order_by(Product.name))
            )
            if want
            else []
        )
    if len(rows) > MAX_PRODUCTOS:
        raise HTTPException(422, f"Son demasiados productos para una sola operación (máximo {MAX_PRODUCTOS}).")
    return rows


def plan(db: Session, p, accion: str, valor, ids: list[int] | None, filtro: dict | None) -> tuple[list[dict], list[dict], list[Product]]:
    """(filas, saltados, productos a cambiar): lo que se mostraria Y lo que se aplicaria."""
    from . import margin_guard as mg
    from .store_inventory import web_price

    if accion not in ACCIONES:
        raise HTTPException(422, "Acción desconocida")
    if accion in PERMISO and not p.can(*PERMISO[accion]):
        raise HTTPException(
            403, "Solo el administrador publica u oculta productos en la tienda" if accion in ("publicar", "ocultar") else "Sin permiso: catalog.precios"
        )
    prods = _products(db, p, ids, filtro)
    store_cur = None
    nuevo = None
    cat_name: dict[int, str] = {}
    if accion == "categoria":
        cat = db.get(Category, int(valor or 0))
        if not cat or cat.tenant_id != p.tenant.id:
            raise HTTPException(404, "Categoría no encontrada")
        nuevo = cat.id
        cat_name = dict(db.execute(select(Category.id, Category.name).where(Category.tenant_id == p.tenant.id)).all())
    elif accion == "marca":
        nuevo = (str(valor or "").strip() or None) if valor is not None else None
        if nuevo and len(nuevo) > 60:
            raise HTTPException(422, "La marca tiene más de 60 caracteres")
    elif accion == "precio_web":
        from ..routers.store import store_cfg

        store_cur = (store_cfg(p.tenant).get("currency") or "CRC").upper()
        if valor is not None:
            try:
                nuevo = Decimal(str(valor))
            except ArithmeticError as e:
                raise HTTPException(422, "Precio inválido") from e
            if nuevo <= 0 or nuevo > Decimal("999999999"):
                raise HTTPException(422, "Escriba un precio mayor que cero")
    else:
        nuevo = accion in ("publicar", "fijar")

    cfg = mg.config(p.tenant, db) if accion == "precio_web" and nuevo is not None else None
    rates = mg.Rates(db) if cfg else None
    filas, saltados, cambiar = [], [], []
    for x in prods:
        campo = CAMPO[accion]
        antes_v = getattr(x, campo)
        nota = None
        if accion == "precio_web":
            cur = (x.currency or "CRC").upper()
            if cur != store_cur:
                saltados.append({"id": x.id, "name": x.name, "motivo": f"Su precio está en {cur}: cámbielo en su ficha"})
                continue
            rate = _rate(x)
            neto = None if nuevo is None else net_from_gross(nuevo, rate)
            if (None if antes_v is None else Decimal(str(antes_v))) == neto:
                continue
            if neto is not None and cfg and x.cost:
                rp, rc = rates.get(cur), rates.get((x.cost_currency or "USD").upper())
                if rp is not None and rc is not None and Decimal(str(x.cost)) > 0:
                    margen = mg.margin_of(neto * rp.tasa, Decimal(str(x.cost)) * rc.tasa)
                    if margen < cfg["min_pct"]:
                        saltados.append({"id": x.id, "name": x.name, "motivo": f"Quedaría bajo el margen mínimo ({margen:.1f} % de {cfg['min_pct']} %)"})
                        continue
            actual = gross_from_net(Decimal(str(web_price(x) or 0)), rate)
            despues_txt = (
                f"{_money(gross_from_net(neto, rate), cur)} (neto {_money(neto, cur)})"
                if neto is not None
                else f"El de siempre: {_money(gross_from_net(Decimal(str(x.price or 0)), rate), cur)}"
            )
            filas.append(
                {
                    "id": x.id,
                    "code": x.code,
                    "name": x.name,
                    "antes": _money(actual, cur) + (" (precio web)" if x.web_price is not None else ""),
                    "despues": despues_txt,
                    "nota": nota,
                }
            )
            cambiar.append((x, neto))
            continue
        if accion in ("publicar", "ocultar", "fijar", "soltar"):
            if bool(antes_v) == bool(nuevo):
                continue
            txt = {"show_on_web": ("Publicado", "No publicado"), "web_featured": ("Fijado arriba", "Normal")}[campo]
            filas.append(
                {"id": x.id, "code": x.code, "name": x.name, "antes": txt[0] if antes_v else txt[1], "despues": txt[0] if nuevo else txt[1], "nota": None}
            )
        elif accion == "categoria":
            if antes_v == nuevo:
                continue
            filas.append(
                {
                    "id": x.id,
                    "code": x.code,
                    "name": x.name,
                    "antes": cat_name.get(antes_v) or "Sin categoría",
                    "despues": cat_name.get(nuevo, ""),
                    "nota": None,
                }
            )
        elif accion == "marca":
            if (antes_v or None) == nuevo:
                continue
            filas.append({"id": x.id, "code": x.code, "name": x.name, "antes": antes_v or "Sin marca", "despues": nuevo or "Sin marca", "nota": None})
        cambiar.append((x, nuevo))
    return filas, saltados, cambiar


def preview(db: Session, p, accion: str, valor, ids, filtro) -> dict:
    filas, saltados, _ = plan(db, p, accion, valor, ids, filtro)
    return {"titulo": ACCIONES[accion], "filas": filas[:MAX_FILAS], "saltados": saltados, "n": len(filas), "mostrando": min(len(filas), MAX_FILAS)}


def apply(db: Session, p, accion: str, valor, ids, filtro) -> dict:
    from . import margin_guard as mg
    from . import store_showcase as sv
    from .store_snapshot import mark_dirty_after_commit

    filas, saltados, cambiar = plan(db, p, accion, valor, ids, filtro)
    campo = CAMPO[accion]
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    retenidos = 0
    for x, v in cambiar:
        old = getattr(x, campo)
        before[str(x.id)] = str(old) if isinstance(old, Decimal | float) else old
        setattr(x, campo, v)
        after[str(x.id)] = str(v) if isinstance(v, Decimal | float) else v
        if campo in ("show_on_web", "web_price"):
            try:
                mg.recheck(db, p.tenant, x)
            except Exception:  # noqa: BLE001  guardar nunca falla por el guardian
                pass
            retenidos += int(bool(x.margin_hold and x.show_on_web))
    if not cambiar:
        return {"op": None, "n": 0, "saltados": len(saltados), "retenidos": 0}
    sv.bump_catalog_version(db, p.tenant)
    mark_dirty_after_commit(db, p.tenant.id)
    # una sola entrada de bitacora con el antes de cada producto (la fila se arma aqui para devolver su id: es la
    # operacion que «Deshacer» revierte)
    log = AuditLog(
        tenant_id=p.tenant.id, user_id=p.user.id, at=datetime.now(UTC), ip=p.ip, action="bulk", entity=ENTITY, entity_id=None,
        diff={"accion": accion, "campo": campo, "n": len(cambiar), "saltados": len(saltados)}, before=before, after=after,
    )  # fmt: skip
    db.add(log)
    db.flush()
    op = log.id
    db.commit()
    return {"op": op, "n": len(cambiar), "saltados": len(saltados), "retenidos": retenidos}


def undo(db: Session, p, op: int) -> dict:
    from . import margin_guard as mg
    from . import store_showcase as sv
    from .store_snapshot import mark_dirty_after_commit

    log = db.get(AuditLog, op)
    if not log or log.tenant_id != p.tenant.id or log.entity != ENTITY or log.action != "bulk":
        raise HTTPException(404, "Operación no encontrada")
    if db.scalar(
        select(AuditLog.id).where(AuditLog.tenant_id == p.tenant.id, AuditLog.entity == ENTITY, AuditLog.action == "bulk_undo", AuditLog.entity_id == op)
    ):
        raise HTTPException(409, "Esa operación ya se deshizo")
    campo = (log.diff or {}).get("campo")
    accion = (log.diff or {}).get("accion")
    if accion in PERMISO and not p.can(*PERMISO[accion]):
        raise HTTPException(403, "Sin permiso para deshacer esta operación")
    before, after = log.before or {}, log.after or {}
    prods = {x.id: x for x in db.scalars(select(Product).where(Product.tenant_id == p.tenant.id, Product.id.in_([int(k) for k in before])))}
    n, saltados = 0, 0

    def norm(v):
        if campo == "web_price":
            return None if v is None else Decimal(str(v))
        return v

    for k, old in before.items():
        x = prods.get(int(k))
        if x is None or norm(getattr(x, campo)) != norm(after.get(k)):
            saltados += 1  # alguien lo cambio despues: no se pisa
            continue
        setattr(x, campo, norm(old) if campo == "web_price" else old)
        n += 1
        if campo in ("show_on_web", "web_price"):
            try:
                mg.recheck(db, p.tenant, x)
            except Exception:  # noqa: BLE001
                pass
    if n:
        sv.bump_catalog_version(db, p.tenant)
        mark_dirty_after_commit(db, p.tenant.id)
    audit(db, p.tenant.id, p.user.id, "bulk_undo", ENTITY, op, {"accion": accion, "n": n, "saltados": saltados}, ip=p.ip)
    db.commit()
    return {"n": n, "saltados": saltados}
