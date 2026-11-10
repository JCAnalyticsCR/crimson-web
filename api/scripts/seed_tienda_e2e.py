"""Semilla de la TIENDA LOCAL para la compra de punta a punta (plan de la tienda, O0-03).

Deja una empresa con la tienda publicada y lista para comprar, con datos 100 % ficticios:

- tienda tipo "tienda" (carrito y checkout) en el dominio local `tienda.localhost`, en colones;
- las tarifas de envio que el plan documenta como las de la tienda (Correos a todo el pais y mensajeria en la GAM) y
  envio gratis desde 50 000 colones;
- SINPE Movil activo (con numero y titular inventados) y tarjeta en MODO DE PRUEBA: sin llave de ONVO y fuera de
  produccion el pago con tarjeta es simulado (services/store_orders.simulation_allowed);
- 6 productos de 2 000 colones o menos, uno de cada estado de disponibilidad:
    2 en bodega · 2 bajo pedido con dato vigente del proveedor · 1 por confirmar (dato viejo) · 1 agotado.

No crea usuarios ni contrasenas, no cambia precios de productos que no sean los suyos (codigo E2E-...) y no llama a
ningun servicio externo. Es idempotente: se puede correr antes de cada corrida de pruebas; repone la existencia y
refresca la fecha del dato del proveedor (que vence a los 8 dias).

Uso (desde api/, sobre la base local de DATABASE_URL, despues de `alembic upgrade head`):

    uv run python -m scripts.seed_tienda_e2e
    uv run python -m scripts.seed_tienda_e2e --json     # imprime el resumen como JSON

Candados (no se puede correr por error contra una base real):
- solo con ENV=local y sin variables de Railway en el entorno;
- solo contra SQLite o contra una Postgres de esta maquina o del docker-compose (localhost, 127.0.0.1, postgres);
- se niega si la empresa ya tiene en la web productos que no son de la semilla (una base de desarrollo con catalogo
  propio): para eso esta `--forzar`, que deja esos productos como estan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

SLUG = "crimson"
DOMINIO = "tienda.localhost"
PREFIJO = "E2E-"
CATEGORIA = "Accesorios de prueba"
PROVEEDOR = "Proveedor Ficticio E2E"
REFERENCIA = "SEMILLA-E2E"
HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", "postgres", "host.docker.internal"}
DIAS_DATO_VIEJO = 30  # mas que la vigencia del dato del proveedor (8 dias): "Consultar disponibilidad"

TIENDA = {
    "name": "Tienda Ficticia E2E",
    "tagline": "Tienda local de pruebas: nada de lo que se ve aquí es real.",
    "published": True,
    "kind": "tienda",
    "domain": DOMINIO,
    "currency": "CRC",
    "whatsapp": "+506 0000 0000",
    # Las tarifas que el plan de la tienda documenta (O1-09: 3 955 a todo el pais y 5 311 en la GAM, con IVA). Aqui
    # van sin IVA, como se guardan en Mi Tienda. No se leyeron de produccion.
    "shipping_rates": [
        {"name": "Correos de Costa Rica", "amount": 3500, "active": True},
        {"name": "Fast Crimson", "amount": 4700, "active": True, "zone": "gam"},
    ],
    "legal": {
        "terms": "Términos de la tienda de pruebas.\n\nEsta tienda es ficticia: ningún pedido se cobra, se factura ni se entrega.",
        "privacy": "Privacidad de la tienda de pruebas.\n\nLos datos que se escriben aquí son inventados y se borran con la base de pruebas.",
    },
}

CHECKOUT = {
    "methods": {
        "sinpe": {"active": True, "phone": "0000-0000", "holder": "Tienda Ficticia E2E", "note": "Pago de prueba: no envíe dinero."},
        "transferencia": {"active": False},
        # activa y SIN llave de ONVO: fuera de produccion el API la ofrece como "Modo de prueba" (pago simulado)
        "tarjeta": {"active": True, "installments": False, "max_installments": 0},
        "efectivo": {"active": False},
    },
    "free_shipping_from": 50000,
    # nada sale de la maquina: sin comprobante electronico automatico y sin consultar el DNS del correo del comprador
    "auto_invoice": False,
    "verify_email": False,
    "delivery_notice": False,
    "seller_choice": False,
}

# ajustes de inventario de los que dependen los seis estados (los demas quedan como esten)
INVENTARIO = {
    "enabled": True,
    "warehouse_ids": [],
    "quantity_display": "exacta",
    "low_threshold": 3,
    "backorder_enabled": True,
    "backorder_lead": "3-5 días hábiles",
    "supplier_max_age_hours": 192,
    "out_of_stock": "mostrar",
    "out_of_stock_action": "whatsapp",
}

# codigo, nombre, precio sin IVA (con el 13 % ninguno pasa de 2 000), existencia propia, proveedor (cantidad, dias del dato)
PRODUCTOS: list[dict] = [
    {"code": "E2E-BOD-1", "name": "Cable de red de prueba 1 m", "price": 1500, "own": 40, "sup": None, "estado": "en_bodega"},
    {"code": "E2E-BOD-2", "name": "Conector RJ45 de prueba", "price": 500, "own": 3, "sup": None, "estado": "en_bodega"},
    {"code": "E2E-PED-1", "name": "Soporte de pared de prueba", "price": 1200, "own": 0, "sup": (25, 0), "estado": "bajo_pedido"},
    {"code": "E2E-PED-2", "name": "Caja de paso de prueba", "price": 800, "own": 0, "sup": (10, 0), "estado": "bajo_pedido"},
    {"code": "E2E-CON-1", "name": "Fuente 12 V de prueba", "price": 1700, "own": 0, "sup": (5, DIAS_DATO_VIEJO), "estado": "consultar"},
    {"code": "E2E-AGO-1", "name": "Sirena interior de prueba", "price": 1600, "own": 0, "sup": None, "estado": "agotado"},
]


class SemillaRechazada(RuntimeError):
    """La semilla no corre aqui (entorno o base que no son de pruebas locales)."""


def revisar_entorno(database_url: str, env: str, entorno: dict | None = None) -> None:
    """Candados de la semilla: lanza SemillaRechazada si esto no es una base local de pruebas."""
    entorno = os.environ if entorno is None else entorno
    if env != "local":
        raise SemillaRechazada(f"ENV={env}: la semilla de la tienda de pruebas solo corre con ENV=local.")
    if any(k.upper().startswith("RAILWAY_") for k in entorno):
        raise SemillaRechazada("Hay variables de Railway en el entorno: la semilla no corre en un servicio desplegado.")
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        return
    if (url.host or "").lower() not in HOSTS_LOCALES:
        raise SemillaRechazada("DATABASE_URL no apunta a esta máquina ni al docker-compose: la semilla no corre contra una base remota.")


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def sembrar(db: Session, *, forzar: bool = False) -> dict:
    """Deja la tienda de pruebas lista y devuelve el resumen {slug, dominio, productos: {codigo: {id, estado, ...}}}.
    Hace commit. Idempotente."""
    from app.models import Category, Product, ProductTax, StockMovement, Supplier, SupplierItem, Tax
    from app.routers.store import STORE_DEFAULTS
    from app.routers.store_checkout import CheckoutCfgIn
    from app.seeds import seed_base
    from app.services import availability, store_snapshot
    from app.services import inventory as invsvc

    t = seed_base(db, demo=False)  # empresa, monedas, impuestos, bodegas y tipo de cambio; sin catalogo ni usuarios
    ajenos = db.scalars(
        select(Product.code).where(Product.tenant_id == t.id, Product.show_on_web, Product.active, ~Product.code.startswith(PREFIJO)).limit(3)
    ).all()
    if ajenos and not forzar:
        raise SemillaRechazada(
            f"La empresa «{t.slug}» ya tiene productos propios en la web ({', '.join(ajenos)}…). Use una base desechable, o --forzar para sembrar encima sin tocarlos."
        )

    # ---------- tienda, pagos e inventario ----------
    ajustes = dict(t.settings or {})
    tienda = {**STORE_DEFAULTS, **(ajustes.get("store") or {}), **TIENDA}
    # el mismo esquema que valida Mi Tienda -> Pagos y entrega: si el contrato cambia, la semilla falla aqui
    tienda["checkout"] = CheckoutCfgIn.model_validate(CHECKOUT).model_dump(mode="json", exclude={"web_orders_commission"})
    showcase = dict(tienda.get("showcase") or {})
    showcase["ga4_id"] = ""  # la tienda de pruebas nunca mide: sin medicion no aparece el aviso ni se pide nada a terceros
    tienda["showcase"] = showcase
    ajustes["store"] = tienda
    ajustes[availability.KEY] = {**(ajustes.get(availability.KEY) or {}), **INVENTARIO}
    t.settings = ajustes
    db.flush()

    # ---------- catalogo ----------
    cat = db.scalar(select(Category).where(Category.tenant_id == t.id, Category.name == CATEGORIA))
    if cat is None:
        cat = Category(tenant_id=t.id, name=CATEGORIA, show_on_web=True)
        db.add(cat)
        db.flush()
    cat.show_on_web = True
    iva13 = db.scalar(select(Tax).where(Tax.tenant_id == t.id, Tax.rate_code == "08"))
    sup = db.scalar(select(Supplier).where(Supplier.tenant_id == t.id, Supplier.name == PROVEEDOR))
    if sup is None:
        sup = Supplier(tenant_id=t.id, name=PROVEEDOR)
        db.add(sup)
        db.flush()
    bodega = invsvc.default_warehouse(db, t.id)
    ahora = datetime.now(UTC)

    prods: dict[str, Product] = {}
    for spec in PRODUCTOS:
        p = db.scalar(select(Product).where(Product.tenant_id == t.id, Product.code == spec["code"]))
        if p is None:
            p = Product(tenant_id=t.id, code=spec["code"], name=spec["name"])
            db.add(p)
        p.name, p.item_type, p.price, p.currency = spec["name"], "producto", Decimal(spec["price"]), "CRC"
        p.cost, p.cost_currency = Decimal(spec["price"]) / 2, "CRC"
        p.web_price = p.compare_price = None
        p.brand, p.model = "Marca Ficticia", spec["code"].replace(PREFIJO, "MF-")
        p.description_store = f"{spec['name']}. Producto inventado para las pruebas de compra: no existe ni se vende."
        p.category_id, p.show_on_web, p.active, p.serialized = cat.id, True, True, False
        p.images, p.margin_hold, p.web_featured = [], False, spec["estado"] == "en_bodega"
        db.flush()
        if iva13 is not None and not p.taxes:
            p.taxes.append(ProductTax(tax_id=iva13.id))
        # proveedor: item VINCULADO (lo unico que la disponibilidad cuenta), con su cantidad y la fecha del dato
        item = db.scalar(
            select(SupplierItem).where(SupplierItem.tenant_id == t.id, SupplierItem.supplier_id == sup.id, SupplierItem.code == f"PF-{spec['code']}")
        )
        if spec["sup"] is None:
            if item is not None:
                item.product_id, item.active, item.stock = None, False, 0
            p.supplier_id = p.supplier_sku = p.supplier_stock = p.supplier_updated_at = None
        else:
            cantidad, dias = spec["sup"]
            cuando = ahora - timedelta(days=dias)
            if item is None:
                item = SupplierItem(tenant_id=t.id, supplier_id=sup.id, code=f"PF-{spec['code']}", name=spec["name"])
                db.add(item)
            item.name, item.product_id, item.link_method, item.linked_at = spec["name"], p.id, "manual", _aware(item.linked_at) or ahora
            item.stock, item.cost, item.currency, item.source, item.active = cantidad, Decimal(spec["price"]) / 2, "CRC", "manual", True
            item.list_date, item.changed_at = cuando.date(), cuando
            p.supplier_id, p.supplier_sku, p.supplier_stock, p.supplier_updated_at = sup.id, item.code, cantidad, cuando
        prods[spec["code"]] = p
    db.flush()

    # ---------- existencia propia: lo LIBRE vuelve a la cantidad de la semilla (las reservas de corridas viejas no restan) ----------
    filas = availability.availability_many(db, t, list(prods.values()), audience="interno")
    for spec in PRODUCTOS:
        p = prods[spec["code"]]
        falta = Decimal(spec["own"]) - Decimal(str(filas.get(p.id, {}).get("own_free") or 0))
        if falta and bodega is not None:
            db.add(
                StockMovement(
                    tenant_id=t.id,
                    product_id=p.id,
                    warehouse_id=bodega.id,
                    kind="entrada" if falta > 0 else "ajuste",
                    quantity=falta,
                    reference=REFERENCIA,
                    note="Semilla de la tienda de pruebas (O0-03)",
                    at=ahora,
                )
            )
    # la version del catalogo sube a mano (bump_catalog_version encola una tarea de Celery, que aqui no hay) y el
    # snapshot se arma ya: la primera visita de la prueba lo encuentra hecho
    ajustes = dict(t.settings or {})
    ajustes["catalog_v"] = int(ahora.timestamp() * 1000)
    t.settings = ajustes
    db.commit()
    store_snapshot.rebuild(db, t.id)

    publico = availability.availability_many(db, t, list(prods.values()), audience="publico")
    resumen = {
        "slug": t.slug,
        "dominio": DOMINIO,
        "productos": {
            code: {
                "id": p.id,
                "nombre": p.name,
                "precio_con_iva": float((Decimal(str(p.price)) * Decimal("1.13")).quantize(Decimal("0.01"))),
                "estado": publico[p.id]["state"],
                "etiqueta": publico[p.id]["label"],
            }
            for code, p in prods.items()
        },
    }
    malos = [
        f"{s['code']}: {resumen['productos'][s['code']]['estado']} (se esperaba {s['estado']})"
        for s in PRODUCTOS
        if resumen["productos"][s["code"]]["estado"] != s["estado"]
    ]
    if malos:
        raise RuntimeError("La semilla no dejó los estados esperados: " + "; ".join(malos))
    return resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Semilla de la tienda local de pruebas (compra de punta a punta, O0-03)")
    ap.add_argument("--forzar", action="store_true", help="sembrar aunque la empresa ya tenga productos propios en la web (no los toca)")
    ap.add_argument("--json", action="store_true", help="imprimir el resumen como JSON")
    args = ap.parse_args(argv)
    for salida in (sys.stdout, sys.stderr):  # la consola de Windows no siempre es UTF-8
        salida.reconfigure(encoding="utf-8", errors="replace")

    from app.core.config import settings

    try:
        revisar_entorno(settings.database_url, settings.env)
        from app.core.db import SessionLocal

        with SessionLocal() as db:
            resumen = sembrar(db, forzar=args.forzar)
    except SemillaRechazada as e:
        print(f"[semilla-e2e] No se sembró nada. {e}", file=sys.stderr, flush=True)
        return 2
    if args.json:
        print(json.dumps(resumen, ensure_ascii=False, indent=2), flush=True)
    else:
        print(f"[semilla-e2e] Tienda «{TIENDA['name']}» publicada en http://{DOMINIO} (empresa {resumen['slug']}).", flush=True)
        for code, r in resumen["productos"].items():
            print(f"[semilla-e2e]   {code:<10} #{r['id']:<4} {r['etiqueta']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
