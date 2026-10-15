"""Buzon de recepcion (IMAP): lee los correos nuevos por UID y registra sus adjuntos (XML, PDF, zip) en Recepcion.

- Nunca marca nada en el servidor: UIDVALIDITY + UID con BODY.PEEK[] (abrir el buzon en Outlook ya no esconde correos).
- Un commit por correo: un correo malo no detiene la corrida y nunca se pierde (queda en "error" con su .eml para
  "Reprocesar").
- Arriendo (locked_until) en reception_mailbox: dos lectores (worker y "Revisar ahora") no se pisan. Un SELECT FOR
  UPDATE no sirve: se soltaria en el primer commit por correo.
- Privacidad: el buzon tambien recibe boletines y correos personales. El .eml crudo solo se guarda si algo fallo; de un
  correo sin comprobantes queda fecha, tamano, sha256, remitente y asunto.
- IMAP4_SSL con ssl.create_default_context() (verifica el certificado del servidor).

Configuracion: variables RECEPTION_IMAP_* (manda) o Ajustes -> XML de proveedores. Sin ninguna, el buzon esta apagado.
"""

from __future__ import annotations

import email
import email.header
import hashlib
import imaplib
import logging
import os
import re
import socket
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.message import Message
from email.utils import parsedate_to_datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core import secretbox
from ..core.config import settings
from ..core.crypto import decrypt, encrypt
from ..models import ReceptionMailbox, ReceptionMessage, Tenant

log = logging.getLogger("crimson.inbox")
MAX_MESSAGES = 40
MAX_EML = 25 * 1024 * 1024
LEASE = timedelta(minutes=10)
CANDIDATE_EXT = (".xml", ".pdf", ".zip")
CANDIDATE_TYPES = ("text/xml", "application/xml", "application/pdf", "application/zip", "application/x-zip-compressed", "application/octet-stream")
IMAP_AAD = "imap:password"


class MailboxBusy(Exception):
    """Otro proceso esta revisando el buzon (arriendo vigente)."""


@dataclass(repr=False)
class MailboxConfig:
    source: str  # env | ajustes
    host: str
    port: int
    user: str
    password: str
    folder: str

    @property
    def key(self) -> str:
        return f"{'env' if self.source == 'env' else 'db'}:{self.user}@{self.host}/{self.folder}"[:200]

    @property
    def label(self) -> str:
        return f"{self.user}@{self.host}/{self.folder}"

    def __repr__(self) -> str:
        return f"MailboxConfig(source={self.source!r}, mailbox={self.label!r})"


def config(t: Tenant) -> dict:
    """Configuracion guardada en Ajustes (pestana XML de proveedores)."""
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


def imap_aad(tenant_id: int) -> str:
    return f"{tenant_id}:{IMAP_AAD}"


def encrypt_password(tenant_id: int, raw: str) -> str:
    """Con FISCAL_KEYS: secretbox (AES-GCM). Sin ella se sigue guardando con el cifrado heredado (no se rompe la pestana)."""
    if secretbox.available():
        return secretbox.seal_text(raw, imap_aad(tenant_id))
    return encrypt(raw)


def decrypt_password(tenant_id: int, enc: str) -> str:
    if enc.startswith("v1."):
        return secretbox.unseal_text(enc, imap_aad(tenant_id))
    return decrypt(enc)


def env_applies(db: Session, tenant: Tenant) -> bool:
    if not (settings.reception_imap_host and settings.reception_imap_user and settings.reception_imap_password and settings.reception_imap_password.get_secret_value()):
        return False
    from .hacienda_config import env_tenant_applies

    return env_tenant_applies(db, tenant, settings.reception_imap_tenant)


def mailbox_config(db: Session, tenant: Tenant) -> MailboxConfig | None:
    if env_applies(db, tenant):
        return MailboxConfig(
            "env",
            settings.reception_imap_host,
            int(settings.reception_imap_port),
            settings.reception_imap_user,
            settings.reception_imap_password.get_secret_value(),
            settings.reception_imap_folder or "INBOX",
        )
    c = config(tenant)
    if c.get("enabled") and c.get("host") and c.get("user") and c.get("password_enc"):
        try:
            pwd = decrypt_password(tenant.id, c["password_enc"])
        except Exception:  # noqa: BLE001 - llave rotada o perdida: el buzon queda apagado con un aviso
            log.warning("buzon de %s: la contraseña guardada no se puede descifrar", tenant.slug)
            return None
        return MailboxConfig("ajustes", c["host"], int(c.get("port") or 993), c["user"], pwd, c.get("folder") or "INBOX")
    return None


