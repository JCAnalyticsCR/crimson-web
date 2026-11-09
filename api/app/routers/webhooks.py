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

from ..core.config import settings
from ..core.db import get_db
from ..core.ratelimit import webhook_limiter, webhook_test_limiter
from ..models import Tenant, WebhookEvent
from ..providers.payments import OnvoAdapter, OnvoError
from ..services import onvo as onvo_svc
from ..services.onvo import slim

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger("crimson.onvo")
MAX_BODY = 64 * 1024  # un evento de ONVO pesa ~1-3 KB


def _keep(db: Session, ext_id: str, typ: str, payload: dict, result: str, *, tenant_id: int | None = None, provider: str = "onvo") -> None:
    """Registra un evento autenticado NO aplicado con un id propio: la clave real queda libre para el reintento."""
    db.add(
        WebhookEvent(
            provider=provider,
            tenant_id=tenant_id,
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
        # sin evt_ la clave lleva la empresa: UNIQUE(provider, external_id) es global, no por empresa
        ext = (f"test:{evt_id}" if evt_id else f"test:{tenant_id}:{int(datetime.now(UTC).timestamp() * 1000)}")[:160]
        if not db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == "onvo", WebhookEvent.external_id == ext)):
            db.add(WebhookEvent(provider="onvo", tenant_id=tenant_id, external_id=ext, event_type=typ, signature_ok=True, payload={"type": typ, "mode": mode, "formato": fmt},
                                received_at=datetime.now(UTC), processed_at=datetime.now(UTC), result="prueba de conexión de ONVO: recibida"))
            db.commit()
        return {"ok": True, "test": True}
    if not typ or not obj_id:
        raise HTTPException(400, "Evento sin type o sin data.id")
    if settings.is_prod and mode == "test":
        # 09/11: en produccion un evento de modo Test nunca toca pedidos ni facturas reales, aunque la llave live no se
        # pueda leer (current_mode None). Va antes de current_mode y de _keep. Los del formato anterior no traen modo:
        # para ellos la barrera es onvo.is_test_meta en apply_paid y _note_attempt (metadata de la Tienda de pruebas).
        log.info("webhook ONVO %s: evento de modo test en la dirección de producción, ignorado", tenant_id)
        return {"ok": True, "ignored": "evento de pruebas en producción"}
    key_mode_now = onvo_svc.current_mode(db, t)
    if mode and key_mode_now and mode != key_mode_now:
        # un evento de pruebas contra la llave de produccion (o al reves): 200 para que ONVO no reintente, sin aplicar nada
        _keep(db, f"{evt_id or typ}:{obj_id}", typ, {"type": typ, "mode": mode}, f"evento de modo {mode} ignorado: la llave guardada es {key_mode_now}", tenant_id=tenant_id)
        return {"ok": True, "ignored": "modo distinto"}
    ext_id = (f"evt:{evt_id}" if evt_id else f"{typ}:{obj_id}")[:160]
    stored = {"type": typ[:80], "data": slim(data)}  # sin datos personales del comprador
    seen = db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == "onvo", WebhookEvent.external_id == ext_id, WebhookEvent.signature_ok))
    if seen:
        return {"ok": True, "duplicate": True}  # idempotente: siempre 200
    ev = WebhookEvent(provider="onvo", tenant_id=tenant_id, external_id=ext_id, event_type=typ[:80], signature_ok=True, payload=stored, received_at=datetime.now(UTC))
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
        _keep(db, ext_id, typ, stored, f"no se pudo confirmar con ONVO: {e.message}", tenant_id=tenant_id)
        log.warning("webhook ONVO %s sin confirmar: %s", ext_id, e.message)
        _alert(db, t, 503, f"{typ} {obj_id}: {e.message}")
        raise HTTPException(503, "No se pudo confirmar el evento con ONVO; reintentar") from None
    ev.processed_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- webhook de PRUEBAS (Tienda de pruebas, 09/11)
@router.post("/onvo/{tenant_id}/pruebas")
async def onvo_pruebas(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    legacy: str | None = Header(None, alias="X-Webhook-Secret"),
    signature: str | None = Header(None, alias="X-Onvo-Signature"),
):
    """Endpoint de ONVO en modo Test (su propio whsec_). Solo formato firmado, solo eventos de modo test, y limitador,
    provider ("onvo_test") e idempotencia propios: nada de esto toca pedidos, facturas ni el estado de produccion."""
    webhook_test_limiter.hit(f"onvo-test:{tenant_id}")
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "Evento demasiado grande")
    body = await request.body()  # cuerpo crudo: la firma se verifica sobre estos bytes, antes de json.loads
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Evento demasiado grande")
    return await run_in_threadpool(_process_pruebas, db, tenant_id, legacy, body, signature)


