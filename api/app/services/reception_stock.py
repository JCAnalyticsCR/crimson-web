"""Entrada al inventario desde una factura de proveedor recibida por XML (Contabilidad -> Recepcion).

Al aceptar la factura de compra, quien maneja inventario puede "dar entrada al inventario": cada linea del XML
(LineaDetalle) se liga a un producto propio y entra a una bodega de VENTA con su cantidad. Asi lo comprado a un
proveedor aparece en todas las superficies (tienda, panel, POS, aliado) como existencia propia, con la funcion
unica de services/availability.

- Solo comprobantes aceptados (total o parcial) de tipo factura o tiquete; nunca notas de credito o debito.
- Una sola vez por comprobante: las entradas llevan la referencia REC-<id> y una segunda vez responde 409.
- El producto sugerido sale del catalogo del proveedor (item con el codigo de la linea, del mismo emisor) o del
  codigo propio / del proveedor del producto. Siempre se puede cambiar o dejar la linea sin entrada.
- El costo unitario de la entrada queda en colones (con el tipo de cambio del comprobante).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Product, ReceivedDocument, StockMovement, Supplier, SupplierItem, Warehouse
from ..providers.hacienda import xml44
from . import inventory as invsvc
from .documents import audit
from .fx import doc_rate

DOC_TYPES = ("FE", "TE", "FEC")


def reference(doc: ReceivedDocument) -> str:
    return f"REC-{doc.id}"


def accepted(doc: ReceivedDocument) -> bool:
    return doc.status in ("respondido", "historico") and doc.action in ("aceptada", "parcial") and (doc.doc_type or "FE") in DOC_TYPES


def entered(db: Session, doc: ReceivedDocument) -> int:
    return int(
        db.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.tenant_id == doc.tenant_id, StockMovement.reference == reference(doc)))
        or 0
    )


def parse_lines(doc: ReceivedDocument) -> list[dict]:
    """Lineas del XML del comprobante (v4.3 y v4.4): numero, codigo, CABYS, detalle, cantidad, unidad, precio."""
    if not doc.xml_document:
        return []
    try:
        root = xml44.parse_xml_bytes(doc.xml_document.encode("utf-8"))
    except xml44.XmlRejected:
        return []
    out = []
    for i, ld in enumerate(xml44.descendants(root, "LineaDetalle"), start=1):
        code = None
        for cc in xml44.kids(ld, "CodigoComercial"):  # codigo del proveedor (v4.3 y v4.4)
            code = xml44.text(cc, "Codigo")
            if code:
                break
        codigo = xml44.child(ld, "Codigo")
        if code is None and codigo is not None and xml44.kids(codigo, "Codigo"):  # v4.2: <Codigo><Tipo/><Codigo/></Codigo>
            code = xml44.text(codigo, "Codigo")
        plain = xml44.text(codigo) if codigo is not None and not xml44.kids(codigo, "Codigo") else None  # v4.3: el CABYS
        out.append(
            {
                "line": int(xml44.dec(xml44.text(ld, "NumeroLinea"), Decimal(i))),
                "code": code,
                "cabys": xml44.text(ld, "CodigoCABYS") or (plain if plain and plain.isdigit() and len(plain) == 13 else None),
                "detail": xml44.text(ld, "Detalle") or "",
                "quantity": xml44.dec(xml44.text(ld, "Cantidad")),
                "unit": xml44.text(ld, "UnidadMedida"),
                "unit_price": xml44.dec(xml44.text(ld, "PrecioUnitario")),
            }
        )
    return out


def _issuer(db: Session, doc: ReceivedDocument) -> Supplier | None:
    idn = xml44.digits(doc.issuer_id)
    if not idn:
        return None
    return next((s for s in db.scalars(select(Supplier).where(Supplier.tenant_id == doc.tenant_id)) if xml44.digits(s.tax_id) == idn), None)


def suggestions(db: Session, doc: ReceivedDocument) -> list[dict]:
    """Lineas con el producto sugerido (o ninguno) para ligarlas."""
    lines = parse_lines(doc)
    sup = _issuer(db, doc)
    codes = {ln["code"].strip().upper() for ln in lines if ln.get("code")}
    by_item: dict[str, int] = {}
    if sup is not None and codes:
        for it in db.scalars(select(SupplierItem).where(SupplierItem.tenant_id == doc.tenant_id, SupplierItem.supplier_id == sup.id, SupplierItem.product_id.is_not(None))):
            if it.code.strip().upper() in codes:
                by_item[it.code.strip().upper()] = it.product_id
    by_code: dict[str, Product] = {}
    if codes:
        for p in db.scalars(
            select(Product).where(
                Product.tenant_id == doc.tenant_id,
                Product.active,
                Product.item_type == "producto",
                or_(func.upper(Product.code).in_(codes), func.upper(Product.supplier_sku).in_(codes)),
            )
        ):
            for k in (p.code, p.supplier_sku):
                if k and k.strip().upper() in codes:
                    by_code.setdefault(k.strip().upper(), p)
    ids = set(by_item.values())
    prods = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
    out = []
    for ln in lines:
        key = (ln.get("code") or "").strip().upper()
        p = prods.get(by_item.get(key)) if key in by_item else by_code.get(key)
        how = "catalogo_proveedor" if key in by_item else ("codigo" if p else None)
        out.append({**ln, "product_id": p.id if p else None, "product_name": p.name if p else None, "product_code": p.code if p else None, "match": how})
    return out


def stock_in(db: Session, doc: ReceivedDocument, items: list[dict], warehouse_id: int, user_id: int | None, ip: str | None = None) -> int:
    """Da entrada al inventario a las lineas elegidas ({line, product_id, quantity}). Sin commit."""
    if not accepted(doc):
        raise HTTPException(409, "Solo se da entrada al inventario de una factura de proveedor aceptada")
    if entered(db, doc):
        raise HTTPException(409, "Este comprobante ya dio entrada al inventario")
    w = db.get(Warehouse, warehouse_id)
    if not w or w.tenant_id != doc.tenant_id or not w.active:
        raise HTTPException(404, "Bodega no encontrada")
    if (w.kind or "venta") != invsvc.SELLABLE:
        raise HTTPException(422, "La compra entra a una bodega de venta (las de garantías guardan equipos de clientes)")
    lines = {ln["line"]: ln for ln in parse_lines(doc)}
    if not lines:
        raise HTTPException(422, "El comprobante no tiene líneas legibles en su XML")
    # costo en colones con la tasa del XML (o la del BCCR de su fecha si no la trae); sin ninguna: FxMissing -> 422
    rate = doc_rate(db, doc.currency, doc.fx_rate, doc.issue_date)
    ref = reference(doc)
    n = 0
    for it in items:
        ln = lines.get(int(it.get("line") or 0))
        if ln is None:
            raise HTTPException(422, f"La línea {it.get('line')} no existe en el comprobante")
        pid = it.get("product_id")
        if not pid:
            continue
        p = db.get(Product, int(pid))
        if not p or p.tenant_id != doc.tenant_id:
            raise HTTPException(404, "Producto no encontrado")
        if p.item_type != "producto":
            raise HTTPException(422, f"{p.name} es un servicio: no lleva inventario")
        qty = Decimal(str(it.get("quantity") if it.get("quantity") is not None else ln["quantity"]))
        if qty <= 0:
            continue
        if qty > ln["quantity"]:
            raise HTTPException(422, f"Línea {ln['line']}: no puede entrar más de lo facturado ({ln['quantity'].normalize():f})")
        invsvc.move(
            db,
            doc.tenant_id,
            user_id,
            p.id,
            w.id,
            "entrada",
            qty,
            ref,
            f"Compra {doc.issuer_name or ''} {doc.consecutive or doc.clave[-20:]} · línea {ln['line']}: {ln['detail'][:120]}".strip(),
            (ln["unit_price"] * rate).quantize(Decimal("0.00001")),
            movement_type="compra",
        )
        n += 1
    if not n:
        raise HTTPException(422, "Elija al menos una línea con su producto")
    audit(db, doc.tenant_id, user_id, "stock_in", "received_document", doc.id, {"movimientos": n, "bodega": w.id}, ip=ip)
    return n