def source_of(db: Session, tenant: Tenant) -> str:
    if env_applies(db, tenant):
        return "env"
    c = config(tenant)
    return "ajustes" if c.get("enabled") and c.get("host") and c.get("user") and c.get("password_enc") else "ninguno"


def status(db: Session, tenant: Tenant) -> dict:
    src = source_of(db, tenant)
    label = None
    if src == "env":
        label = f"{settings.reception_imap_user}@{settings.reception_imap_host}/{settings.reception_imap_folder or 'INBOX'}"
    elif src == "ajustes":
        c = config(tenant)
        label = f"{c['user']}@{c['host']}/{c.get('folder') or 'INBOX'}"
    mbox = None
    if label:
        key = f"{'env' if src == 'env' else 'db'}:{label}"[:200]
        mbox = db.scalar(select(ReceptionMailbox).where(ReceptionMailbox.tenant_id == tenant.id, ReceptionMailbox.key == key))
    return {
        "source": src,
        "mailbox": label,
        "enabled": src != "ninguno",
        "last_run_at": mbox.last_run_at if mbox else (config(tenant).get("last_run") if src == "ajustes" else None),
        "last_result": mbox.last_result if mbox else (config(tenant).get("last_result") if src == "ajustes" else None),
    }


def candidate_parts(msg: Message) -> list[tuple[str, bytes]]:
    """Adjuntos candidatos (recorre tambien los reenviados message/rfc822)."""
    out = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_maintype() == "multipart":
            continue
        name = part.get_filename() or ""
        ctype = (part.get_content_type() or "").lower()
        if not (name.lower().endswith(CANDIDATE_EXT) or ctype in CANDIDATE_TYPES):
            continue
        payload = part.get_payload(decode=True) or b""
        if payload:
            out.append((name or f"adjunto.{ctype.rsplit('/', 1)[-1]}", payload))
    return out


def _mailbox(db: Session, tenant: Tenant, cfg: MailboxConfig) -> ReceptionMailbox:
    m = db.scalar(select(ReceptionMailbox).where(ReceptionMailbox.tenant_id == tenant.id, ReceptionMailbox.key == cfg.key))
    if m is None:
        m = ReceptionMailbox(tenant_id=tenant.id, key=cfg.key, last_uid=0)
        db.add(m)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            m = db.scalar(select(ReceptionMailbox).where(ReceptionMailbox.tenant_id == tenant.id, ReceptionMailbox.key == cfg.key))
    return m


