"""Historial de precios y fecha de cambio publico (O0-07). Solo registra: no cambia ningun precio.

Dos ganchos de sesion, para que TODO camino que cambie un precio quede registrado sin repetir codigo en cada router
(ficha, lista, ajuste en bloque, importacion, Articulos por completar, inventario web, guardian de margen,
sincronizacion, y tambien los routers vedados de la vitrina mientras kits no entra a main):

- before_flush: anota los productos nuevos o con price, web_price o impuestos tocados (candidatos al historial) y
  pone web_updated_at cuando cambia algo que el cliente ve (VISIBLE). Costo, existencia, fecha del proveedor, margen
  y demas columnas internas no lo tocan.
- before_commit (solo el commit real, no el de un SAVEPOINT): en bloque, por cada candidato compara precio, precio web, moneda e IVA con la ultima fila del historial y
  escribe una fila solo si algo cambio (un guardado con el mismo precio no deja fila). Se evalua al confirmar y no en
  cada flush porque la ficha vacia y vuelve a cargar los impuestos con un flush en medio.

Origen y usuario: session.info["price_source"] (manual por defecto; set_source(db, "sync"|"lote")) y
session.info["actor_user_id"] (lo pone core/deps al autenticar). Las actualizaciones en bloque por SQL (update()
sin ORM) no pasan por aqui: hoy ninguna toca precios.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import event, func, inspect, select
from sqlalchemy.orm import Session

from ..models.catalog import Product, ProductPriceHistory, ProductTax, Tax

SOURCES = ("manual", "sync", "lote", "inicial")
# lo que el cliente ve en la tienda: si cambia, cambia web_updated_at (sitemap lastmod real en la Ola 1)
VISIBLE = (
    "name",
    "price",
    "web_price",
    "compare_price",
    "currency",
    "images",
    "description_store",
    "description_invoice",
    "show_on_web",
    "active",
    # O3-01: el contenido propio de la tienda (nombre comercial, tipo, especificaciones y descripcion aprobados)
    "web_name",
    "web_subtitle",
    "web_type",
    "web_specs",
    "web_description",
    # retener por margen pone la tienda en «Consultar» (Pricer.of devuelve 0) y liberarlo la saca: el cliente lo ve
    "margin_hold",
)
PRICE_ATTRS = ("price", "web_price", "currency")
_PENDING = "_price_history_pending"
DEFAULT_RATE = Decimal(13)


def set_source(db: Session, source: str) -> None:
    """Origen de los cambios de precio de esta sesion (queda hasta que otro llamado lo cambie)."""
    if source not in SOURCES:
        raise ValueError(f"origen de precio desconocido: {source}")
    db.info["price_source"] = source


def set_actor(db: Session, user_id: int | None) -> None:
    """Quien hace los cambios de esta sesion (una peticion autenticada); el origen vuelve a «manual»."""
    db.info["actor_user_id"] = user_id
    db.info["price_source"] = "manual"


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return Decimal(str(a)) == Decimal(str(b))
    return a == b


def _changed(state, attr: str) -> bool:
    """True si el atributo cambio de verdad (el mismo valor reasignado no cuenta). Sin valor anterior cargado se
    asume que cambio (mejor una fecha de mas que perder un cambio)."""
    h = state.attrs[attr].history
    if not h.has_changes():
        return False
    if not h.deleted:
        return True
    if attr == "images" or not h.added:
        return True
    return not _same(h.deleted[0], h.added[0])


def tax_rate(db: Session, p: Product) -> Decimal:
    """IVA del producto: el impuesto de menor id (como la fila «inicial» de la migracion y la relacion ordenada de
    Product.taxes; los recien agregados, sin id, al final); 13 si no tiene."""
    with db.no_autoflush:
        for pt in sorted(p.taxes, key=lambda pt: (pt.id is None, pt.id or 0)):
            tax = pt.tax if "tax" in inspect(pt).dict else None
            if tax is None and pt.tax_id is not None:
                tax = db.get(Tax, pt.tax_id)
            if tax is not None and tax.rate is not None:
                return Decimal(str(tax.rate))
    return DEFAULT_RATE


def snapshot(db: Session, p: Product) -> dict:
    return _snap(p.price, p.web_price, p.currency, tax_rate(db, p))


def last(db: Session, product_id: int) -> ProductPriceHistory | None:
    with db.no_autoflush:
        return db.scalar(
            select(ProductPriceHistory)
            .where(ProductPriceHistory.product_id == product_id)
            .order_by(ProductPriceHistory.valid_from.desc(), ProductPriceHistory.id.desc())
            .limit(1)
        )


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _ctx, _instances) -> None:
    pending: set = session.info.setdefault(_PENDING, set())
    now = datetime.now(UTC)
    for obj in list(session.new):
        if isinstance(obj, Product):
            pending.add(obj)
            if obj.web_updated_at is None:
                obj.web_updated_at = now
        elif isinstance(obj, ProductTax):
            _pending_from_tax(session, obj, pending)
    for obj in list(session.dirty):
        if isinstance(obj, Product):
            state = inspect(obj)
            if any(_changed(state, a) for a in PRICE_ATTRS) or state.attrs["taxes"].history.has_changes():
                pending.add(obj)
            if any(_changed(state, a) for a in VISIBLE):
                obj.web_updated_at = now
    for obj in list(session.deleted):
        if isinstance(obj, ProductTax):
            _pending_from_tax(session, obj, pending)


def _pending_from_tax(session: Session, pt: ProductTax, pending: set) -> None:
    if pt.product_id is None:
        return
    with session.no_autoflush:
        prod = session.get(Product, pt.product_id)
    if prod is not None:
        pending.add(prod)


CHUNK = 1000  # ids por consulta IN (Postgres y SQLite aceptan mas, pero asi la consulta queda acotada)


def _rates(db: Session, ids: list[int]) -> dict[int, Decimal]:
    """IVA de muchos productos en una consulta: el impuesto de menor id, igual que tax_rate() y la migracion."""
    out: dict[int, Decimal] = {}
    rows = db.execute(
        select(ProductTax.product_id, Tax.rate)
        .join(Tax, Tax.id == ProductTax.tax_id)
        .where(ProductTax.product_id.in_(ids), Tax.rate.is_not(None))
        .order_by(ProductTax.product_id, ProductTax.id)
    )
    for pid, rate in rows:
        out.setdefault(pid, Decimal(str(rate)))
    return out


def _lasts(db: Session, ids: list[int]) -> dict[int, ProductPriceHistory]:
    """Ultima fila del historial de muchos productos en una consulta (row_number sirve en Postgres y SQLite)."""
    rn = (
        func.row_number()
        .over(
            partition_by=ProductPriceHistory.product_id,
            order_by=(ProductPriceHistory.valid_from.desc(), ProductPriceHistory.id.desc()),
        )
        .label("rn")
    )
    sub = select(ProductPriceHistory.id, rn).where(ProductPriceHistory.product_id.in_(ids)).subquery()
    rows = db.scalars(select(ProductPriceHistory).join(sub, sub.c.id == ProductPriceHistory.id).where(sub.c.rn == 1))
    return {r.product_id: r for r in rows}


def _snap(price, web_price, currency, rate: Decimal) -> dict:
    net = Decimal(str(price or 0))
    web = Decimal(str(web_price)) if web_price is not None else None
    final = ((web if web is not None else net) * (1 + rate / 100)).quantize(Decimal("0.01"))
    return {"price_net": net, "web_price_net": web, "currency": (currency or "CRC").upper(), "tax_rate": rate, "price_final": final}


@event.listens_for(Session, "before_commit")
def _before_commit(session: Session) -> None:
    # before_commit tambien se dispara al confirmar un SAVEPOINT: el historial se escribe solo en el commit real.
    if session.in_nested_transaction():
        return
    if not session.info.get(_PENDING) and not any(isinstance(o, (Product, ProductTax)) for o in (*session.new, *session.dirty, *session.deleted)):
        return
    session.flush()  # ids de los productos nuevos y ultimo pase de before_flush
    pending: set = session.info.pop(_PENDING, set())
    ids = sorted({p.id for p in pending if p.id is not None and not _gone(p)})
    if not ids:
        return
    source = session.info.get("price_source") or "manual"
    user_id = session.info.get("actor_user_id")
    now = datetime.now(UTC)
    # Todo en bloque (unas pocas consultas por cada CHUNK productos, no tres por producto): un ajuste masivo de precios
    # no debe alargar el commit mientras tiene tomadas las filas. Despues del flush la base ya tiene lo de la sesion.
    with session.no_autoflush:
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i : i + CHUNK]
            rows = session.execute(select(Product.id, Product.tenant_id, Product.price, Product.web_price, Product.currency).where(Product.id.in_(chunk))).all()
            rates = _rates(session, chunk)
            lasts = _lasts(session, chunk)
            for pid, tenant_id, price, web_price, currency in rows:
                snap = _snap(price, web_price, currency, rates.get(pid, DEFAULT_RATE))
                prev = lasts.get(pid)
                if prev is not None and all(_same(getattr(prev, k), v) for k, v in snap.items() if k != "price_final"):
                    continue
                session.add(ProductPriceHistory(tenant_id=tenant_id, product_id=pid, source=source, changed_by=user_id, valid_from=now, **snap))


def _gone(p: Product) -> bool:
    st = inspect(p)
    return st.deleted or st.detached or st.transient or st.was_deleted


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction) -> None:
    """Los pendientes viven lo que la transaccion de verdad. Revertir un SAVEPOINT NO los borra: lo que la transaccion
    externa ya cambio y vacio no vuelve a salir en session.dirty, y se perderia su fila. Lo creado dentro del savepoint
    queda transitorio y before_commit lo salta; lo revertido vuelve a su valor y la comparacion no deja fila."""
    if transaction.parent is None:
        session.info.pop(_PENDING, None)
