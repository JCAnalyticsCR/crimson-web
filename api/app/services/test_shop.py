"""Tienda de pruebas (09/11, pedido de Andres): compras de prueba con ONVO en modo Test, AISLADAS de lo real.

Que la aisla:
- Pedidos en su propia tabla (store_test_order, numero PRB-0001), sin FK ni llamadas a pedidos, clientes, productos,
  inventario, cupones, numeracion ORD/F01, facturas, pagos, contabilidad ni correos. De documents solo usa audit (la
  bitacora); NO importa store_orders, events, mail ni add_payment/create_invoice, y nunca debe hacerlo.
- Llaves de pruebas solo por services/onvo_test (tabla aparte, solo onvo_test_). Sin llave: 409 y no se llama a ONVO.
- Metadata del checkout: {crimson_env: "test", test_order_id, tenant_id}; nunca order_id ni invoice_id. Comprador fijo
  «Cliente de prueba» (sin correo ni telefono). Regreso de ONVO al PANEL (/mi-tienda/pruebas, pide sesion), nunca a la
  tienda publica. Si un evento de pruebas llegara al camino real, onvo.apply_paid lo rechaza (onvo.is_test_meta).
- Configuracion en tenant.settings.store.test_shop, aparte de settings.store.checkout (tienda publica): «Tarjeta (ONVO)»
  se prueba aqui sin ofrecerse en la tienda publica. Se vuelve a validar al leer (no se confia en lo guardado).

Confirmar un pago (confirm): la sesion es de ESTE pedido y la abrio la llave de pruebas; ONVO la da pagada y su intencion
succeeded; metadata de la sesion (la que creo Crimson) y de la intencion: si viene y no coincide, «revisar»; si no viene,
se anota «no vino» sin tumbar el pago; monto exacto en centimos, moneda CRC y modo test cuando venga. Un fallo = estado
«revisar» con el detalle, nunca pagado. La misma intencion otra vez = «duplicado ignorado»; otra intencion pagada para un
pedido ya pagado = «revisar · pago doble». Un pago que llega con el pedido cancelado, vencido o fallido lo pasa a pagado
(con la nota). En el timeline y la evidencia solo van ids (cs_, pi_, evt_), estados, montos, moneda y mensajes recortados:
nunca el objeto crudo de ONVO (puede traer datos del comprador o de la tarjeta).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core.config import settings
from ..models import StoreTestOrder, Tenant, User
from ..providers.payments import OnvoError
from . import onvo_test
from .documents import audit

log = logging.getLogger("crimson.onvo")

STATUS_LABEL = {
    "nuevo": "Nuevo",
    "en_onvo": "En ONVO",
    "en_proceso": "En proceso",
    "pagado": "Pagado",
    "fallido": "Fallido",
    "cancelado": "Cancelado",
    "vencido": "Vencido",
    "revisar": "Revisar",
}
OPEN = ("nuevo", "en_onvo", "en_proceso")  # esperan un resultado de ONVO
METHODS = {"tarjeta": "Tarjeta", "sinpe_onvo": "SINPE Móvil (ONVO)"}
MEDIO_PAGO = {"tarjeta": "02", "sinpe_onvo": "06"}  # catalogo TipoMedioPago de Hacienda 4.4: 02 tarjeta, 06 SINPE Movil
PRODUCT = "[PRUEBA] Artículo de prueba"
BUYER = "Cliente de prueba"
DEFAULT_PRICE = Decimal(100)  # + IVA 13 % = 113 colones: cuadra al centimo en el XML de Hacienda
IVA = Decimal(13)
MAX_TOTAL = Decimal(2000)
MAX_QTY = 5
MAX_SESSIONS = 5
LIFETIME = timedelta(hours=24)
KEEP = 50
SEARCH_CALLS = 10  # consultas a ONVO como maximo para ubicar el pedido de un evento sin metadata
NO_KEY = "Cargue la llave secreta de pruebas en Ajustes → Pagos → Pasarelas (tarjeta «Pruebas») y habilite la Tienda de pruebas"
INTENT_FAILED = ("failed", "canceled", "cancelled")
INTENT_PENDING = ("processing",)
CHECK_LABEL = {
    "sesion": "Sesión del pedido",
    "pagado": "Pagado en ONVO",
    "ambiente": "Ambiente (modo test)",
    "meta_ambiente": "Metadata: ambiente",
    "meta_pedido": "Metadata: pedido",
    "meta_empresa": "Metadata: empresa",
    "monto": "Monto",
    "moneda": "Moneda",
    "metodo": "Método de pago",
    "pago_doble": "Pago doble",
}


# ---------------------------------------------------------------- utilidades
def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _q2(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(v) -> int:
    return int((Decimal(str(v)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _crc(v) -> str:
    d = _q2(v)
    return f"₡{d:,.2f}".replace(",", " ")


def _tl(o: StoreTestOrder, what: str, detail: str = "", *, evt: str | None = None, mode: str | None = None, ref: str | None = None) -> None:
    e = {"at": _now().isoformat(), "what": what[:160], "detail": (detail or "")[:300]}
    if evt:
        e["evt"] = str(evt)[:80]
    if mode:
        e["mode"] = str(mode)[:8]
    if ref:
        e["ref"] = str(ref)[:120]
    o.timeline = [*(o.timeline or []), e][-200:]  # lista nueva: la columna JSON no detecta cambios en sitio


def _onvo_msg(e: OnvoError) -> str:
    """Mensajes fijos: nunca el texto crudo de ONVO en la pantalla de pruebas."""
    if e.status == 0:
        return "Sin conexión con ONVO"
    if e.status in (401, 403):
        return "ONVO rechazó la llave de pruebas: revise que sea la secreta de modo Test"
    if e.status == 404:
        return "ONVO no encontró ese pago con la llave de pruebas (¿es de producción?)"
    if e.status == 409:
        return "ONVO todavía no lo da por pagado"
    if e.status == 429:
        return "ONVO pidió esperar un momento (demasiadas consultas)"
    return f"ONVO respondió con un error ({e.status})"


# ---------------------------------------------------------------- configuracion (aparte de la tienda publica)
def config(t: Tenant) -> dict:
    raw = (((t.settings or {}).get("store") or {}).get("test_shop")) or {}
    raw = raw if isinstance(raw, dict) else {}
    methods = raw.get("methods") if isinstance(raw.get("methods"), dict) else {}
    try:
        price = _q2(raw.get("price", DEFAULT_PRICE))
    except Exception:  # noqa: BLE001 - lo guardado se valida de nuevo: si no sirve, el de siempre
        price = DEFAULT_PRICE
    if price < 1 or quote(price, 1)[3] > MAX_TOTAL:
        price = DEFAULT_PRICE
    return {"methods": {k: bool(methods.get(k, True)) for k in METHODS}, "price": price, "product": PRODUCT}


def quote(price, qty: int) -> tuple[list[dict], Decimal, Decimal, Decimal]:
    from .totals import LineIn, compute_document

    doc = compute_document([LineIn(quantity=Decimal(qty), unit_price=_q2(price), tax_rate=IVA)])
    sub, tax = _q2(doc.subtotal), _q2(doc.tax_total)
    total = sub + tax
    line = {"name": PRODUCT, "quantity": qty, "unit_price": str(_q2(price)), "subtotal": str(sub), "tax_rate": str(IVA), "tax": str(tax), "total": str(total)}
    return [line], sub, tax, total


def save_config(db: Session, t: Tenant, user: User, methods: dict[str, bool], price: Decimal, ip: str | None = None) -> dict:
    price = _q2(price)
    if price < 1:
        raise HTTPException(422, "El precio base debe ser de al menos ₡1")
    if quote(price, 1)[3] > MAX_TOTAL:
        raise HTTPException(422, f"Con IVA el artículo pasa de {_crc(MAX_TOTAL)}: las pruebas son de ₡2 000 o menos")
    st = dict(t.settings or {})
    store = dict(st.get("store") or {})
    store["test_shop"] = {"methods": {k: bool(methods.get(k)) for k in METHODS}, "price": str(price)}
    st["store"] = store
    t.settings = st
    flag_modified(t, "settings")
    audit(db, t.id, user.id, "test_shop_config", "pedido_prueba", t.id, {"metodos": [k for k in METHODS if methods.get(k)], "precio": str(price)}, ip=ip)
    db.commit()
    return config(t)


# ---------------------------------------------------------------- pedidos
def _lock(db: Session, oid: int) -> StoreTestOrder:
    return db.get(StoreTestOrder, oid, with_for_update=True, populate_existing=True)


def get(db: Session, t: Tenant, oid: int) -> StoreTestOrder:
    o = db.get(StoreTestOrder, oid)
    if o is None or o.tenant_id != t.id:
        raise HTTPException(404, "Pedido de prueba no encontrado")
    return o


def _next_number(db: Session, t: Tenant) -> str:
    nums = db.scalars(select(StoreTestOrder.number).where(StoreTestOrder.tenant_id == t.id)).all()
    n = max((int(x.split("-")[-1]) for x in nums if x and x.split("-")[-1].isdigit()), default=0)
    return f"PRB-{n + 1:04d}"


def expire_lazy(o: StoreTestOrder) -> bool:
    """Vencimiento al leer (sin tarea del worker): nuevo o en ONVO por mas de 24 h -> vencido."""
    exp = _aware(o.expires_at)
    if o.status in ("nuevo", "en_onvo", "en_proceso") and exp is not None and _now() > exp:
        o.status = "vencido"
        _tl(o, "Vencido", "Pasaron 24 horas sin pago. Puede reabrirlo.")
        return True
    return False


def _open(db: Session, t: Tenant, o: StoreTestOrder, creds: onvo_test.OnvoTestCreds) -> str:
    back = f"{settings.public_base_url.rstrip('/')}/mi-tienda/pruebas?prueba={o.id}"
    try:
        it = onvo_test.adapter(creds.secret).create_checkout(
            amount_cents=cents(o.total),
            currency="CRC",
            description=f"PRUEBA · {o.number} · no se cobra",
            metadata={"crimson_env": "test", "test_order_id": o.id, "tenant_id": t.id},
            redirect_url=f"{back}&r=ok",
            cancel_url=f"{back}&r=cancelado",
            # ficticio y sin correo ni telefono: ONVO Test no le escribe a nadie real
            customer={"name": BUYER},
        )
    except OnvoError as e:
        log.warning("ONVO (modo Test) no abrió el checkout del pedido de prueba %s: status %s", o.number, e.status)
        _tl(o, "ONVO no abrió la página de pago", _onvo_msg(e))
        db.commit()
        raise HTTPException(502, "ONVO (modo pruebas) no abrió la página de pago. Pruebe de nuevo en un momento.") from None
    if not it.id or not it.checkout_url:
        _tl(o, "ONVO no devolvió la página de pago", "Sin sesión de Checkout")
        db.commit()
        raise HTTPException(502, "ONVO (modo pruebas) no abrió la página de pago. Pruebe de nuevo en un momento.")
    o.onvo_sessions = [*(o.onvo_sessions or []), {"id": it.id[:120], "at": _now().isoformat()}]
    o.status = "en_onvo"
    _tl(o, "Sesión abierta en ONVO (modo Test)", f"Sesión {it.id}", ref=it.id)
    return it.checkout_url


def create(db: Session, t: Tenant, user: User, method: str, qty: int, ip: str | None = None) -> tuple[StoreTestOrder, str]:
    cfg = config(t)
    if method not in METHODS:
        raise HTTPException(422, "Método de prueba desconocido")
    if not cfg["methods"][method]:
        raise HTTPException(422, f"«{METHODS[method]}» está apagado en la Tienda de pruebas")
    if not 1 <= qty <= MAX_QTY:
        raise HTTPException(422, f"La cantidad va de 1 a {MAX_QTY}")
    lines, sub, tax, total = quote(cfg["price"], qty)
    if total > MAX_TOTAL:
        raise HTTPException(422, f"El total ({_crc(total)}) pasa de ₡2 000: baje la cantidad o el precio de prueba")
    creds = onvo_test.creds(db, t.id)
    if creds is None:
        raise HTTPException(409, NO_KEY)
    now = _now()
    o = StoreTestOrder(
        tenant_id=t.id,
        number=_next_number(db, t),
        status="nuevo",
        method=method,
        currency="CRC",
        subtotal=sub,
        tax=tax,
        total=total,
        lines=lines,
        buyer={"name": BUYER},
        onvo_sessions=[],
        checks={},
        timeline=[],
        created_by=user.id,
        created_at=now,
        updated_at=now,
        expires_at=now + LIFETIME,
    )
    _tl(o, "Pedido de prueba creado", f"{qty} × {PRODUCT} · total {_crc(total)} CRC · quiere probar: {METHODS[method]}")
    db.add(o)
    db.flush()
    audit(db, t.id, user.id, "test_order_create", "pedido_prueba", o.id, {"numero": o.number, "total": str(total), "metodo": method}, ip=ip)
    url = _open(db, t, o, creds)
    _purge(db, t)
    db.commit()
    return o, url


def reopen(db: Session, t: Tenant, user: User, oid: int, ip: str | None = None) -> tuple[StoreTestOrder, str]:
    o = _lock(db, get(db, t, oid).id)
    expire_lazy(o)
    if o.status in ("pagado", "revisar", "en_proceso"):
        raise HTTPException(409, f"El pedido está «{STATUS_LABEL[o.status]}»: no se puede reabrir")
    if len(o.onvo_sessions or []) >= MAX_SESSIONS:
        raise HTTPException(409, f"Este pedido ya abrió {MAX_SESSIONS} pagos: cree un pedido de prueba nuevo")
    creds = onvo_test.creds(db, t.id)
    if creds is None:
        raise HTTPException(409, NO_KEY)
    antes = o.status
    o.expires_at = _now() + LIFETIME
    _tl(o, "Pago reabierto", f"Estaba «{STATUS_LABEL[antes]}»")
    url = _open(db, t, o, creds)
    audit(db, t.id, user.id, "test_order_reopen", "pedido_prueba", o.id, {"numero": o.number, "antes": antes}, ip=ip)
    db.commit()
    return o, url


def _purge(db: Session, t: Tenant) -> None:
    ids = db.scalars(
        select(StoreTestOrder.id).where(StoreTestOrder.tenant_id == t.id).order_by(StoreTestOrder.created_at.desc(), StoreTestOrder.id.desc())
    ).all()
    for oid in ids[KEEP:]:
        old = db.get(StoreTestOrder, oid)
        if old is not None:
            db.delete(old)


def clear(db: Session, t: Tenant, user: User, ip: str | None = None) -> int:
    """«Limpiar pruebas»: borra SOLO los pedidos de prueba de la empresa (nada real; las corridas de Hacienda quedan)."""
    rows = db.scalars(select(StoreTestOrder).where(StoreTestOrder.tenant_id == t.id)).all()
    for o in rows:
        db.delete(o)
    audit(db, t.id, user.id, "test_orders_clear", "pedido_prueba", t.id, {"borrados": len(rows)}, ip=ip)
    db.commit()
    return len(rows)


# ---------------------------------------------------------------- confirmacion del pago
def _api(db: Session, t: Tenant):
    c = onvo_test.query_creds(db, t.id)
    if c is None:
        raise OnvoError(0, "sin llave de pruebas de ONVO para consultar")
    return onvo_test.adapter(c.secret)


def _pm_type(pi: dict) -> str | None:
    for cand in (pi.get("paymentMethod"), pi.get("payment_method")):
        if isinstance(cand, dict) and cand.get("type"):
            return str(cand["type"]).lower()
    for k in ("paymentMethodType", "paymentMethodTypes"):
        v = pi.get(k)
        if isinstance(v, str) and v:
            return v.lower()
        if isinstance(v, list) and len(v) == 1:
            return str(v[0]).lower()
    return None


def _method_of(pi: dict) -> str | None:
    typ = _pm_type(pi)
    if not typ:
        return None
    if "card" in typ or "tarjeta" in typ:
        return "tarjeta"
    if "sinpe" in typ or "mobile" in typ:
        return "sinpe_onvo"
    return None


def _check(checks: dict, key: str, ok: bool | None, detalle: str, origen: str) -> None:
    checks[key] = {"label": CHECK_LABEL.get(key, key), "ok": ok, "detalle": detalle[:200], "origen": origen}


def _meta_checks(checks: dict, s: dict, pi: dict, o: StoreTestOrder, t: Tenant) -> None:
    for key, k, want in (("meta_ambiente", "crimson_env", "test"), ("meta_pedido", "test_order_id", o.id), ("meta_empresa", "tenant_id", t.id)):
        vals = []
        for obj, name in ((s, "sesión"), (pi, "intención")):
            m = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
            if m.get(k) not in (None, ""):
                vals.append((str(m[k]), name))
        if not vals:
            _check(checks, key, None, "no vino en la metadata de ONVO (no tumba el pago)", "—")
        elif all(v == str(want) for v, _ in vals):
            _check(checks, key, True, f"{k} = {want}", " y ".join(n for _, n in vals))
        else:
            malos = ", ".join(f"{n}: {v}" for v, n in vals if v != str(want))
            _check(checks, key, False, f"{k} no coincide ({malos}; se esperaba {want})", " y ".join(n for _, n in vals))


def confirm(db: Session, t: Tenant, o: StoreTestOrder, sid: str, source: str, *, s: dict | None = None, evt: str | None = None) -> str | None:
    """Aplica una sesion pagada de ONVO Test al pedido (bloqueado). None si ONVO no la da por pagada (consulta);
    en el webhook eso es OnvoError (el evento no se consume y ONVO lo reintenta)."""
    o = _lock(db, o.id)
    if sid not in [x.get("id") for x in (o.onvo_sessions or [])]:
        _tl(o, "ONVO reporta un pago de una sesión que no es de este pedido", f"Sesión {sid}: no se aplica", evt=evt, ref=sid)
        if o.status != "pagado":
            o.status = "revisar"
        return f"sesión {sid} ajena al pedido de prueba {o.number}: revisar"
    api = _api(db, t)
    if s is None:
        s = api.get_checkout(sid)
    if s.get("paymentStatus") != "paid":
        if source == "webhook":
            raise OnvoError(409, f"ONVO dice que la sesión {sid} no está pagada")
        return None
    pid = str(s.get("paymentIntentId") or "")
    pi = api.get_intent(pid) if pid else {}
    if pid and pi.get("status") != "succeeded":
        if source == "webhook":
            raise OnvoError(409, f"ONVO dice que la intención {pid} está «{pi.get('status')}»")
        return None
    key = pid or sid
    if o.onvo_intent_id == key and o.status in ("pagado", "revisar"):
        _tl(o, "Duplicado ignorado", f"La intención {key} ya estaba aplicada ({source})", evt=evt, ref=key)
        return f"pedido de prueba {o.number}: pago {key} ya aplicado (duplicado ignorado)"
    checks: dict = {}
    _check(checks, "sesion", True, f"{sid} es de {o.number} y la abrió la llave de pruebas", "Crimson")
    _check(checks, "pagado", True, f"sesión «paid» · intención {pid or '—'} «succeeded»", "sesión e intención")
    modes = [(m, n) for m, n in ((s.get("mode"), "sesión"), (pi.get("mode"), "intención")) if m]
    if not modes:
        _check(checks, "ambiente", None, "ONVO no informó el modo (la sesión la abrió la llave de pruebas)", "—")
    else:
        _check(checks, "ambiente", all(m == "test" for m, _ in modes), ", ".join(f"{n}: {m}" for m, n in modes), " y ".join(n for _, n in modes))
    _meta_checks(checks, s, pi, o, t)
    want = cents(o.total)
    amt, amt_src = (s.get("amountTotal"), "sesión") if s.get("amountTotal") is not None else (pi.get("amount"), "intención")
    pi_amt = pi.get("amount")
    try:
        ok_amt = amt is not None and int(amt) == want and (pi_amt is None or int(pi_amt) == want)
        shown = _crc(Decimal(int(amt)) / 100) if amt is not None else "sin monto"
    except (TypeError, ValueError):
        ok_amt, shown = False, "monto inválido"
    _check(checks, "monto", ok_amt, f"{shown} (se esperaba {_crc(o.total)})", amt_src)
    curs = [(c, n) for c, n in ((s.get("currency"), "sesión"), (pi.get("currency"), "intención")) if c]
    _check(
        checks,
        "moneda",
        bool(curs) and all(c == "CRC" for c, _ in curs),
        ", ".join(f"{n}: {c}" for c, n in curs) or "sin moneda",
        " y ".join(n for _, n in curs) or "—",
    )
    found = _method_of(pi)
    _check(
        checks,
        "metodo",
        True if found else None,
        METHODS.get(found or o.method, o.method),
        "intención" if found else "lo que eligió el administrador (ONVO no lo informó)",
    )
    if o.status in ("pagado", "revisar") and o.onvo_intent_id and o.onvo_intent_id != key:
        _check(checks, "pago_doble", False, f"Ya había un pago ({o.onvo_intent_id}) y llegó otro ({key})", "Crimson")
        o.checks = checks
        o.status = "revisar"
        _tl(o, "Revisar · pago doble", f"Otra sesión del mismo pedido también quedó pagada ({key})", evt=evt, ref=key)
        return f"pedido de prueba {o.number}: pago doble ({key}), revisar"
    o.checks = checks
    o.onvo_intent_id = key
    o.paid_method = found or o.method
    fails = [v["label"] for v in checks.values() if v["ok"] is False]
    if fails:
        o.status = "revisar"
        _tl(o, "Pago de ONVO con diferencias: revisar", "No coincide: " + ", ".join(fails), evt=evt, ref=key)
        return f"pedido de prueba {o.number}: revisar ({', '.join(fails)})"
    antes = o.status
    o.status, o.paid_at = "pagado", _now()
    tarde = f" (llegó con el pedido «{STATUS_LABEL[antes]}»)" if antes in ("cancelado", "vencido", "fallido") else ""
    _tl(
        o,
        "Pagado: confirmado con ONVO (modo Test)" + tarde,
        f"Sesión {sid} · intención {pid or '—'} · {_crc(o.total)} CRC · por {source}",
        evt=evt,
        mode="test",
        ref=key,
    )
    return f"pedido de prueba {o.number} pagado"


def _failed_msg(pi: dict) -> str | None:
    st = str(pi.get("status") or "")
    err = pi.get("lastPaymentError") or pi.get("error")
    msg = (err.get("message") if isinstance(err, dict) else None) or (str(err) if isinstance(err, str) else "")
    if st in INTENT_FAILED or (st == "requires_payment_method" and err):
        return (msg or "sin detalle")[:160]
    return None


def sync(db: Session, t: Tenant, oid: int, regreso: str | None = None) -> StoreTestOrder:
    """«Consultar ONVO» (y el regreso desde ONVO): revisa las sesiones del pedido con la llave de pruebas, de la mas
    reciente a la mas vieja, con el freno por sesion de services/onvo (cuida la cuota). Nunca llama a Hacienda."""
    from .onvo import _throttled

    o = _lock(db, get(db, t, oid).id)
    expire_lazy(o)
    if regreso in ("ok", "cancelado") and not any(e.get("what") == f"Regresó de ONVO ({regreso})" for e in (o.timeline or [])[-3:]):
        _tl(o, f"Regresó de ONVO ({regreso})", "Crimson consulta el resultado a ONVO")
    try:
        api = _api(db, t)
    except OnvoError:
        _tl(o, "No se pudo consultar a ONVO", "No hay llave de pruebas cargada")
        db.commit()
        return o
    latest = str((o.onvo_sessions or [{}])[-1].get("id") or "")
    for sess in reversed(o.onvo_sessions or []):
        sid = str(sess.get("id") or "")
        if not sid or _throttled(f"prueba:{sid}"):
            continue
        try:
            s = api.get_checkout(sid)
            if s.get("paymentStatus") == "paid":
                confirm(db, t, o, sid, "consulta", s=s)
                o = _lock(db, o.id)
                continue
            pid = str(s.get("paymentIntentId") or "")
            # rechazo o espera del banco: solo cuenta la sesion VIGENTE (una anterior rechazada no tumba la reabierta)
            if pid and sid == latest and o.status in OPEN:
                pi = api.get_intent(pid)
                msg = _failed_msg(pi)
                if msg is not None:
                    if not any(e.get("ref") == pid and e.get("what") == "ONVO rechazó el pago" for e in (o.timeline or [])):
                        _tl(o, "ONVO rechazó el pago", msg, ref=pid)
                    o.status = "fallido"
                elif str(pi.get("status") or "") in INTENT_PENDING and o.status != "en_proceso":
                    o.status = "en_proceso"
                    _tl(o, "En proceso", "ONVO recibió el pago y espera la confirmación del banco", ref=pid)
        except OnvoError as e:
            _tl(o, "No se pudo consultar a ONVO", _onvo_msg(e), ref=sid)
    if regreso == "cancelado" and o.status in ("nuevo", "en_onvo"):
        o.status = "cancelado"
        _tl(o, "Cancelado", "Se canceló en ONVO sin pago. Puede reabrirlo.")
    db.commit()
    return o


def cancel(db: Session, t: Tenant, oid: int) -> StoreTestOrder:
    return sync(db, t, oid, regreso="cancelado")


# ---------------------------------------------------------------- eventos del webhook de pruebas
def _by_meta(db: Session, t: Tenant, meta: dict) -> StoreTestOrder | None:
    if str(meta.get("crimson_env") or "") != "test" or not meta.get("test_order_id"):
        return None
    if meta.get("tenant_id") and str(meta["tenant_id"]) != str(t.id):
        return None
    try:
        o = db.get(StoreTestOrder, int(meta["test_order_id"]))
    except (TypeError, ValueError):
        return None
    return o if o is not None and o.tenant_id == t.id else None


def _recent(db: Session, t: Tenant, limit: int = 200, only_open: bool = False) -> list[StoreTestOrder]:
    q = select(StoreTestOrder).where(StoreTestOrder.tenant_id == t.id)
    if only_open:
        q = q.where(StoreTestOrder.status.in_(OPEN))
    return list(db.scalars(q.order_by(StoreTestOrder.id.desc()).limit(limit)))


def _by_session(db: Session, t: Tenant, sid: str) -> StoreTestOrder | None:
    return next((o for o in _recent(db, t) if sid in [x.get("id") for x in (o.onvo_sessions or [])]), None)


def _by_intent(db: Session, t: Tenant, pid: str) -> StoreTestOrder | None:
    o = db.scalar(select(StoreTestOrder).where(StoreTestOrder.tenant_id == t.id, StoreTestOrder.onvo_intent_id == pid).limit(1))
    if o is not None:
        return o
    return next((o for o in _recent(db, t) if any(e.get("ref") == pid for e in (o.timeline or []))), None)


def _session_of_intent(api, orders: list[StoreTestOrder], pid: str) -> tuple[StoreTestOrder, dict] | None:
    """Ubica la sesion de una intencion consultando a ONVO (tope SEARCH_CALLS consultas)."""
    calls = 0
    for o in orders:
        for sess in reversed(o.onvo_sessions or []):
            if calls >= SEARCH_CALLS:
                return None
            calls += 1
            try:
                s = api.get_checkout(str(sess.get("id")))
            except OnvoError as e:
                if e.status == 404:
                    continue
                raise
            if str(s.get("paymentIntentId") or "") == pid:
                return o, s
    return None


def handle_event(db: Session, t: Tenant, payload: dict, *, evt_id: str | None = None, mode: str | None = None) -> str:
    """Evento ya autenticado del webhook de PRUEBAS. Solo resuelve pedidos de prueba de la empresa (metadata
    crimson_env/test_order_id, o la sesion/intencion conocida); ignora order_id e invoice_id. OnvoError = el evento no se
    consume (503 y ONVO reintenta)."""
    typ = str(payload.get("type") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    oid = str(data.get("id") or "")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if (meta.get("order_id") or meta.get("invoice_id")) and not meta.get("test_order_id"):
        return "evento con un pedido o una factura reales en la metadata: la Tienda de pruebas no lo aplica"
    if typ == "checkout-session.succeeded":
        o = _by_meta(db, t, meta) or _by_session(db, t, oid)
        if o is None:
            return f"sesión {oid} sin pedido de prueba identificable: no cambia nada"
        return confirm(db, t, o, oid, "webhook", evt=evt_id) or "sin cambios"
    if typ in ("payment-intent.succeeded", "payment-intent.failed", "payment-intent.deferred"):
        o = _by_meta(db, t, meta) or _by_intent(db, t, oid)
        api = _api(db, t)
        s = None
        if o is not None and typ == "payment-intent.succeeded":
            hit = _session_of_intent(api, [o], oid)
            s = hit[1] if hit else None
        if o is None:
            hit = _session_of_intent(api, _recent(db, t, 20, only_open=typ != "payment-intent.succeeded"), oid)
            if hit:
                o, s = hit
        if o is None:
            what = {"payment-intent.succeeded": "intención pagada", "payment-intent.failed": "intento fallido", "payment-intent.deferred": "pago en proceso"}[
                typ
            ]
            return f"{what} sin pedido de prueba identificable: no cambia nada (lo resuelve «Consultar ONVO» o checkout_session.succeeded)"
        if typ == "payment-intent.succeeded":
            if s is None:
                return f"intención {oid} sin sesión del pedido {o.number}: la aplica checkout_session.succeeded"
            return confirm(db, t, o, str(s.get("id")), "webhook", s=s, evt=evt_id) or "sin cambios"
        o = _lock(db, o.id)
        if typ == "payment-intent.failed":
            err = data.get("error") or data.get("lastPaymentError") or {}
            msg = ((err.get("message") if isinstance(err, dict) else None) or "sin detalle")[:160]
            _tl(o, "ONVO rechazó el pago", msg, evt=evt_id, mode=mode, ref=oid)
            if o.status in OPEN:
                o.status = "fallido"
            return f"pedido de prueba {o.number}: intento rechazado"
        _tl(o, "En proceso", "ONVO recibió el pago y espera la confirmación del banco", evt=evt_id, mode=mode, ref=oid)
        if o.status in ("nuevo", "en_onvo"):
            o.status = "en_proceso"
        return f"pedido de prueba {o.number}: en proceso"
    return f"evento {typ or '(sin tipo)'} ignorado"


def note_duplicate(db: Session, t: Tenant, payload: dict, evt_id: str | None) -> None:
    """El mismo evt_ otra vez: queda «duplicado ignorado» en la linea de tiempo (sin consultar a ONVO). Nunca falla."""
    try:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        oid = str(data.get("id") or "")
        o = _by_meta(db, t, meta) or _by_session(db, t, oid) or _by_intent(db, t, oid)
        if o is None:
            return
        o = _lock(db, o.id)
        _tl(o, "Duplicado ignorado", f"ONVO reenvió el aviso {evt_id or oid}: ya estaba procesado", evt=evt_id, mode="test")
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("no se pudo anotar el duplicado de la tienda de pruebas")


# ---------------------------------------------------------------- factura de prueba en Hacienda (ambiente de Pruebas)
def invoice(db: Session, t: Tenant, user: User, oid: int, cabys: str, ip: str | None = None):
    from ..models import HaciendaTestRun
    from . import hacienda_prueba as hp

    o = _lock(db, get(db, t, oid).id)
    if o.status != "pagado":
        raise HTTPException(409, "Solo un pedido de prueba «Pagado» puede tener factura de prueba")
    if o.hacienda_test_run_id:
        prev = db.get(HaciendaTestRun, o.hacienda_test_run_id)
        if prev is not None and prev.tenant_id == t.id and (hp.is_live(prev) or prev.state == "aceptado"):
            raise HTTPException(409, "Este pedido ya tiene su factura de prueba")
    medio = MEDIO_PAGO.get(o.paid_method or o.method, "02")
    run = hp.iniciar_desde_pedido_prueba(db, t, user, o, cabys, medio, ip=ip)
    o = _lock(db, o.id)
    o.hacienda_test_run_id = run.id
    _tl(
        o,
        "Factura de prueba enviada a Hacienda (ambiente de Pruebas)",
        f"Consecutivo {run.consecutive} · clave …{(run.clave or '')[-8:]} · TipoMedioPago {medio} · estado {run.state}",
        ref=run.clave,
    )
    db.commit()
    return o, run


# ---------------------------------------------------------------- salida
def out(db: Session, o: StoreTestOrder, *, with_run: bool = True) -> dict:
    run = None
    if with_run and o.hacienda_test_run_id:
        from ..models import HaciendaTestRun
        from . import hacienda_prueba as hp

        r = db.get(HaciendaTestRun, o.hacienda_test_run_id)
        if r is not None and r.tenant_id == o.tenant_id:
            run = hp.out(db, r)  # lo guardado: un GET nunca llama a Hacienda
    return {
        "id": o.id,
        "number": o.number,
        "status": o.status,
        "status_label": STATUS_LABEL.get(o.status, o.status),
        "method": o.method,
        "method_label": METHODS.get(o.method, o.method),
        "paid_method": o.paid_method,
        "currency": o.currency,
        "subtotal": str(_q2(o.subtotal)),
        "tax": str(_q2(o.tax)),
        "total": str(_q2(o.total)),
        "lines": o.lines or [],
        "buyer": o.buyer or {},
        "sessions": [x.get("id") for x in (o.onvo_sessions or [])],
        "intent_id": o.onvo_intent_id,
        "checks": o.checks or {},
        "timeline": o.timeline or [],
        "created_at": o.created_at,
        "paid_at": o.paid_at,
        "expires_at": o.expires_at,
        "can_reopen": o.status in ("nuevo", "en_onvo", "cancelado", "vencido", "fallido") and len(o.onvo_sessions or []) < MAX_SESSIONS,
        "hacienda_test_run_id": o.hacienda_test_run_id,
        "hacienda": run,
    }


def evidence(db: Session, t: Tenant, oid: int) -> dict:
    o = get(db, t, oid)
    d = out(db, o)
    return {
        "aviso": "Evidencia de una compra de PRUEBA (ONVO modo Test): no se cobró dinero real ni se tocó nada real.",
        "empresa": t.name,
        "generado": _now().isoformat(),
        "pedido": {
            k: d[k]
            for k in (
                "number",
                "status_label",
                "method_label",
                "paid_method",
                "currency",
                "subtotal",
                "tax",
                "total",
                "lines",
                "buyer",
                "created_at",
                "paid_at",
            )
        },
        "onvo": {"sesiones": d["sessions"], "intencion": d["intent_id"], "ambiente": "test"},
        "chequeos": d["checks"],
        "linea_de_tiempo": d["timeline"],
        "hacienda": {k: (d["hacienda"] or {}).get(k) for k in ("state", "consecutive", "clave", "fe_estado", "fe_detalle", "amount", "created_at")}
        if d["hacienda"]
        else None,
    }


def counts(db: Session, t: Tenant) -> dict:
    rows = db.execute(select(StoreTestOrder.status, func.count()).where(StoreTestOrder.tenant_id == t.id).group_by(StoreTestOrder.status)).all()
    return {s: n for s, n in rows}
