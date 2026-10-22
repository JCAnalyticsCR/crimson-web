"""Vuelve a cifrar con la llave principal de FISCAL_KEYS toda credencial fiscal (y contrasena de buzon) cifrada con otra,
incluidas las contrasenas de buzon que todavia usan el cifrado heredado derivado del JWT_SECRET.

Uso:  python -m app.tools.rotate_fiscal_keys [--dry-run]
Procedimiento: agregar la llave nueva AL PRINCIPIO de FISCAL_KEYS (dejando la vieja detras), desplegar api y worker,
correr esto, y recien entonces quitar la vieja. Solo imprime conteos: nunca datos.
Ese despliegue con las dos llaves tambien deja que el arranque (bootstrap.extra_invites) anote con la llave nueva la
huella de las cuentas eliminadas que sigan en BOOTSTRAP_INVITES, para que no vuelvan a recibir invitacion.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..core import secretbox
from ..models import FiscalCredential, Tenant

FIELDS = (("atv_user_enc", "atv_user", True), ("atv_password_enc", "atv_password", True), ("p12_enc", "p12", False), ("p12_pin_enc", "p12_pin", True))


def rotate(db: Session, dry_run: bool = False) -> dict:
    kid = secretbox.primary_kid()
    if not kid:
        raise SystemExit("Falta FISCAL_KEYS")
    out = {"credenciales": 0, "campos": 0, "buzones": 0, "ilegibles": 0}
    for c in db.scalars(select(FiscalCredential)).all():
        touched = False
        for col, field, _text in FIELDS:
            tok = getattr(c, col)
            if not tok or secretbox.kid_of(tok) == kid:
                continue
            aad = secretbox.aad_for(c.tenant_id, c.environment, field)
            try:
                plain = secretbox.unseal(tok, aad)
            except secretbox.SecretUnavailable:
                out["ilegibles"] += 1
                continue
            if not dry_run:
                setattr(c, col, secretbox.seal(plain, aad))
            out["campos"] += 1
            touched = True
        if touched:
            out["credenciales"] += 1
            if not dry_run:
                c.key_id = kid
    # contrasenas de buzon con el cifrado heredado (JWT_SECRET): se pasan a FISCAL_KEYS (app.bootstrap tambien lo hace)
    from ..services.inbox import upgrade_legacy_passwords

    legacy = upgrade_legacy_passwords(db, dry_run=dry_run)
    out["buzones"] += legacy["migradas"]
    out["ilegibles"] += legacy["ilegibles"]
    for t in db.scalars(select(Tenant)).all():
        inbox = dict((t.settings or {}).get("inbox") or {})
        tok = inbox.get("password_enc")
        if not tok or not tok.startswith("v1.") or secretbox.kid_of(tok) == kid:
            continue
        aad = f"{t.id}:imap:password"
        try:
            plain = secretbox.unseal(tok, aad)
        except secretbox.SecretUnavailable:
            out["ilegibles"] += 1
            continue
        if not dry_run:
            inbox["password_enc"] = secretbox.seal(plain, aad)
            st = dict(t.settings or {})
            st["inbox"] = inbox
            t.settings = st
            flag_modified(t, "settings")
        out["buzones"] += 1
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return out


def main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Rotar la llave de las credenciales fiscales")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from ..core.db import SessionLocal

    with SessionLocal() as db:
        res = rotate(db, args.dry_run)
    print(("SIMULACION: " if args.dry_run else "") + ", ".join(f"{k}={v}" for k, v in res.items()))


if __name__ == "__main__":  # pragma: no cover
    main()