def _test_alert(db: Session, t: Tenant, status: int, detail: str) -> None:
    from ..services import onvo_test

    try:
        if onvo_test.alert(db, t, status, detail):
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("no se pudo avisar el %s del webhook de pruebas (empresa %s)", status, t.id)


def _peek(body: bytes) -> tuple[str, str | None]:
    try:
        raw = json.loads(body)
    except ValueError:
        return "", None
    payload, _evt, mode = onvo_svc.normalize_event(raw if isinstance(raw, dict) else {})
    return str(payload.get("type") or ""), mode


def _process_pruebas(db: Session, tenant_id: int, legacy: str | None, body: bytes, signature: str | None) -> dict:
    from ..services import onvo_test, test_shop

    t = db.get(Tenant, tenant_id)
    wh = onvo_test.webhook_secret(db, tenant_id) if t is not None else None
    if t is None or not wh or not signature:
        # sin empresa, sin Tienda de pruebas habilitada, sin whsec_ o con el formato anterior (X-Webhook-Secret): 401
        log.warning("webhook de pruebas ONVO rechazado (%s): %s", tenant_id, "sin firma X-Onvo-Signature" if not signature else "sin secreto de pruebas")
        raise HTTPException(401, "Secreto o firma del webhook invalido")
    if not OnvoAdapter.verify_signature(wh, signature, body):
        live = onvo_svc.webhook_secret(db, tenant_id)
        if live and OnvoAdapter.verify_signature(live, signature, body):
            # solo diagnostico: ONVO Live apunta a la direccion de pruebas. No se aplica nada; constancia y aviso
            typ, mode = _peek(body)
            onvo_test.record_crossed(db, t, typ, mode)
            _test_alert(db, t, 401, onvo_test.CROSSED)
        log.warning("webhook de pruebas ONVO rechazado (%s): firma invalida o vencida", tenant_id)
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
    now = datetime.now(UTC)
    if typ == "webhook_endpoint.test":
        ext = (f"prueba:{evt_id}" if evt_id else f"prueba:{tenant_id}:{int(now.timestamp() * 1000)}")[:160]
        if not db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == onvo_test.PROVIDER, WebhookEvent.external_id == ext)):
            db.add(
                WebhookEvent(
                    provider=onvo_test.PROVIDER,
                    tenant_id=tenant_id,
                    external_id=ext,
                    event_type=typ,
                    signature_ok=True,
                    payload={"type": typ, "mode": mode},
                    received_at=now,
                    processed_at=now,
                    result="prueba de conexión de ONVO (modo Test): recibida",
                )
            )
            db.commit()
        return {"ok": True, "test": True}
    if mode != "test":  # incluido sin modo: esta direccion es solo del modo Test
        _keep(
            db,
            f"{tenant_id}:{evt_id or typ}:{obj_id}",
            typ,
            {"type": typ, "mode": mode},
            f"evento de modo {mode or 'desconocido'} ignorado: esta dirección es solo de pruebas",
            tenant_id=tenant_id,
            provider=onvo_test.PROVIDER,
        )
        return {"ok": True, "ignored": "modo distinto"}
    if not typ or not obj_id:
        raise HTTPException(400, "Evento sin type o sin data.id")
    ext_id = (f"evt:{evt_id}" if evt_id else f"{tenant_id}:{typ}:{obj_id}")[:160]
    stored = {"type": typ[:80], "data": slim(data)}
    if db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == onvo_test.PROVIDER, WebhookEvent.external_id == ext_id, WebhookEvent.signature_ok)):
        test_shop.note_duplicate(db, t, payload, evt_id)
        return {"ok": True, "duplicate": True}
    ev = WebhookEvent(provider=onvo_test.PROVIDER, tenant_id=tenant_id, external_id=ext_id, event_type=typ[:80], signature_ok=True, payload=stored, received_at=now)
    db.add(ev)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    try:
        ev.result = test_shop.handle_event(db, t, payload, evt_id=evt_id, mode=mode)[:2000]
    except OnvoError as e:
        db.rollback()
        _keep(db, ext_id, typ, stored, f"no se pudo confirmar con ONVO (modo Test): {e.message}", tenant_id=tenant_id, provider=onvo_test.PROVIDER)
        log.warning("webhook de pruebas ONVO %s sin confirmar: %s", ext_id, e.message)
        _test_alert(db, t, 503, f"{typ} {obj_id}: {e.message}")
        raise HTTPException(503, "No se pudo confirmar el evento con ONVO; reintentar") from None
    ev.processed_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}
