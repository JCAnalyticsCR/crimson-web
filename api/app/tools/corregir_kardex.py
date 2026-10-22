"""Correccion de la diferencia de Kardex (reunion 21/10, item 13): existencias negativas y salidas adelantadas.

Causa encontrada: al confirmarse el pago de un pedido web, la factura automatica (store_orders.auto_invoice ->
store_inventory.fulfill_for_order) sacaba de bodega TODO el pedido, tambien lo que se vende bajo pedido y todavia no
se compro ni se recibio. La bodega quedaba en negativo ("salio mas de lo que entro") hasta que alguien recibia la
compra, y si la compra nunca se registraba (pruebas, entrega directa del proveedor) el negativo quedaba para siempre.
Desde esta version facturar no saca lo que se espera del proveedor: sale al despachar, consumiendo la reserva que deja
la compra al recibirse.

Esta herramienta corrige lo que quedo de antes, con movimientos documentados (nunca borra ni edita el kardex):
1. Salidas adelantadas (pedido pagado y facturado, sin despachar, con la compra sin recibir): se devuelve a la bodega
   lo que la factura saco de mas, ligado al pedido y a su factura. Sale al despachar.
2. Existencias negativas que quedan: AJUSTE que deja la bodega en cero, con referencia AJ-SALUD-<fecha>, el motivo en
   la nota y la bitacora. Si hay una compra del producto sin recibir no se ajusta (recibirla es la correccion) salvo
   con --forzar.

Uso (por defecto solo muestra, no escribe nada):
    python -m app.tools.corregir_kardex                         # lista lo que corregiria, todas las empresas
    python -m app.tools.corregir_kardex --empresa 1             # solo una empresa
    python -m app.tools.corregir_kardex --empresa 1 --aplicar --motivo "Conteo fisico 21/10: el sensor no existe en bodega"
Lo mismo se hace desde el panel: Inventarios -> Salud -> Corregir (con el motivo).
Correrlo dos veces no duplica: lo ya corregido no vuelve a salir.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Order, Product, Tenant, Warehouse
from ..services import inventory as invsvc
from ..services import inventory_health, store_inventory
from ..services.documents import audit


def plan(db: Session, tenant_id: int) -> dict:
    """Lo que se corregiria en una empresa: salidas adelantadas por pedido y negativos por (producto, bodega)."""
    adelantadas = []
    for o in db.scalars(
        select(Order).where(
            Order.tenant_id == tenant_id,
            Order.channel == "tienda",
            Order.invoice_id.is_not(None),
            Order.status.not_in(("enviado", "entregado", "cancelado", "reembolsado")),
        )
    ):
        ex = store_inventory.early_exit(db, o)
        if ex:
            adelantadas.append({"order_id": o.id, "order": o.number, "productos": {pid: str(q) for pid, q in ex.items()}})
    negativos = []
    whs = {w.id: w.name for w in db.scalars(select(Warehouse).where(Warehouse.tenant_id == tenant_id))}
    for lv in invsvc.stock_levels(db, tenant_id):
        if lv["quantity"] < 0:
            p = db.get(Product, lv["product_id"])
            negativos.append(
                {
                    "product_id": lv["product_id"],
                    "warehouse_id": lv["warehouse_id"],
                    "producto": f"{p.code} · {p.name}" if p else f"#{lv['product_id']}",
                    "bodega": whs.get(lv["warehouse_id"], f"#{lv['warehouse_id']}"),
                    "existencia": str(lv["quantity"]),
                    "compras_sin_recibir": inventory_health._open_purchases(db, tenant_id, lv["product_id"]),
                }
            )
    return {"adelantadas": adelantadas, "negativos": negativos}


def apply(db: Session, tenant_id: int, motivo: str, *, force: bool = False, user_id: int | None = None) -> dict:
    """Aplica las correcciones (sin commit). Primero las salidas adelantadas (pueden resolver el negativo solas) y
    despues los negativos que queden. Devuelve lo hecho y lo que no se pudo (con el motivo)."""
    hechos = {"devoluciones": 0, "ajustes": 0, "omitidos": []}
    for a in plan(db, tenant_id)["adelantadas"]:
        o = db.get(Order, a["order_id"])
        hechos["devoluciones"] += store_inventory.undo_early_exit(db, o, user_id)
    db.flush()
    for n in plan(db, tenant_id)["negativos"]:
        try:
            hechos["ajustes"] += inventory_health.fix_negative(db, tenant_id, n["product_id"], n["warehouse_id"], motivo, user_id, force=force)
        except Exception as e:  # noqa: BLE001 - HTTPException con el motivo; se informa y se sigue
            hechos["omitidos"].append({"producto": n["producto"], "bodega": n["bodega"], "motivo": getattr(e, "detail", str(e))})
    if hechos["devoluciones"] or hechos["ajustes"]:
        audit(db, tenant_id, user_id, "kardex_fix", "stock", 0, {"devoluciones": hechos["devoluciones"], "ajustes": hechos["ajustes"], "motivo": motivo[:240]})
    return hechos


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(prog="python -m app.tools.corregir_kardex", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--empresa", type=int, help="id de la empresa (tenant); por defecto todas las activas")
    ap.add_argument("--aplicar", action="store_true", help="escribe las correcciones (sin esto solo muestra)")
    ap.add_argument("--motivo", default="", help="motivo del ajuste de los negativos (obligatorio con --aplicar si hay negativos)")
    ap.add_argument("--forzar", action="store_true", help="ajusta aunque haya compras del producto sin recibir")
    args = ap.parse_args()

    from ..core.db import SessionLocal

    with SessionLocal() as db:
        q = select(Tenant).where(Tenant.active)
        if args.empresa:
            q = select(Tenant).where(Tenant.id == args.empresa)
        for t in db.scalars(q).all():
            p = plan(db, t.id)
            print(f"== Empresa {t.id} · {t.name}")
            for a in p["adelantadas"]:
                print(f"  salida adelantada · pedido {a['order']} · productos {a['productos']}")
            for n in p["negativos"]:
                extra = f" · compras sin recibir: {', '.join(n['compras_sin_recibir'])}" if n["compras_sin_recibir"] else ""
                print(f"  negativo · {n['producto']} · {n['bodega']} · existencia {n['existencia']}{extra}")
            if not p["adelantadas"] and not p["negativos"]:
                print("  todo cuadra")
                continue
            if not args.aplicar:
                continue
            if p["negativos"] and len(args.motivo.strip()) < 5:
                raise SystemExit("Falta --motivo (queda en el kardex de cada ajuste)")
            res = apply(db, t.id, args.motivo, force=args.forzar)
            db.commit()
            print(f"  aplicado: {res['devoluciones']} devoluciones, {res['ajustes']} ajustes")
            for o in res["omitidos"]:
                print(f"  sin ajustar · {o['producto']} · {o['bodega']}: {o['motivo']}")


if __name__ == "__main__":  # pragma: no cover
    main()