def _lease(db: Session, mbox: ReceptionMailbox, holder: str) -> None:
    now = datetime.now(UTC)
    res = db.execute(
        update(ReceptionMailbox)
        .where(ReceptionMailbox.id == mbox.id, or_(ReceptionMailbox.locked_until.is_(None), ReceptionMailbox.locked_until < now))
        .values(locked_until=now + LEASE, locked_by=holder)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if res.rowcount == 0:
        raise MailboxBusy("Ya se está revisando el buzón")
    db.refresh(mbox)


def _renew(db: Session, mbox: ReceptionMailbox, holder: str) -> None:
    db.execute(
        update(ReceptionMailbox)
        .where(ReceptionMailbox.id == mbox.id, ReceptionMailbox.locked_by == holder)
        .values(locked_until=datetime.now(UTC) + LEASE)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _release(db: Session, mbox_id: int, holder: str) -> None:
    try:
        db.rollback()
        db.execute(
            update(ReceptionMailbox)
            .where(ReceptionMailbox.id == mbox_id, ReceptionMailbox.locked_by == holder)
            .values(locked_until=None, locked_by=None)
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:  # noqa: BLE001 - el arriendo vence solo a los 10 min
        db.rollback()


def _ok(resp) -> list:
    typ, data = resp
    if typ != "OK":
        raise imaplib.IMAP4.error(f"IMAP respondió {typ}")
    return data or []


def _uidvalidity(client, folder: str) -> int | None:
    try:
        data = _ok(client.status(folder, "(UIDVALIDITY)"))
    except Exception:  # noqa: BLE001
        return None
    m = re.search(rb"UIDVALIDITY (\d+)", b" ".join(x if isinstance(x, bytes) else str(x).encode() for x in data))
    return int(m.group(1)) if m else None


def _uids(data: list) -> list[int]:
    raw = b" ".join(x for x in data if isinstance(x, bytes))
    return sorted({int(x) for x in raw.split() if x.isdigit()})


def _size(data: list) -> int | None:
    for x in data:
        blob = x[0] if isinstance(x, tuple) else x
        if isinstance(blob, bytes):
            m = re.search(rb"RFC822\.SIZE (\d+)", blob)
            if m:
                return int(m.group(1))
    return None


def _body(data: list) -> bytes | None:
    for x in data:
        if isinstance(x, tuple) and len(x) >= 2 and isinstance(x[1], bytes):
            return x[1]
    return None


def _header(msg: Message, name: str) -> str | None:
    v = msg.get(name)
    if v is None:
        return None
    try:
        return str(email.header.make_header(email.header.decode_header(str(v))))[:300]
    except Exception:  # noqa: BLE001
        return str(v)[:300]


def _date(msg: Message) -> datetime | None:
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:  # noqa: BLE001
        return None


def _connect(cfg: MailboxConfig):
    return imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ssl.create_default_context(), timeout=25)


def process_raw(db: Session, tenant: Tenant, raw: bytes, *, mailbox_id: int | None, uid: int | None, uidvalidity: int | None, user_id: int | None = None, message: ReceptionMessage | None = None) -> tuple[ReceptionMessage, dict]:
    """Procesa un .eml (del buzon o de "Reprocesar"). No hace commit."""
    from .reception_intake import ingest_files, new_result, status_of

    msg = email.message_from_bytes(raw)
    rm = message or ReceptionMessage(
        tenant_id=tenant.id,
        mailbox_id=mailbox_id,
        source="imap",
        uid=uid,
        uidvalidity=uidvalidity,
        message_id=_header(msg, "Message-ID"),
        sender=_header(msg, "From"),
        subject=_header(msg, "Subject"),
        received_at=_date(msg),
        size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        created_by=user_id,
    )
    if message is None:
        db.add(rm)
        db.flush()
    files = candidate_parts(msg)
    if not files:
        rm.status, rm.summary, rm.raw_eml, rm.error = "sin_comprobantes", [], None, None
        return rm, new_result()
    res = ingest_files(db, tenant, files, source="imap", message=rm, user_id=user_id)
    rm.status = status_of(res)
    rm.error = "; ".join(e["error"] for e in res["errores"])[:500] or None
    rm.raw_eml = raw if rm.status in ("error", "parcial") and len(raw) <= MAX_EML else None
    return rm, res


def poll(db: Session, t: Tenant, connect=None) -> dict:
    """Revisa el buzon. Devuelve {"messages", "nuevos", "duplicados", "adjuntos", "ignorados", "errores"}.
    `connect` permite inyectar un cliente IMAP en pruebas. Lanza ValueError sin configuracion y MailboxBusy si otro
    proceso lo esta revisando."""
    cfg = mailbox_config(db, t)
    if cfg is None:
        raise ValueError("Bandeja sin configurar (servidor, usuario y contraseña)")
    mbox = _mailbox(db, t, cfg)
    holder = f"{socket.gethostname()[:30]}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    _lease(db, mbox, holder)
    mbox_id = mbox.id
    totals = {"messages": 0, "nuevos": 0, "duplicados": 0, "adjuntos": 0, "ignorados": 0, "errores": []}
    client = None
    try:
        client = (connect or (lambda: _connect(cfg)))()
        _ok(client.login(cfg.user, cfg.password))
        _ok(client.select(cfg.folder or "INBOX"))
        uv = _uidvalidity(client, cfg.folder or "INBOX")
        if uv is not None and uv != mbox.uidvalidity:
            mbox.uidvalidity, mbox.last_uid = uv, 0
            db.commit()
        last = int(mbox.last_uid or 0)
        if last == 0:
            since = (date.today() - timedelta(days=int(settings.reception_imap_since_days or 30))).strftime("%d-%b-%Y")
            uids = _uids(_ok(client.uid("SEARCH", None, f"SINCE {since}")))
        else:
            uids = [u for u in _uids(_ok(client.uid("SEARCH", None, f"UID {last + 1}:*"))) if u > last]
        for i, uid in enumerate(uids[:MAX_MESSAGES]):
            if i and i % 10 == 0:
                _renew(db, mbox, holder)
            size = _size(_ok(client.uid("FETCH", str(uid), "(RFC822.SIZE)")))
            if size and size > MAX_EML:
                db.add(
                    ReceptionMessage(
                        tenant_id=t.id, mailbox_id=mbox_id, source="imap", uid=uid, uidvalidity=mbox.uidvalidity, size=size,
                        sha256=f"sin-cuerpo-{mbox.uidvalidity}-{uid}"[:64], status="error", error=f"Correo de {size // (1024 * 1024)} MB: supera 25 MB", summary=[],
                    )
                )
                mbox.last_uid = uid
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    db.execute(update(ReceptionMailbox).where(ReceptionMailbox.id == mbox_id).values(last_uid=uid))
                    db.commit()
                totals["messages"] += 1
                totals["errores"].append(f"UID {uid}: correo de más de 25 MB")
                continue
            raw = _body(_ok(client.uid("FETCH", str(uid), "(BODY.PEEK[])"))) or b""
            sha = hashlib.sha256(raw).hexdigest()
            # §6.4 paso 4: el mismo correo (mismo sha256) ya registrado en CUALQUIER buzon de la empresa -> solo avanzar.
            # Cubre la UIDVALIDITY nueva y el paso del buzon de Ajustes a variables de entorno (otra fila de
            # reception_mailbox con last_uid = 0 que vuelve a bajar los ultimos N dias).
            seen = db.scalar(select(ReceptionMessage.id).where(ReceptionMessage.tenant_id == t.id, ReceptionMessage.sha256 == sha).limit(1))
            if seen:
                mbox.last_uid = uid
                db.commit()
                continue
            try:
                rm, res = process_raw(db, t, raw, mailbox_id=mbox_id, uid=uid, uidvalidity=mbox.uidvalidity)
                mbox.last_uid = uid
                db.commit()
                totals["messages"] += 1
                totals["nuevos"] += len(res["nuevos"])
                totals["duplicados"] += len(res["duplicados"])
                totals["adjuntos"] += len(res["adjuntos"])
                totals["ignorados"] += len(res["ignorados"])
                totals["errores"] += [f"{(rm.subject or '')[:60]}: {e['error']}" for e in res["errores"]]
            except Exception as e:  # noqa: BLE001 - un correo malo no detiene la corrida
                db.rollback()
                if isinstance(e, IntegrityError) and "uq_reception_message_uid" in str(e.orig if hasattr(e, "orig") else e) or _uid_taken(db, t.id, mbox_id, mbox.uidvalidity, uid):
                    db.execute(update(ReceptionMailbox).where(ReceptionMailbox.id == mbox_id).values(last_uid=uid))
                    db.commit()
                    db.refresh(mbox)
                    continue
                try:
                    db.add(
                        ReceptionMessage(
                            tenant_id=t.id, mailbox_id=mbox_id, source="imap", uid=uid, uidvalidity=mbox.uidvalidity, size=len(raw), sha256=sha,
                            raw_eml=raw if len(raw) <= MAX_EML else None, status="error", error=f"{type(e).__name__}: {e}"[:500], summary=[],
                        )
                    )
                    db.execute(update(ReceptionMailbox).where(ReceptionMailbox.id == mbox_id).values(last_uid=uid))
                    db.commit()
                    db.refresh(mbox)
                    totals["messages"] += 1
                    totals["errores"].append(f"UID {uid}: {type(e).__name__}")
                except Exception:  # noqa: BLE001 - base caida: se corta SIN avanzar (la proxima corrida lo reintenta)
                    db.rollback()
                    log.warning("buzon %s: no se pudo registrar el error del UID %s; se corta la corrida", t.slug, uid)
                    break
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass
        _release(db, mbox_id, holder)
    summary = {k: (v if k != "errores" else v[:5]) for k, v in totals.items()}
    mbox = db.get(ReceptionMailbox, mbox_id)
    mbox.last_run_at, mbox.last_result = datetime.now(UTC), summary
    if cfg.source == "ajustes":
        st = dict(t.settings or {})
        st["inbox"] = {**config(t), "last_run": datetime.now(UTC).isoformat(), "last_result": summary}
        t.settings = st
        flag_modified(t, "settings")
    db.commit()
    return totals


def _uid_taken(db: Session, tenant_id: int, mailbox_id: int, uidvalidity, uid: int) -> bool:
    return bool(
        db.scalar(
            select(ReceptionMessage.id).where(
                ReceptionMessage.tenant_id == tenant_id, ReceptionMessage.mailbox_id == mailbox_id, ReceptionMessage.uidvalidity == uidvalidity, ReceptionMessage.uid == uid
            )
        )
    )
