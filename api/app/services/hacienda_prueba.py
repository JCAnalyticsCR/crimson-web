"""«Prueba real con Hacienda» (Ajustes -> Hacienda), siempre en el ambiente de PRUEBAS.

Para que el administrador pruebe Hacienda de verdad con SUS datos (cedula, credenciales y .p12 de Pruebas) sin pasar la
empresa a modo Pruebas (cada venta real iria al ambiente de pruebas) y sin gastar la numeracion real:
- una FE de 1 000 colones + IVA 13 % de la empresa a si misma, firmada con su certificado de Pruebas y validada contra
  el XSD; opcional, el mensaje receptor de aceptacion sobre esa misma FE;
- consecutivos T01 y T05 (nunca F0x), sin Invoice ni EInvoiceDocument, sin correos, el modo no cambia;
- consulta por peticion (sin worker ni Celery): iniciar() hace un POST y consultar() un GET por llamada.

Reglas de envio (D6): la fila de la corrida, el T01 y los bytes firmados van en UN commit antes del POST. Un reenvio
manda exactamente esos bytes, una sola vez, si Hacienda no encuentra la clave y pasaron 30 s. El mensaje receptor se
firma y se guarda (con su T05) en un commit antes de su POST y nunca se reenvia a ciegas.

Una prueba aceptada solo informa: no escribe last_test_*, no confirma consecutivos ni cuenta para Produccion.
Nunca se registra el usuario ATV, la contrasena, el PIN, el .p12 ni el token; en los logs solo el tipo de error y los
ultimos 8 digitos de la clave.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import HaciendaTestRun, ReceivedDocument, ReceptionResponse, Tenant, User
from ..models.fiscal import TEST_RUN_LIVE
from ..providers.einvoice import build_clave, cr_now, security_code
from ..providers.hacienda import xml44
from ..providers.hacienda.client import HaciendaAuthError, HaciendaUnavailable, ProductionBlocked, not_received, rate_limited
from ..providers.hacienda.provider import HaciendaDirectProvider, InvalidXml, receptor_body, receptor_ref
from ..providers.hacienda.xades import CertError, load_p12
from . import hacienda_config as hc
from .documents import audit
from .sequences import next_fiscal_consecutive

log = logging.getLogger("crimson.hacienda")
ENV = "pruebas"  # fijo: esta prueba no existe para otro ambiente
LIVE = TEST_RUN_LIVE
FINAL = ("aceptado", "rechazado", "error", "no_concluyente")
MR_PENDING = ("firmado", "enviado", "procesando")
MIN_POLL = timedelta(seconds=4)  # una consulta antes de esto devuelve el estado sin llamar a Hacienda
INICIAR_GRACE = timedelta(seconds=90)  # iniciar() hace el primer POST (token 20 s + POST 20 s): la consulta no lo pisa
RESEND_AFTER = timedelta(seconds=30)  # el sandbox puede tardar en indexar: no se reenvia antes
MR_INDEX_GRACE = timedelta(minutes=3)  # un 400 «no recibido» antes de esto es el sandbox indexando
STALE = timedelta(minutes=15)  # sin respuesta final en 15 min: 'sin_respuesta' (libera la empresa)
PER_HOUR, PER_DAY = 3, 10
KEEP = 20  # se conservan las ultimas 20 corridas por empresa
SUBTOTAL, IVA_RATE = Decimal(1000), Decimal(13)
# expresion del anexo 4.4 para CorreoElectronico (documentacion del XSD de FacturaElectronica)
EMAIL_RE = re.compile(r"\s*\w+([-+.']\w+)*@\w+([-.]\w+)*\.\w+([-.]\w+)*\s*")

TXT_TOKEN = "Hacienda no aceptó el usuario, la contraseña o el token de Pruebas. Revíselos con «Probar conexión» y vuelva a intentar."
TXT_MR_RUTA = "Ruta de consulta del mensaje receptor sin confirmar"


class RequisitosFallan(Exception):
    """Algun requisito no se cumple: no se envio nada ni se gasto numeracion. steps: lo revisado, paso por paso."""

    def __init__(self, steps: list[dict]):
        self.steps = steps
        malos = [s for s in steps if s["status"] == "falla"]
        super().__init__("No se envió nada a Hacienda: " + "; ".join(f"{s['label']}: {s['detail']}" for s in malos))


def _now() -> datetime:
    """Unico reloj del modulo (las pruebas lo adelantan)."""
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _tail(clave: str | None) -> str:
    return (clave or "")[-8:]


SONDA_CONSECUTIVO = "00199999019999999999"  # consecutivo que nunca se emite: la clave de sonda no existe en Hacienda


def sonda_clave(tax_id: str | None) -> str:
    """Clave de 50 digitos que no existe, sin gastar ningun consecutivo: solo para la consulta de estado de «Probar
    conexion» y del smoke (detecta una ruta de API que Hacienda no atiende sin enviar nada)."""
    return build_clave("506", cr_now(), tax_id or "0", SONDA_CONSECUTIVO, "1", "00000000")


# ---------------------------------------------------------------- armado del XML (sin tocar el de las ventas)
def armar_fe_prueba(
    db: Session, t: Tenant, cabys: str, clave: str, consecutive: str, fecha: datetime, lineas: list[dict] | None = None, medio_pago: str = "01"
) -> tuple[dict, dict]:
    """Payload de xml44.build_comprobante para la FE de prueba y sus montos (los toma el mensaje receptor).

    Sin `lineas` ni `medio_pago` es la FE de siempre (1 000 + IVA, efectivo 01). La Tienda de pruebas (09/11) manda una
    linea {"detail", "unit_price", "subtotal", "tax"} con el monto del pedido y el TipoMedioPago del pago (02 tarjeta, 06
    SINPE Movil): mismo CABYS de servicio, unidad 'Sp' e IVA 13 %.

    unit 'Sp' (servicios profesionales): xml44 clasifica servicio/mercancia por SERVICE_UNITS (xml44.py, linea del
    `service = ln["unit"] in SERVICE_UNITS`) y no por el CABYS. Por eso la prueba exige un CABYS de servicio (primer digito
    5 a 9): asi la clasificacion que arma Crimson (TotalServGravados) coincide con la que valida Hacienda por el CABYS."""
    prof = hc.profile(db, t)
    ced = xml44.digits(t.tax_id)
    act = hc.default_activity(db, t.id)
    iva = (SUBTOTAL * IVA_RATE / 100).quantize(Decimal("0.01"))
    montos = {"subtotal": SUBTOTAL, "iva": iva, "total": SUBTOTAL + iva}  # 1 000 + 130 = 1 130
    lines = [
        {
            "cabys": cabys,
            "code": "PRUEBA",
            "quantity": "1",
            "unit": "Sp",
            "detail": "Servicio de prueba de facturación electrónica",
            "unit_price": str(SUBTOTAL),
            "subtotal": str(SUBTOTAL),
            "tax": str(iva),
            "rate": str(IVA_RATE),
            "rate_code": "08",
        }
    ]
    if lineas:
        lines, sub, iva = [], Decimal(0), Decimal(0)
        for ln in lineas:
            ls, lt = Decimal(str(ln["subtotal"])), Decimal(str(ln["tax"]))
            sub, iva = sub + ls, iva + lt
            lines.append(
                {
                    "cabys": cabys,
                    "code": "PRUEBA",
                    "quantity": "1",
                    "unit": "Sp",
                    "detail": str(ln["detail"])[:160],
                    "unit_price": str(Decimal(str(ln.get("unit_price", ls)))),
                    "subtotal": str(ls),
                    "tax": str(lt),
                    "rate": str(IVA_RATE),
                    "rate_code": "08",
                }
            )
        montos = {"subtotal": sub, "iva": iva, "total": sub + iva}
    emisor = {
        "name": (t.legal_name or "").strip(),
        "id_type": prof.id_type,
        "id_number": ced,
        "commercial_name": prof.commercial_name,
        "province": prof.province,
        "canton": prof.canton,
        "district": prof.district,
        "barrio": prof.barrio,
        "other_signs": prof.other_signs,
        "phone_country": prof.phone_country or "506",
        "phone": prof.phone,
        "email": prof.email,
    }
    payload = {
        "doc_type": "FE",
        "clave": clave,
        "consecutive": consecutive,
        "fecha": fecha,
        "proveedor_sistemas": settings.hacienda_proveedor_sistemas or ced,  # como einvoice.direct_payload
        "activity_code": act.code if act else "",
        "emisor": emisor,
        "receptor": {"name": emisor["name"], "id_type": prof.id_type, "id_number": ced, "email": prof.email},
        "sale_condition": "01",
        "lines": lines,
        "currency": "CRC",
        "fx": 1,
        "payment_method": medio_pago or "01",
        "tax_total": montos["iva"],
        "total": montos["total"],
    }
    return payload, montos


def montos_de_fe(fe_xml: str) -> dict:
    """Los montos de la FE firmada (TotalVentaNeta, TotalImpuesto, TotalComprobante): el mensaje receptor los toma de
    ahi y no de constantes repetidas."""
    root = xml44.parse_xml_bytes(fe_xml.encode("utf-8"))
    return {
        "subtotal": xml44.dec(xml44.text(root, "ResumenFactura", "TotalVentaNeta")),
        "iva": xml44.dec(xml44.text(root, "ResumenFactura", "TotalImpuesto")),
        "total": xml44.dec(xml44.text(root, "ResumenFactura", "TotalComprobante")),
    }


def armar_mr_prueba(db: Session, t: Tenant, clave: str, mr_consecutive: str, montos: dict, emitted_at: datetime) -> tuple[ReceivedDocument, ReceptionResponse]:
    """Mensaje receptor de aceptacion (1, IVA 01) sobre la FE de prueba, SOLO en memoria (no se agrega a la sesion)."""
    prof = hc.profile(db, t)
    ced = xml44.digits(t.tax_id)
    act = hc.default_activity(db, t.id)
    iva, total = montos["iva"], montos["total"]
    doc = ReceivedDocument(tenant_id=t.id, clave=clave, issuer_id=ced, issuer_id_type=prof.id_type, tax_total=iva, total=total, iva_total=iva, doc_type="FE")
    r = ReceptionResponse(
        tenant_id=t.id,
        attempt=1,
        message_code="1",
        iva_condition_code="01",
        activity_code=act.code if act else None,
        tax_total=iva,
        total=total,
        credit_amount=iva,
        applicable_amount=total - iva,
        consecutive=mr_consecutive,
        mode=ENV,
        emitted_at=emitted_at,
        created_at=_now(),
    )
    return doc, r


# ---------------------------------------------------------------- requisitos
def _step(steps: list, label: str, status: str, detail: str) -> None:
    steps.append({"label": label, "status": status, "detail": detail})


def requisitos(db: Session, t: Tenant, cabys: str | None) -> tuple[list[dict], object | None]:
    """(pasos, credenciales o None). Revisa todo lo que Hacienda cruza ANTES de gastar un consecutivo. Los pasos no
    llevan secretos: solo los valores que Hacienda compara (actividad, proveedor de sistemas, identificaciones)."""
    steps: list[dict] = []
    prof = hc.profile(db, t)
    if hc.production_on(db, t):
        _step(steps, "Producción apagada", "falla", "La empresa tiene Producción activada: la prueba real solo se hace con Producción apagada")
    creds, err = hc.credentials_status(db, t, ENV)
    if not creds:
        _step(steps, "Credenciales de Pruebas", "falla", err or "Cargue las credenciales de Pruebas")
    else:
        _step(steps, "Credenciales de Pruebas", "ok", "Usuario, contraseña, certificado y PIN de Pruebas cargados")
        visto = hc.atv_user_env(creds.username)
        if visto and visto != ENV:
            _step(steps, "Usuario ATV del ambiente", "aviso", hc.atv_user_warning(ENV, visto) or "El usuario parece de otro ambiente")
    falta = hc.profile_missing(db, t)
    if falta:
        _step(steps, "Identificación y ubicación", "falla", "Complete Ajustes → Hacienda: falta " + ", ".join(falta))
    else:
        _step(steps, "Identificación y ubicación", "ok", "Perfil del emisor completo")
    ced = xml44.digits(t.tax_id)
    if creds:
        try:
            info = load_p12(creds.p12, creds.pin)
            cert_ced = xml44.digits(info.serial_number_attr)
            if cert_ced and ced and int(cert_ced) != int(ced):
                _step(steps, "Certificado de Pruebas", "falla", f"El certificado es de la cédula {info.serial_number_attr}, no de la empresa: Hacienda lo rechazaría")
            else:
                _step(steps, "Certificado de Pruebas", "ok", f"El PIN abre el certificado · vence el {info.not_after:%d/%m/%Y}")
        except CertError as e:
            _step(steps, "Certificado de Pruebas", "falla", str(e))
    # CABYS: de servicio (5 a 9) porque la linea va con unidad 'Sp' (ver armar_fe_prueba)
    code = re.sub(r"\D", "", cabys or "")
    if len(code) != 13:
        _step(steps, "CABYS", "falla", "El CABYS debe tener 13 dígitos")
    elif code[0] not in "56789":
        _step(steps, "CABYS", "falla", f"El CABYS {code} es de un bien: use uno de servicio (empieza con 5, 6, 7, 8 o 9)")
    else:
        from . import cabys as cabys_svc

        try:
            hit = cabys_svc.find_code(code)
        except cabys_svc.CabysUnavailable:
            hit = False
        except Exception:  # noqa: BLE001
            hit = False
        if hit is None:
            _step(steps, "CABYS", "falla", f"El CABYS {code} no existe en el catálogo de Hacienda")
        elif hit is False:
            _step(steps, "CABYS", "aviso", f"No se pudo confirmar el CABYS {code} con Hacienda en este momento; se envía igual")
        elif hit.get("tax_rate") is not None and Decimal(str(hit["tax_rate"])) != IVA_RATE:
            _step(steps, "CABYS", "falla", f"El CABYS {code} tiene IVA del {hit['tax_rate']:g} %; la prueba es con 13 %: elija uno de 13 %")
        else:
            _step(steps, "CABYS", "ok", f"{code} · {hit.get('description') or 'servicio'} · IVA 13 %")
    # telefono: Hacienda rechaza un NumTelefono corto o con el codigo de pais repetido
    tel = xml44.digits(prof.phone)
    pais = xml44.digits(prof.phone_country) or "506"
    if not tel:
        _step(steps, "Teléfono", "ok", "Sin teléfono (no se envía)")
    elif not (8 <= len(tel) <= 20):
        _step(steps, "Teléfono", "falla", f"El teléfono tiene {len(tel)} dígitos: debe tener de 8 a 20 (o dejarlo vacío)")
    elif tel.startswith(pais) and len(tel) > 8:
        _step(steps, "Teléfono", "falla", f"El teléfono empieza con {pais}: quítelo, Crimson ya agrega el código de país")
    else:
        _step(steps, "Teléfono", "ok", f"+{pais} {tel}")
    correo = (prof.email or "").strip()
    if not correo:
        _step(steps, "Correo", "falla", "Falta el correo de facturación (va en el Emisor y en el Receptor de la prueba)")
    elif any(ch in correo for ch in ";,") or re.search(r"\s", correo) or not EMAIL_RE.fullmatch(correo) or len(correo) > 160:
        _step(steps, "Correo", "falla", "El correo de facturación debe ser una sola dirección válida, sin comas, punto y coma ni espacios (va en el Emisor y en el Receptor)")
    else:
        _step(steps, "Correo", "ok", f"{correo} · va como correo del Emisor y del Receptor de la prueba")
    act = hc.default_activity(db, t.id)
    _step(
        steps,
        "Datos que Hacienda cruza",
        "ok",
        f"CodigoActividadEmisor {act.code if act else '—'} · ProveedorSistemas {settings.hacienda_proveedor_sistemas or ced or '—'} · "
        f"Emisor y Receptor tipo {prof.id_type or '—'} número {ced or '—'} · Teléfono {'se envía' if len(tel) >= 3 else 'no se envía'}",
    )
    return steps, creds


def _fails(steps: list[dict]) -> bool:
    return any(s["status"] == "falla" for s in steps)


# ---------------------------------------------------------------- consultas de estado
def _live_q(tid: int):
    return select(HaciendaTestRun).where(HaciendaTestRun.tenant_id == tid, HaciendaTestRun.state.in_(LIVE))


def _mr_pending(run: HaciendaTestRun) -> bool:
    return run.state == "aceptado" and bool(run.con_mr) and (run.mr_estado is None or run.mr_estado in MR_PENDING)


def is_live(run: HaciendaTestRun) -> bool:
    return run.state in LIVE or _mr_pending(run)


def _since(run: HaciendaTestRun) -> datetime:
    return _aware(run.fe_body_sent_at) or _aware(run.created_at) or _now()


def cerrar_vencidas(db: Session, t: Tenant) -> int:
    """Corridas vivas con mas de 15 min: 'sin_respuesta' sin llamar a Hacienda (asi la empresa no queda bloqueada). El
    mensaje receptor pendiente tambien se cierra. Devuelve cuantas cambio (sin commit)."""
    n = 0
    now = _now()
    for run in db.scalars(select(HaciendaTestRun).where(HaciendaTestRun.tenant_id == t.id, HaciendaTestRun.finished_at.is_(None))):
        if run.state in LIVE and now - _since(run) > STALE:
            run.state = "sin_respuesta"
            run.finished_at = now
            n += 1
        elif _mr_pending(run) and now - (_aware(run.mr_sent_at) or _aware(run.last_check_at) or _since(run)) > STALE:
            run.mr_estado = "sin_respuesta"
            run.finished_at = now
            n += 1
    if n:
        db.flush()
    return n


def blocked_reason(db: Session, t: Tenant) -> str | None:
    if hc.production_on(db, t):
        return "La empresa tiene Producción activada: la prueba real solo se hace con Producción apagada"
    if hc.credentials_status(db, t, ENV)[0] is None:
        return "Cargue las credenciales de Pruebas (usuario, contraseña, certificado y PIN) para hacer la prueba real"
    if db.scalar(_live_q(t.id).limit(1)) is not None:
        return "Ya hay una prueba en curso: espere su resultado"
    return _tope(db, t)


def _tope(db: Session, t: Tenant) -> str | None:
    """Topes en la base, por empresa: 3 por hora y 10 por dia."""
    now = _now()
    hora = db.scalar(select(func.count(HaciendaTestRun.id)).where(HaciendaTestRun.tenant_id == t.id, HaciendaTestRun.created_at >= now - timedelta(hours=1)))
    if (hora or 0) >= PER_HOUR:
        return f"Ya hizo {PER_HOUR} pruebas en la última hora: vuelva a intentar más tarde"
    dia = db.scalar(select(func.count(HaciendaTestRun.id)).where(HaciendaTestRun.tenant_id == t.id, HaciendaTestRun.created_at >= now - timedelta(days=1)))
    if (dia or 0) >= PER_DAY:
        return f"Ya hizo {PER_DAY} pruebas en las últimas 24 horas: vuelva a intentar mañana"
    return None


# ---------------------------------------------------------------- envio
def _fe_body(run: HaciendaTestRun) -> dict:
    """Cuerpo del POST con los MISMOS bytes guardados (reenvio): emisor, receptor y fecha salen del propio XML."""
    raw = run.fe_xml.encode("utf-8")
    root = xml44.parse_xml_bytes(raw)
    body = {
        "clave": run.clave,
        "fecha": xml44.text(root, "FechaEmision"),
        "emisor": {"tipoIdentificacion": xml44.text(root, "Emisor", "Identificacion", "Tipo"), "numeroIdentificacion": xml44.text(root, "Emisor", "Identificacion", "Numero")},
        "comprobanteXml": base64.b64encode(raw).decode(),
    }
    if xml44.text(root, "Receptor", "Identificacion", "Numero"):
        body["receptor"] = {"tipoIdentificacion": xml44.text(root, "Receptor", "Identificacion", "Tipo"), "numeroIdentificacion": xml44.text(root, "Receptor", "Identificacion", "Numero")}
    return body


def _finish(run: HaciendaTestRun) -> None:
    if not is_live(run) and run.finished_at is None:
        run.finished_at = _now()


def _apply_fe_post(run: HaciendaTestRun, res, *, first: bool) -> None:
    run.fe_post_cause = (res.cause or "")[:300] or None
    if res.kind == "ok":
        run.state = "enviado"
        run.fe_location = (res.location or "")[:300] or None
    elif res.kind == "already_received":
        # en el PRIMER envio no es aceptacion: Hacienda ya tenia esa clave (numeracion de pruebas repetida). En el reenvio
        # de los mismos bytes prueba que el primer POST si llego
        run.state = "no_concluyente" if first else "enviado"
    elif res.gateway:
        run.state = "error"
    elif res.kind == "rejected":
        run.state = "rechazado"
    # retry: queda como esta ('firmado'): la consulta decide
    _finish(run)


def _post_fe(db: Session, prov: HaciendaDirectProvider, run: HaciendaTestRun, *, first: bool) -> None:
    run.fe_body_sent_at = _now()
    db.commit()  # el intento queda registrado antes del POST: si el proceso muere, la consulta no lo toma por no enviado
    res = prov.post(_fe_body(run))
    _apply_fe_post(run, res, first=first)


def _audit_result(db: Session, run: HaciendaTestRun, user_id: int | None) -> None:
    if run.state not in LIVE:
        audit(db, run.tenant_id, user_id, "hacienda_test_result", "hacienda_test_run", run.id, {"estado": run.state, "fe": run.fe_estado, "mr": run.mr_estado, "clave_fin": _tail(run.clave)})


def _purge(db: Session, tid: int) -> None:
    ids = db.scalars(select(HaciendaTestRun.id).where(HaciendaTestRun.tenant_id == tid).order_by(HaciendaTestRun.created_at.desc(), HaciendaTestRun.id.desc())).all()
    for rid in ids[KEEP:]:
        old = db.get(HaciendaTestRun, rid)
        if old is not None and not is_live(old):
            db.delete(old)


def iniciar(db: Session, t: Tenant, user: User, cabys: str, con_mr: bool, ip: str | None = None) -> HaciendaTestRun:
    """Revisa, reserva la corrida, firma y envia la FE de prueba a Hacienda (PRUEBAS). Lanza HTTPException (409/429)
    o RequisitosFallan (422). Nunca 500 por Hacienda: los errores de token quedan en la corrida."""
    return _iniciar(db, t, user, cabys, con_mr, ip)


TIENDA_MEDIOS = ("02", "06")  # tarjeta y SINPE Movil (catalogo TipoMedioPago 4.4)


def iniciar_desde_pedido_prueba(db: Session, t: Tenant, user: User, o, cabys: str, medio_pago: str = "02", ip: str | None = None) -> HaciendaTestRun:
    """Factura de prueba de un pedido PAGADO de la Tienda de pruebas (services/test_shop.py): las MISMAS reglas que
    iniciar() (produccion apagada, una corrida viva, 3 por hora y 10 por dia, requisitos, T01 de pruebas, firma, XSD,
    un commit antes del POST y el modo sin cambiar), con una linea «Pedido de prueba PRB-xxxx», el monto del pedido y el
    TipoMedioPago del pago. Sin mensaje receptor (con_mr=False: no gasta un T05). Receptor: la empresa misma."""
    total = Decimal(str(o.total))
    sub, tax = Decimal(str(o.subtotal)), Decimal(str(o.tax))
    if total <= 0 or total > 2000 or sub + tax != total:
        raise HTTPException(422, "El monto del pedido de prueba no sirve para una factura de prueba (debe ser de ₡2 000 o menos)")
    lineas = [{"detail": f"Pedido de prueba {o.number}", "unit_price": sub, "subtotal": sub, "tax": tax}]
    return _iniciar(db, t, user, cabys, False, ip, origin="tienda_pruebas", lineas=lineas, medio_pago=medio_pago if medio_pago in TIENDA_MEDIOS else "02", amount=total)


def _iniciar(
    db: Session,
    t: Tenant,
    user: User,
    cabys: str,
    con_mr: bool,
    ip: str | None = None,
    *,
    origin: str = "ajustes",
    lineas: list[dict] | None = None,
    medio_pago: str = "01",
    amount: Decimal | None = None,
) -> HaciendaTestRun:
    if hc.production_on(db, t):  # aunque este bloqueada por el servidor: no se hace nada
        audit(db, t.id, user.id, "hacienda_test_refused", "tenant", t.id, {"motivo": "produccion"}, ip=ip)
        db.commit()
        raise HTTPException(409, "La empresa tiene Producción activada: la prueba real solo se hace con Producción apagada. No se hizo nada.")
    if cerrar_vencidas(db, t):
        db.commit()
    if db.scalar(_live_q(t.id).limit(1)) is not None:
        raise HTTPException(409, "Ya hay una prueba en curso: espere su resultado")
    why = _tope(db, t)
    if why:
        audit(db, t.id, user.id, "hacienda_test_refused", "tenant", t.id, {"motivo": "tope"}, ip=ip)
        db.commit()
        raise HTTPException(429, why)
    steps, creds = requisitos(db, t, cabys)
    if _fails(steps) or creds is None:
        audit(db, t.id, user.id, "hacienda_test_refused", "tenant", t.id, {"motivo": "requisitos", "pasos": [s["label"] for s in steps if s["status"] == "falla"]}, ip=ip)
        db.commit()
        raise RequisitosFallan(steps)
    modo_antes = hc.hacienda_mode(db, t)
    info = load_p12(creds.p12, creds.pin)
    code = re.sub(r"\D", "", cabys)
    run = HaciendaTestRun(
        tenant_id=t.id,
        created_by=user.id,
        created_at=_now(),
        environment=ENV,
        state="firmado",
        steps=steps,
        cabys=code,
        amount=amount if amount is not None else SUBTOTAL + (SUBTOTAL * IVA_RATE / 100),
        origin=origin,
        con_mr=bool(con_mr),
        fe_resent=False,
        polls=0,
        tax_id_at_run=(t.tax_id or "")[:30] or None,
        cert_sha256=info.sha256,
    )
    db.add(run)
    try:
        db.flush()  # la reserva va ANTES del consecutivo: dos pestanas a la vez -> una sola corrida
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Ya hay una prueba en curso: espere su resultado") from None
    prov = HaciendaDirectProvider(t, ENV, creds)
    cons = next_fiscal_consecutive(db, t.id, "01", ENV)
    fecha = cr_now()
    clave = build_clave("506", fecha, xml44.digits(t.tax_id), cons, "1", security_code())
    payload, _montos = armar_fe_prueba(db, t, code, clave, cons, fecha, lineas=lineas, medio_pago=medio_pago)
    try:
        signed = prov.sign_document(payload)  # firma + XSD
    except (InvalidXml, xml44.BuildError, CertError) as e:
        db.rollback()  # nada enviado: tampoco se gasta el T01 ni queda la corrida
        audit(db, t.id, user.id, "hacienda_test_refused", "tenant", t.id, {"motivo": "xml", "error": type(e).__name__}, ip=ip)
        db.commit()
        raise HTTPException(422, f"El XML de prueba no se pudo firmar o no pasa el esquema de Hacienda (no se envió nada): {str(e)[:300]}") from e
    run.clave, run.consecutive, run.fe_xml = clave, cons, signed.decode("utf-8")
    audit(db, t.id, user.id, "hacienda_test_send", "hacienda_test_run", run.id, {"consecutivo": cons, "clave_fin": _tail(clave), "con_mr": run.con_mr, "cabys": code, "origen": origin}, ip=ip)
    db.commit()  # UN commit: corrida + T01 + bytes firmados, antes del POST
    try:
        prov.client.token()
        _post_fe(db, prov, run, first=True)
    except HaciendaAuthError as e:
        log.warning("prueba real %s: %s", _tail(clave), type(e).__name__)
        run.state, run.fe_post_cause = "error", TXT_TOKEN[:300]
        _finish(run)
    except HaciendaUnavailable as e:
        log.warning("prueba real %s: %s", _tail(clave), type(e).__name__)
        run.fe_post_cause = str(e)[:300]  # queda 'firmado': la consulta lo reintenta con los mismos bytes
    except ProductionBlocked as e:
        run.state, run.fe_post_cause = "error", str(e)[:300]
        _finish(run)
    if hc.hacienda_mode(db, t) != modo_antes:
        raise RuntimeError("La prueba real cambió el modo de la empresa")
    _audit_result(db, run, user.id)
    _purge(db, t.id)
    db.commit()
    return run


# ---------------------------------------------------------------- consulta (un GET por llamada)
def _apply_fe_status(run: HaciendaTestRun, st) -> None:
    if st.kind in ("aceptado", "rechazado", "error"):
        run.state = {"aceptado": "aceptado", "rechazado": "rechazado", "error": "error"}[st.kind]
        run.fe_estado = st.kind
        run.fe_detalle = (st.detail or "")[:500] or None
        run.fe_respuesta_xml = st.xml
    elif st.gateway:
        run.state = "error"
        run.fe_post_cause = (st.detail or "")[:300] or None
    elif st.kind == "procesando" and run.state in ("firmado", "enviado"):
        run.state = "procesando"  # llego a Hacienda: 'sin_respuesta' solo cambia a un estado final


def _mr_ref(prov: HaciendaDirectProvider, run: HaciendaTestRun, doc, r) -> str:
    pref = prov.client.ep["api"] + "recepcion/"
    loc = run.mr_location or ""
    if loc.startswith(pref) and len(loc) > len(pref):
        return loc[len(pref) :]
    return receptor_ref(doc, r)


def _mr(db: Session, t: Tenant, prov: HaciendaDirectProvider, run: HaciendaTestRun) -> None:
    """Mensaje receptor de aceptacion sobre la FE aceptada: una sola vez (commit con T05 y bytes antes del POST)."""
    prof = hc.profile(db, t)
    ced = xml44.digits(t.tax_id)
    if run.mr_estado is None:
        montos = montos_de_fe(run.fe_xml)
        emitted = cr_now()
        cons = next_fiscal_consecutive(db, t.id, "05", ENV)
        doc, r = armar_mr_prueba(db, t, run.clave, cons, montos, emitted)
        try:
            signed = prov.sign_receptor(xml44.build_mensaje_receptor(r, doc, t, ced))
        except (InvalidXml, xml44.BuildError, CertError) as e:
            # sin rollback: se perderia la FE aceptada de esta consulta y el candado. Se gasta un T05 de Pruebas, sin efecto real
            run.mr_estado, run.mr_detalle = "error", f"El mensaje receptor no se pudo firmar o no pasa el esquema (no se envió): {str(e)[:300]}"
            return
        run.mr_consecutive, run.mr_xml, run.mr_estado = cons, signed.decode("utf-8"), "firmado"
        db.commit()  # D6: T05 y bytes guardados antes del POST; si algo falla despues, se consulta y no se reenvia
        run.mr_sent_at = _now()
        res = prov.post(receptor_body(r, doc, prof.id_type, ced, signed))
        run.mr_location = (res.location or "")[:300] or None
        if res.kind in ("ok", "already_received"):
            run.mr_estado = "enviado"
        elif res.gateway:
            run.mr_estado, run.mr_detalle = "error", (res.cause or "")[:500]
        elif res.kind == "rejected":
            run.mr_estado, run.mr_detalle = "rechazado", (res.cause or "")[:500]
        else:
            run.mr_detalle = (res.cause or "")[:500] or None  # sigue 'firmado': se consulta, nunca se reenvia
        return
    doc = ReceivedDocument(tenant_id=t.id, clave=run.clave)
    r = ReceptionResponse(tenant_id=t.id, consecutive=run.mr_consecutive)
    st = prov.get_status(_mr_ref(prov, run, doc, r))
    if st.kind in ("aceptado", "rechazado", "error"):
        run.mr_estado = st.kind
        run.mr_detalle = (st.detail or "")[:500] or None
        run.mr_respuesta_xml = st.xml
    elif st.gateway:
        run.mr_estado, run.mr_detalle = "error", (st.detail or "")[:500]
    elif st.kind == "procesando":
        run.mr_estado = "procesando"
    elif (st.kind == "not_found" or st.http_status == 400) and _now() - (_aware(run.mr_sent_at) or _now()) > MR_INDEX_GRACE:
        run.mr_detalle = TXT_MR_RUTA
    if run.mr_estado in MR_PENDING and _now() - (_aware(run.mr_sent_at) or _now()) > STALE:
        run.mr_estado = "sin_respuesta"


def consultar(db: Session, t: Tenant, run_id: int, user_id: int | None = None) -> HaciendaTestRun:
    """Un GET de estado por llamada, con la fila bloqueada. Siempre devuelve la corrida (nunca 500 por Hacienda)."""
    run = db.execute(select(HaciendaTestRun).where(HaciendaTestRun.id == run_id, HaciendaTestRun.tenant_id == t.id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Prueba no encontrada")
    now = _now()
    if not (is_live(run) or run.state == "sin_respuesta"):
        db.commit()
        return run
    last = _aware(run.last_check_at)
    if (last and now - last < MIN_POLL) or rate_limited():
        db.commit()  # suelta el bloqueo
        return run
    creds, _err = hc.credentials_status(db, t, ENV)
    if creds is None:
        db.commit()
        return run
    estado_antes = (run.state, run.mr_estado)
    run.last_check_at = now
    run.polls = (run.polls or 0) + 1
    try:
        prov = HaciendaDirectProvider(t, ENV, creds)
        if run.state == "firmado":
            if run.fe_body_sent_at is None:
                if now - (_aware(run.created_at) or now) >= INICIAR_GRACE:  # iniciar() no llego al POST (proceso caido)
                    prov.client.token()
                    _post_fe(db, prov, run, first=True)
            else:
                st = prov.get_status(run.clave)
                if st.kind == "not_found" or not_received(st, run.clave):
                    if not run.fe_resent and now - _aware(run.fe_body_sent_at) >= RESEND_AFTER:
                        run.fe_resent = True
                        _post_fe(db, prov, run, first=False)  # los MISMOS bytes, una sola vez
                else:
                    _apply_fe_status(run, st)
        elif run.state in ("enviado", "procesando", "sin_respuesta"):
            _apply_fe_status(run, prov.get_status(run.clave))
        elif _mr_pending(run):
            _mr(db, t, prov, run)
        if run.state == "aceptado" and _mr_pending(run) and run.mr_estado is None:
            _mr(db, t, prov, run)  # la FE salio aceptada en esta misma consulta: el MR va de una vez
        if run.state in LIVE and now - _since(run) > STALE:
            run.state = "sin_respuesta"
    except HaciendaAuthError as e:
        log.warning("prueba real %s: %s", _tail(run.clave), type(e).__name__)
        if _mr_pending(run):
            run.mr_estado, run.mr_detalle = "error", TXT_TOKEN
        else:
            run.state, run.fe_post_cause = "error", TXT_TOKEN[:300]
    except HaciendaUnavailable as e:
        log.warning("prueba real %s: %s", _tail(run.clave), type(e).__name__)
    except ProductionBlocked as e:
        run.state, run.fe_post_cause = "error", str(e)[:300]
    except Exception as e:  # noqa: BLE001 - la consulta siempre responde con el estado guardado, nunca 500
        tail = _tail(run.clave)
        db.rollback()
        log.warning("prueba real %s: la consulta falló (%s)", tail, type(e).__name__)
        return run
    _finish(run)
    if (run.state, run.mr_estado) != estado_antes:
        _audit_result(db, run, user_id)
    db.commit()
    return run


# ---------------------------------------------------------------- salida (sin XML ni secretos)
def out(db: Session, run: HaciendaTestRun) -> dict:
    by = db.get(User, run.created_by) if run.created_by else None
    return {
        "id": run.id,
        "origin": run.origin or "ajustes",
        "state": run.state,
        "live": is_live(run),
        "steps": run.steps or [],
        "clave": run.clave,
        "consecutive": run.consecutive,
        "mr_consecutive": run.mr_consecutive,
        "cabys": run.cabys,
        "amount": str(run.amount) if run.amount is not None else None,
        "con_mr": bool(run.con_mr),
        "fe_estado": run.fe_estado,
        "fe_detalle": run.fe_detalle,
        "fe_post_cause": run.fe_post_cause,
        "fe_resent": bool(run.fe_resent),
        "fe_llego": bool(run.fe_location or run.fe_estado or run.state in ("enviado", "procesando")),
        "mr_sent_at": run.mr_sent_at,
        "mr_estado": run.mr_estado,
        "mr_detalle": run.mr_detalle,
        "created_at": run.created_at,
        "fe_body_sent_at": run.fe_body_sent_at,
        "finished_at": run.finished_at,
        "last_check_at": run.last_check_at,
        "polls": run.polls or 0,
        "has_fe_xml": bool(run.fe_xml),
        "has_fe_respuesta": bool(run.fe_respuesta_xml),
        "has_mr_xml": bool(run.mr_xml),
        "has_mr_respuesta": bool(run.mr_respuesta_xml),
        "created_by_name": by.full_name if by else None,
    }


def cabys_sugerido(db: Session, t: Tenant) -> str | None:
    """Un CABYS de servicio (5 a 9) del catalogo de la empresa, si hay; si no, el de la ultima prueba."""
    from ..models import Product

    last = db.scalar(select(HaciendaTestRun.cabys).where(HaciendaTestRun.tenant_id == t.id, HaciendaTestRun.cabys.is_not(None)).order_by(HaciendaTestRun.id.desc()).limit(1))
    if last:
        return last
    for code in db.scalars(select(Product.cabys_code).where(Product.tenant_id == t.id, Product.cabys_code.is_not(None)).limit(500)):
        d = re.sub(r"\D", "", code or "")
        if len(d) == 13 and d[0] in "56789":
            return d
    return None
