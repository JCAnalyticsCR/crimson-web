"""Ingreso de webhooks de ONVO (plan 5.3).

Dos formatos de ONVO, que conviven (docs.onvopay.com/webhooks y /webhooks/legacy); cada endpoint usa uno:
- firmado (el que ONVO crea hoy): header X-Onvo-Signature "t=..,v1=.." (HMAC-SHA256 de "t.cuerpo" con el secreto
  whsec_), sobre {"id": "evt_..", "type": "payment_intent.succeeded", "mode", "data": {"object": {...}}}. Idempotencia
  por el id del evento. La prueba del panel ("Probar conexion") es webhook_endpoint.test y espera un 2xx.
- anterior: header X-Webhook-Secret (el secreto webhook_secret_ tal cual), {"type": "payment-intent.succeeded",
  "data": {...}}, sin id de evento: la clave de idempotencia es "<type>:<data.id>".
09/11: Crimson solo entendia el anterior; el endpoint que creo Andres es firmado y ONVO recibia 401. Cada evento autenticado queda guardado; un pago solo se
aplica despues de confirmarlo con la API de ONVO (services/onvo.handle_event).

Respuestas: 2xx solo si el evento quedo procesado (ONVO cuenta cualquier otro estado como entrega fallida). Lo que no
se pudo confirmar se registra con otro id, sin consumir la clave, para que el reintento se aplique. Sin el secreto
correcto no se aplica nada (cualquiera puede llamar a esta URL) y la respuesta es 401 exista o no la empresa.

Un 401 con forma de ONVO (secreto webhook_secret_... y un evento de ONVO, empresa con ONVO conectado) o un 503 avisan
al admin, como maximo una vez por tipo y por dia (services/onvo.webhook_alert; el correo se encola, no se entrega aqui):
con el webhook caido, los pagos con tarjeta solo se confirman al volver el comprador o en la consulta automatica.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..core.db import get_db
from ..core.ratelimit import webhook_limiter
from ..models import Tenant, WebhookEvent
from ..providers.payments import OnvoAdapter, OnvoError
from ..services import onvo as onvo_svc
from ..services.onvo import slim

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger("crimson.onvo")
MAX_BODY = 64 * 1024  # un evento de ONVO pesa ~1-3 KB


def _keep(db: Session, ext_id: str, typ: str, payload: dict, result: str) -> None:
    """Registra un evento autenticado NO aplicado con un id propio: la clave real queda libre para el reintento."""
    db.add(
        WebhookEvent(
            provider="onvo",
            external_id=f"{ext_id}#{int(datetime.now(UTC).timestamp() * 1000)}"[:160],
            event_type=typ[:80],
            signature_ok=True,
            payload=payload,
            received_at=datetime.now(UTC),
            result=result,
        )
    )
    db.commit()


@router.post("/onvo/{tenant_id}")
async def onvo(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    secret: str | None = Header(None, alias="X-Webhook-Secret"),
    signature: str | None = Header(None, alias="X-Onvo-Signature"),
):
    webhook_limiter.hit(f"onvo:{tenant_id}")
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "Evento demasiado grande")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Evento demasiado grande")
    # lo demas usa la base y llama a ONVO (HTTP sincrono): fuera del loop de eventos
    return await run_in_threadpool(_process, db, tenant_id, secret, body, signature)


def _alert(db: Session, t: Tenant, status: int, detail: str) -> None:
    """Aviso al admin (uno por tipo y por dia, services/onvo.webhook_alert). Nunca cambia la respuesta a ONVO."""
    try:
        if onvo_svc.webhook_alert(db, t, status, detail):
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("no se pudo avisar el %s del webhook ONVO (empresa %s)", status, t.id)


def _authentic(db: Session, t: Tenant | None, tenant_id: int, secret: str | None, signature: str | None, body: bytes) -> str | None:
    """«firmado» | «anterior» si el aviso es de ONVO con el secreto guardado; None si no."""
    if t is None:
        return None
    stored = onvo_svc.webhook_secret(db, tenant_id)
    if signature:  # el formato se decide por el header que trae, no por lo guardado
        return "firmado" if OnvoAdapter.verify_signature(stored, signature, body) else None
    return "anterior" if OnvoAdapter.verify_webhook(stored, secret) else None


def _process(db: Session, tenant_id: int, secret: str | None, body: bytes, signature: str | None = None) -> dict:
    t = db.get(Tenant, tenant_id)
    fmt = _authentic(db, t, tenant_id, secret, signature, body)
    if fmt is None:
        log.warning("webhook ONVO rechazado (%s): %s", tenant_id, "firma X-Onvo-Signature invalida o vencida" if signature else "secreto X-Webhook-Secret invalido o ausente")
        if t is not None and onvo_svc.onvo_shaped(secret, body, signature):
            # ONVO con otro secreto = pagos sin confirmar por webhook: el admin se entera (una sonda cualquiera no avisa)
            _alert(db, t, 401, "la firma del webhook no coincide" if signature else "secreto del webhook distinto")
        raise HTTPException(401, "Secreto o firma del webhook invalido")
    try:
        raw = json.loads(body)
    except ValueError as e:
        raise HTTPException(400, "JSON invalido") from e
    if not isinstance(raw, dict):
        raise HTTPException(400, "JSON invalido")
    payload, evt_id, mode = onvo_svc.normalize_event(raw)
    typ = str(payload.get("type") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    obj_id = str(data.get("id") or "")
    if typ == "webhook_endpoint.test":
        # «Probar conexion» del panel de ONVO: 2xx, sin tocar ningun pago; queda la constancia (estado del webhook)
        ext = f"test:{evt_id or int(datetime.now(UTC).timestamp() * 1000)}"[:160]
        if not db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == "onvo", WebhookEvent.external_id == ext)):
            db.add(WebhookEvent(provider="onvo", external_id=ext, event_type=typ, signature_ok=True, payload={"type": typ, "mode": mode, "formato": fmt},
                                received_at=datetime.now(UTC), processed_at=datetime.now(UTC), result="prueba de conexión de ONVO: recibida"))
            db.commit()
        return {"ok": True, "test": True}
    if not typ or not obj_id:
        raise HTTPException(400, "Evento sin type o sin data.id")
    key_mode_now = onvo_svc.current_mode(db, t)
    if mode and key_mode_now and mode != key_mode_now:
        # un evento de pruebas contra la llave de produccion (o al reves): 200 para que ONVO no reintente, sin aplicar nada
        _keep(db, f"{evt_id or typ}:{obj_id}", typ, {"type": typ, "mode": mode}, f"evento de modo {mode} ignorado: la llave guardada es {key_mode_now}")
        return {"ok": True, "ignored": "modo distinto"}
    ext_id = (f"evt:{evt_id}" if evt_id else f"{typ}:{obj_id}")[:160]
    stored = {"type": typ[:80], "data": slim(data)}  # sin datos personales del comprador
    seen = db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == "onvo", WebhookEvent.external_id == ext_id, WebhookEvent.signature_ok))
    if seen:
        return {"ok": True, "duplicate": True}  # idempotente: siempre 200
    ev = WebhookEvent(provider="onvo", external_id=ext_id, event_type=typ[:80], signature_ok=True, payload=stored, received_at=datetime.now(UTC))
    db.add(ev)
    try:
        db.flush()
    except IntegrityError:  # el mismo evento entro dos veces a la vez: el otro lo procesa
        db.rollback()
        return {"ok": True, "duplicate": True}
    try:
        ev.result = onvo_svc.handle_event(db, t, payload)[:2000]
    except OnvoError as e:
        db.rollback()  # nada a medias: ni el evento ni cambios en pedidos/facturas
        _keep(db, ext_id, typ, stored, f"no se pudo confirmar con ONVO: {e.message}")
        log.warning("webhook ONVO %s sin confirmar: %s", ext_id, e.message)
        _alert(db, t, 503, f"{typ} {obj_id}: {e.message}")
        raise HTTPException(503, "No se pudo confirmar el evento con ONVO; reintentar") from None
    ev.processed_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
