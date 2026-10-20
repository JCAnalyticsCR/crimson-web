"""Prueba de punta a punta contra el AMBIENTE DE PRUEBAS de Hacienda (manual; no corre en CI).

Uso:  python -m app.tools.hacienda_smoke --tenant <slug> --cabys <codigo de 13 digitos> --confirmo-pruebas [--sin-modo]

Solo ambiente de pruebas (el cliente se arma con environment="pruebas" fijo: la guarda del constructor impide otro). No
toca facturas reales ni la numeracion visible: arma el comprobante en memoria (no crea Invoice), usa consecutivos T01 y
T05, y no manda correos. Pasos: token -> FE de 1 000 colones + IVA 13 % de la empresa a si misma -> envio -> consulta
hasta estado final (max. 10 min) -> mensaje receptor de aceptacion sobre esa FE -> consulta. Imprime estados y
DetalleMensaje; nunca credenciales ni tokens. Sirve para confirmar lo marcado "a confirmar" (ruta de estado del mensaje
receptor, clasificacion servicio/mercancia, si hace falta la cadena del certificado).

--sin-modo (plan maestro 1.11): usa las credenciales de PRUEBAS aunque la empresa este en "ninguno" (o "simulado") y
NO toca el modo. Asi se valida Hacienda sin pasar la empresa real a Pruebas, que mandaria cada venta real al ambiente de
pruebas y despues no se podria emitir en produccion. Exige --confirmo-pruebas. Con produccion activa no hace nada.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select


def _wait(prov, ref: str, label: str, limit_s: int = 600) -> str:
    t0 = time.monotonic()
    delay = 10
    while time.monotonic() - t0 < limit_s:
        st = prov.get_status(ref)
        print(f"  {label}: {st.kind}" + (f" · {st.detail}" if st.detail else ""))
        if st.kind in ("aceptado", "rechazado", "error"):
            return st.kind
        time.sleep(delay)
        delay = min(delay * 2, 60)
    return "sin respuesta"


def mode_problem(mode: str, sin_modo: bool, confirmo: bool) -> str | None:
    """Por que NO se corre (None = se puede). Nunca cambia el modo de la empresa."""
    if not confirmo:
        return "Falta --confirmo-pruebas: no se hace nada"
    if mode == "produccion":
        return "La empresa está en Producción: esta prueba no se corre (no se hace nada)"
    if mode == "pruebas":
        return None
    if sin_modo:
        return None  # ninguno o simulado: las credenciales de pruebas sin tocar el modo ni las ventas
    return "La empresa no está en modo Pruebas de Hacienda: no se hace nada (use --sin-modo para probar sin cambiar el modo)"


def smoke(db, t, cabys: str, prov, wait: Callable[[object, str, str], str] = _wait) -> int:
    """Los pasos de la prueba con un proveedor ya armado (HaciendaDirectProvider de pruebas, o uno falso en las pruebas
    automaticas). No crea facturas, no cambia el modo y solo gasta consecutivos T01/T05 (los de pruebas)."""
    from ..models import ReceivedDocument, ReceptionResponse
    from ..providers.einvoice import build_clave, cr_now, security_code
    from ..providers.hacienda import xml44
    from ..providers.hacienda.provider import comprobante_body, receptor_body
    from ..services import hacienda_config as hc
    from ..services.sequences import next_fiscal_consecutive

    prof = hc.profile(db, t)
    prov.client.token(force=True)
    print("Token: OK")
    ced = xml44.digits(t.tax_id)
    fecha = cr_now()
    cons = next_fiscal_consecutive(db, t.id, "01", "pruebas")
    clave = build_clave("506", fecha, ced, cons, "1", security_code())
    act = hc.default_activity(db, t.id)
    emisor = {
        "name": t.legal_name,
        "id_type": prof.id_type,
        "id_number": ced,
        "commercial_name": prof.commercial_name,
        "province": prof.province,
        "canton": prof.canton,
        "district": prof.district,
        "barrio": prof.barrio,
        "other_signs": prof.other_signs,
        "phone_country": prof.phone_country,
        "phone": prof.phone,
        "email": prof.email,
    }
    payload = {
        "doc_type": "FE",
        "clave": clave,
        "consecutive": cons,
        "fecha": fecha,
        "proveedor_sistemas": ced,
        "activity_code": act.code if act else "",
        "emisor": emisor,
        "receptor": {"name": t.legal_name, "id_type": prof.id_type, "id_number": ced, "email": prof.email},
        "sale_condition": "01",
        "lines": [
            {"cabys": cabys, "code": "PRUEBA", "quantity": "1", "unit": "Sp", "detail": "Servicio de prueba", "unit_price": "1000", "subtotal": "1000", "tax": "130", "rate": "13", "rate_code": "08"}
        ],
        "currency": "CRC",
        "fx": 1,
        "payment_method": "01",
    }
    signed = prov.sign_document(payload)
    res = prov.post(comprobante_body(payload, signed))
    print(f"FE {clave}: POST {res.kind}" + (f" · {res.cause}" if res.cause else ""))
    if res.kind not in ("ok", "already_received"):
        db.rollback()
        return 1
    db.commit()
    if wait(prov, clave, "FE") != "aceptado":
        return 1
    # mensaje receptor de aceptacion sobre esa FE (en memoria, sin registrar la recepcion)
    doc = ReceivedDocument(tenant_id=t.id, clave=clave, issuer_id=ced, issuer_id_type=prof.id_type, tax_total=Decimal(130), total=Decimal(1130), iva_total=Decimal(130), doc_type="FE")
    r = ReceptionResponse(
        tenant_id=t.id,
        attempt=1,
        message_code="1",
        iva_condition_code="01",
        activity_code=act.code if act else None,
        tax_total=Decimal(130),
        total=Decimal(1130),
        credit_amount=Decimal(130),
        applicable_amount=Decimal(1000),
        consecutive=next_fiscal_consecutive(db, t.id, "05", "pruebas"),
        mode="pruebas",
        emitted_at=cr_now(),
        created_at=datetime.now(UTC),
    )
    mr = prov.sign_receptor(xml44.build_mensaje_receptor(r, doc, t, ced))
    res = prov.post(receptor_body(r, doc, prof.id_type, ced, mr))
    print(f"Mensaje receptor {r.consecutive}: POST {res.kind}" + (f" · {res.cause}" if res.cause else ""))
    db.commit()
    if res.kind not in ("ok", "already_received"):
        return 1
    return 0 if wait(prov, f"{clave}-{r.consecutive}", "Mensaje receptor") == "aceptado" else 1


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - herramienta manual con red
    ap = argparse.ArgumentParser(description="Prueba contra el ambiente de PRUEBAS de Hacienda")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--cabys", required=True)
    ap.add_argument("--confirmo-pruebas", action="store_true", required=True)
    ap.add_argument("--sin-modo", action="store_true", help="usar las credenciales de pruebas sin cambiar el modo de la empresa")
    args = ap.parse_args(argv)
    from ..core.db import SessionLocal
    from ..models import Tenant
    from ..providers.hacienda.provider import HaciendaDirectProvider
    from ..services import hacienda_config as hc

    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == args.tenant))
        if not t:
            print("Empresa no encontrada")
            return 1
        mode = hc.hacienda_mode(db, t)
        problem = mode_problem(mode, args.sin_modo, args.confirmo_pruebas)
        if problem:
            print(problem)
            return 1
        if mode != "pruebas":
            print(f"Modo de la empresa: {mode} (no se cambia). Se usan las credenciales de PRUEBAS.")
        creds, err = hc.credentials_status(db, t, "pruebas")
        if not creds:
            print(err)
            return 1
        return smoke(db, t, args.cabys, HaciendaDirectProvider(t, "pruebas", creds))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
