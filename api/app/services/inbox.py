"""Bandeja de entrada de XML (IMAP): lee correos no leidos, toma los adjuntos XML de facturas de proveedores y los
registra en Recepcion. La contrasena del buzon se guarda cifrada; se recomienda un buzon dedicado
(p. ej. facturas@empresa) con contrasena de aplicacion."""

from __future__ import annotations

import email
import imaplib
from email.message import Message

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core.crypto import decrypt
from ..models import Tenant

MAX_MESSAGES = 40
MAX_ATTACHMENT = 2 * 1024 * 1024


def config(t: Tenant) -> dict:
    return {
        "enabled": False,
        "host": "",
        "port": 993,
        "user": "",
        "folder": "INBOX",
        "password_enc": None,
        "last_run": None,
        "last_result": None,
        **((t.settings or {}).get("inbox") or {}),
    }


def _xml_parts(msg: Message) -> list[str]:
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        name = (part.get_filename() or "").lower()
        ctype = part.get_content_type()
        if not (name.endswith(".xml") or ctype in ("text/xml", "application/xml")):
            continue
        payload = part.get_payload(decode=True) or b""
        if 0 < len(payload) <= MAX_ATTACHMENT:
            out.append(payload.decode("utf-8", errors="replace"))
    return out


def poll(db: Session, t: Tenant, connect=None) -> dict:
    """Procesa hasta MAX_MESSAGES correos no leidos. `connect` permite inyectar un cliente IMAP en pruebas."""
    from datetime import UTC, datetime

    from ..routers.reception import receive_xml

    cfg = config(t)
    res = {"messages": 0, "nuevos": 0, "duplicados": 0, "ignorados": 0, "errores": []}
    if not cfg["host"] or not cfg["user"] or not cfg["password_enc"]:
        raise ValueError("Bandeja sin configurar (servidor, usuario y contraseña)")
    client = (connect or (lambda: imaplib.IMAP4_SSL(cfg["host"], int(cfg["port"]), timeout=25)))()
    try:
        client.login(cfg["user"], decrypt(cfg["password_enc"]))
        client.select(cfg["folder"] or "INBOX")
        typ, data = client.search(None, "UNSEEN")
        ids = (data[0] or b"").split()[:MAX_MESSAGES] if typ == "OK" else []
        for mid in ids:
            typ, parts = client.fetch(mid, "(RFC822)")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            res["messages"] += 1
            msg = email.message_from_bytes(parts[0][1])
            for xml in _xml_parts(msg):
                try:
                    status, _ = receive_xml(db, t.id, xml)
                    res[{"nuevo": "nuevos", "duplicado": "duplicados"}.get(status, "ignorados")] += 1
                except Exception as e:  # noqa: BLE001 - un XML malo no detiene la bandeja
                    res["errores"].append(f"{msg.get('Subject', '')[:60]}: {getattr(e, 'detail', e)}")
            client.store(mid, "+FLAGS", "\\Seen")
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass
    st = dict(t.settings or {})
    st["inbox"] = {**cfg, "last_run": datetime.now(UTC).isoformat(), "last_result": {k: (v if k != "errores" else v[:5]) for k, v in res.items()}}
    t.settings = st
    flag_modified(t, "settings")
    db.commit()
    return res
