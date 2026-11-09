"""Llaves de ONVO de PRUEBAS (modo Test): la UNICA puerta a la tabla onvo_test_config.

Aislamiento (pedido de Andres, 09/11): la Tienda de pruebas usa llaves onvo_test_ aunque la app este en produccion, y
ningun cliente real puede terminar en un checkout de prueba. Por eso estas llaves viven en su propia tabla y solo este
modulo las lee; el camino real (services/onvo.py: tienda publica, enlaces de pago de facturas, consulta automatica,
reembolsos) no la conoce y sigue rechazando onvo_test_ en produccion.

- Cifrado: FISCAL_KEYS con AAD «<empresa>:onvo_test:secret|webhook». Sin FISCAL_KEYS no se guarda (503). Solo tokens
  «v1.»: nunca el cifrado heredado (JWT_SECRET) ni ONVO_SECRET_KEY / ONVO_WEBHOOK_SECRET del entorno.
- Solo llaves de modo Test (onvo_test_..._key_) y secreto del webhook firmado (whsec_).
- Antes de cualquier llamada a ONVO se exige una llave de modo Test (adapter()): sin llave, OnvoAdapter.create_checkout
  "simula" en silencio y eso aqui no puede pasar.
- Nunca se devuelve ni se registra una llave ni el whsec_: el estado lleva mascara (prefijo + ultimos 4) y booleanos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import secretbox
from ..models import OnvoTestConfig, Tenant, WebhookEvent
from ..providers.payments import OnvoAdapter, OnvoError, key_mode, key_problem, webhook_format

log = logging.getLogger("crimson.onvo")

ENV = "onvo_test"  # segundo campo de la AAD: distinto de "onvo" (produccion)
PROVIDER = "onvo_test"  # WebhookEvent.provider de los avisos de pruebas (idempotencia y estado aparte de "onvo")
FISCAL_MSG = "El servidor no tiene el llavero de cifrado (FISCAL_KEYS): las llaves de pruebas no se pueden guardar de forma segura"
CROSSED = "El webhook de PRODUCCIÓN apunta a la dirección de pruebas"


@dataclass(repr=False)
class OnvoTestCreds:
    secret: str
    publishable: str | None = None
    webhook: str | None = field(default=None)

    def __repr__(self) -> str:  # nunca las llaves en un log o una traza
        return "OnvoTestCreds(***)"


def _aad(tenant_id: int, fld: str) -> str:
    return secretbox.aad_for(tenant_id, ENV, fld)


def seal(tenant_id: int, fld: str, plain: str) -> str:
    if not secretbox.available():
        raise HTTPException(503, FISCAL_MSG)
    return secretbox.seal_text(plain, _aad(tenant_id, fld))


def _dec(token: str | None, tenant_id: int, fld: str) -> str | None:
    if not token or not token.startswith("v1."):
        return None
    try:
        return secretbox.unseal_text(token, _aad(tenant_id, fld))
    except secretbox.SecretUnavailable:
        log.warning("llave de pruebas de ONVO ilegible (empresa %s, %s): hay que volver a cargarla", tenant_id, fld)
        return None


def config(db: Session, tenant_id: int) -> OnvoTestConfig | None:
    return db.scalar(select(OnvoTestConfig).where(OnvoTestConfig.tenant_id == tenant_id))


def _secret(c: OnvoTestConfig) -> str | None:
    s = _dec(c.secret_enc, c.tenant_id, "secret")
    return s if s and key_mode(s) == "test" and key_problem(s, "secret") is None else None


def _creds_of(c: OnvoTestConfig | None) -> OnvoTestCreds | None:
    if c is None:
        return None
    s = _secret(c)
    if not s:
        return None
    wh = _dec(c.webhook_secret_enc, c.tenant_id, "webhook")
    return OnvoTestCreds(secret=s, publishable=c.publishable_key, webhook=wh if webhook_format(wh) == "firmado" else None)


def creds(db: Session, tenant_id: int) -> OnvoTestCreds | None:
    """Para ABRIR un pago de prueba: llaves cargadas, legibles, de modo Test y la Tienda de pruebas habilitada."""
    c = config(db, tenant_id)
    return _creds_of(c) if c is not None and c.enabled else None


def query_creds(db: Session, tenant_id: int) -> OnvoTestCreds | None:
    """Para CONSULTAR un pago de prueba ya abierto (aunque despues se apagara la Tienda de pruebas)."""
    return _creds_of(config(db, tenant_id))


def webhook_secret(db: Session, tenant_id: int) -> str | None:
    """whsec_ del endpoint de pruebas (solo con la Tienda de pruebas habilitada)."""
    c = creds(db, tenant_id)
    return c.webhook if c else None


def stored_webhook(db: Session, tenant_id: int) -> str | None:
    """whsec_ guardado (este o no habilitada): para impedir que el de produccion y el de pruebas sean el mismo."""
    c = config(db, tenant_id)
    return _dec(c.webhook_secret_enc, c.tenant_id, "webhook") if c else None


def stored_secret(db: Session, tenant_id: int) -> str | None:
    c = config(db, tenant_id)
    return _dec(c.secret_enc, c.tenant_id, "secret") if c else None


def adapter(secret: str | None) -> OnvoAdapter:
    """OnvoAdapter SOLO con una llave de modo Test. Sin ella no se llama a ONVO (ni se simula)."""
    if not secret or key_mode(secret) != "test":
        raise OnvoError(0, "sin llave de pruebas de ONVO")
    return OnvoAdapter(secret)


def webhook_url(t: Tenant) -> str:
    from .onvo import webhook_url as live_url

    return f"{live_url(t)}/pruebas"


def _mask(secret: str | None) -> str | None:
    if not secret or not key_mode(secret):
        return None
    return f"{secret[: secret.index('_key_') + 5]}••••{secret[-4:]}"


def _short(pub: str | None) -> str | None:
    if not pub:
        return None
    return pub if len(pub) <= 24 else f"{pub[: pub.index('_key_') + 5] if '_key_' in pub else pub[:12]}…{pub[-4:]}"


def _last(db: Session, tenant_id: int, ok: bool) -> WebhookEvent | None:
    q = select(WebhookEvent).where(WebhookEvent.tenant_id == tenant_id, WebhookEvent.provider == PROVIDER, WebhookEvent.signature_ok.is_(ok))
    return db.scalar(q.order_by(WebhookEvent.id.desc()).limit(1))


def last_ping(db: Session, tenant_id: int) -> dict | None:
    from ..models import AuditLog

    a = db.scalar(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.action == "onvo_test_ping", AuditLog.entity == "onvo_test")
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    return {"at": a.at, "ok": bool((a.diff or {}).get("ok"))} if a else None


def status(db: Session, t: Tenant) -> dict:
    """Estado de PRUEBAS para Ajustes y la Tienda de pruebas: API y webhook por separado. Nunca una llave ni el whsec_."""
    c = config(db, t.id)
    url = webhook_url(t)
    problems: list[str] = []
    fiscal = secretbox.available()
    if not fiscal:
        problems.append(FISCAL_MSG + ". Revíselo con soporte antes de cargarlas.")
    secret = _dec(c.secret_enc, t.id, "secret") if c else None
    wh = _dec(c.webhook_secret_enc, t.id, "webhook") if c else None
    unreadable = {"secret": bool(c and c.secret_enc) and secret is None, "webhook": bool(c and c.webhook_secret_enc) and wh is None}
    if unreadable["secret"]:
        problems.append("La llave secreta de pruebas guardada ya no se puede leer: vuelva a cargarla.")
    if unreadable["webhook"]:
        problems.append("El secreto del webhook de pruebas guardado ya no se puede leer: cópielo de nuevo desde ONVO (modo Test) y cárguelo aquí.")
    configured = bool(c and _secret(c))
    if configured and not c.enabled:
        problems.append("Las llaves de pruebas están cargadas, pero la Tienda de pruebas está apagada.")
    if configured and not (wh and webhook_format(wh) == "firmado"):
        problems.append("Falta el secreto del webhook de pruebas: el pago solo se confirmará con «Consultar ONVO».")
    if "localhost" in url or "127.0.0.1" in url:
        problems.append("La URL del webhook es local: ONVO no puede llegar a ella. En este equipo el pago se confirma con «Consultar ONVO».")
    ok_ev, crossed = _last(db, t.id, True), _last(db, t.id, False)
    if crossed is not None and (ok_ev is None or crossed.id > ok_ev.id):
        problems.append(f"{CROSSED}: en ONVO, la URL que termina en /pruebas va en el modo Test, y la otra en el modo Live. Corríjalo en ONVO.")
    return {
        "api": {
            "configured": configured,
            "enabled": bool(c and c.enabled),
            "mode": "test" if configured else None,
            "unreadable": unreadable["secret"],
            "secret_mask": _mask(secret),
            "publishable_short": _short(c.publishable_key if c else None),
            "last_ping": last_ping(db, t.id),
        },
        "webhook": {
            "set": bool(wh),
            "format": webhook_format(wh),
            "url": url,
            "unreadable": unreadable["webhook"],
            "last": {"at": ok_ev.received_at, "type": ok_ev.event_type, "result": (ok_ev.result or "")[:160]} if ok_ev else None,
        },
        "fiscal_keys": fiscal,
        "problems": problems,
    }


def record_crossed(db: Session, t: Tenant, typ: str, mode: str | None) -> None:
    """Un aviso firmado con el whsec_ de PRODUCCION llego a la direccion de pruebas: queda la constancia (no se aplica)."""
    db.add(
        WebhookEvent(
            provider=PROVIDER,
            tenant_id=t.id,
            external_id=f"cruzado:{t.id}:{int(datetime.now(UTC).timestamp() * 1000)}"[:160],
            event_type=(typ or "?")[:80],
            signature_ok=False,
            payload={"type": (typ or "")[:80], "mode": mode},
            received_at=datetime.now(UTC),
            result=CROSSED,
        )
    )
    db.commit()


def alert(db: Session, t: Tenant, status_code: int, detail: str) -> bool:
    """Aviso al admin por el webhook de PRUEBAS (cupo diario propio: entity onvo_webhook_test_401|503). Solo si la
    empresa tiene llaves de pruebas. No hace commit."""
    from ..models import EmailOutbox
    from . import email_layout as el
    from .onvo import _cr_now, _queue_admins

    if config(db, t.id) is None:
        return False
    entity = f"onvo_webhook_test_{status_code}"
    hoy = _cr_now().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    if db.scalar(select(EmailOutbox.id).where(EmailOutbox.tenant_id == t.id, EmailOutbox.entity == entity, EmailOutbox.created_at >= hoy).limit(1)) is not None:
        return False
    db.execute(select(Tenant.id).where(Tenant.id == t.id).with_for_update())
    if db.scalar(select(EmailOutbox.id).where(EmailOutbox.tenant_id == t.id, EmailOutbox.entity == entity, EmailOutbox.created_at >= hoy).limit(1)) is not None:
        return False
    titulo = "Tienda de pruebas: " + (CROSSED if status_code == 401 else "ONVO (modo Test) avisó un pago que no se pudo confirmar")
    que = (
        "Llegó a la dirección del webhook de PRUEBAS un aviso firmado con el secreto de PRODUCCIÓN. No se aplicó nada. "
        "En ONVO, la URL que termina en /pruebas va solo en el modo Test (con su propio whsec_)."
        if status_code == 401
        else "ONVO (modo Test) avisó un pago de prueba que no se pudo confirmar consultando a ONVO. ONVO lo reintenta solo; también puede usar «Consultar ONVO» en la Tienda de pruebas."
    )
    cuerpo = el.render(
        t,
        title=titulo,
        body=el.p(que) + el.note(f"Detalle: {detail[:300]}", warn=True),
        preheader=f"{titulo}. Esto es solo del ambiente de pruebas: no hay dinero real.",
        action=(el.panel_url("/ajustes?tab=pagos"), "Revisar las pasarelas en Ajustes"),
        foot="Aviso interno de la Tienda de pruebas (ONVO modo Test). Sale como máximo una vez por día.",
    )
    return _queue_admins(db, t, titulo, cuerpo, entity) > 0
