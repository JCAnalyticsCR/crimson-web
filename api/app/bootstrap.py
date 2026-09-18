"""Arranque inicial de un entorno compartido (staging/produccion) sin contrasenas en variables ni en el chat.

Corre en cada arranque del contenedor, despues de `alembic upgrade head`:

    python -m app.bootstrap

- Si BOOTSTRAP_ADMIN_EMAIL no esta definido, no hace nada.
- Si ya existe algun usuario, no hace nada (el entorno ya fue reclamado).
- Si no hay usuarios: crea los datos base (y demo si BOOTSTRAP_DEMO=true), invalida invitaciones
  pendientes previas y emite UNA invitacion de administrador. El enlace sale en el log del servicio;
  la persona elige su propia contrasena al aceptarla y a partir de ahi este script queda inerte.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .core.config import settings
from .core.db import SessionLocal
from .core.security import hash_token
from .models import Invitation, User
from .seeds import seed_base

INVITE_HOURS = 72


def run(db: Session, admin_email: str | None, demo: bool = False, base_url: str | None = None) -> str | None:
    """Devuelve el enlace de invitacion emitido, o None si no correspondia hacer nada."""
    if not admin_email:
        return None
    if db.scalar(select(func.count()).select_from(User)):
        return None
    t = seed_base(db, demo=demo)
    now = datetime.now(UTC)
    for old in db.scalars(select(Invitation).where(Invitation.tenant_id == t.id, Invitation.accepted_at.is_(None))):
        old.expires_at = now  # un solo enlace vivo a la vez
    raw = secrets.token_urlsafe(32)
    db.add(
        Invitation(
            tenant_id=t.id,
            email=admin_email.strip().lower(),
            role_code="admin",
            token_hash=hash_token(raw),
            expires_at=now + timedelta(hours=INVITE_HOURS),
            invited_by=None,
        )
    )
    db.commit()
    return f"{(base_url or settings.public_base_url).rstrip('/')}/invitacion/{raw}"


def main() -> None:
    with SessionLocal() as db:
        link = run(db, settings.bootstrap_admin_email, demo=settings.bootstrap_demo)
    if link:
        print(f"[bootstrap] Invitacion de administrador para {settings.bootstrap_admin_email} (vence en {INVITE_HOURS} h, un solo uso):", flush=True)
        print(f"[bootstrap] {link}", flush=True)
    else:
        print("[bootstrap] Nada que hacer (sin BOOTSTRAP_ADMIN_EMAIL o el entorno ya tiene usuarios).", flush=True)


if __name__ == "__main__":
    main()
