"""API local DESECHABLE para la compra de punta a punta de la tienda (plan de la tienda, O0-03), sin Docker.

Un solo proceso hace lo mismo que el contenedor del api (migrar y arrancar) mas la semilla de pruebas:

    alembic upgrade head  ->  scripts.seed_tienda_e2e  ->  uvicorn en 127.0.0.1:8101

Lo levanta y lo apaga Playwright (`npm run e2e:compra` en portal/, ver portal/e2e/README.md). A mano, desde api/:

    uv run python -m scripts.tienda_e2e_local

Que lo hace seguro:
- La base es una SQLite nueva en cada arranque, en la carpeta temporal del sistema (fuera del repo). Con
  E2E_DATABASE_URL usa esa base (el CI pasa una Postgres de servicio); solo se aceptan bases de esta maquina, y si ya
  tiene tablas, solo si su nombre la marca como de pruebas (e2e, test o prueba). Todo eso se revisa ANTES de migrar.
- Las variables de Railway se buscan en el entorno original, antes de limpiarlo: dentro de `railway run` no arranca.
- El entorno se limpia ANTES de cargar la aplicacion: fuera llaves de correo, ONVO, Eurocomp, Hacienda, BCCR, buzon
  IMAP, respaldo y variables de Railway. Tampoco se lee ningun .env: el proceso trabaja en la carpeta temporal.
  Resultado: ENV=local, el correo queda "simulado", la tarjeta en modo de prueba y nada sale a internet.
- Escucha solo en 127.0.0.1.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

API = Path(__file__).resolve().parents[1]
REPO = API.parent
PUERTO = 8101
BASE_URL = "http://tienda.localhost:5301"
# todo lo que podria conectar este proceso con un servicio real (o cambiarle el entorno) se quita del ambiente
PREFIJOS = (
    "RESEND_",
    "MAIL_",
    "ONVO_",
    "EUROCOMP_",
    "HACIENDA_",
    "FISCAL_",
    "RECEPTION_",
    "BCCR_",
    "BOOTSTRAP_",
    "OPS_",
    "RAILWAY_",
    "RESPALDO_",
    "BACKUP_",
    "RCLONE_",
    "AGE_",
    "PUBLIC_",
    "LANDING_",
    "MEDIA_",
    "COOKIE_",
    "API_PUBLIC_",
)
SUELTAS = (
    "ENV",
    "DEBUG",
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "PROXY_SHARED_KEY",
    "HEALTH_TOKEN",
    "CORS_ORIGINS",
    "EINVOICE_PROVIDER",
    "TEST_DATABASE_URL",
)


def entorno_limpio(entorno: dict, database_url: str, base_url: str) -> dict:
    """El ambiente con el que arranca el API de pruebas: sin llaves ni servicios reales, con lo local fijo."""
    limpio = {k: v for k, v in entorno.items() if not (k.upper().startswith(PREFIJOS) or k.upper() in SUELTAS)}
    limpio.update(
        ENV="local",
        DATABASE_URL=database_url,
        PUBLIC_BASE_URL=base_url,
        CORS_ORIGINS=json.dumps([base_url]),
        EINVOICE_PROVIDER="none",
        # llave de cifrado al azar, solo para este arranque (con Postgres el API no usa la de desarrollo): nada real se cifra con ella
        FISCAL_KEYS="e2e:" + base64.urlsafe_b64encode(os.urandom(32)).decode(),
        REDIS_URL="redis://127.0.0.1:1/0",  # un puerto donde no hay nada: el API no usa Redis y asi no toca uno real
    )
    return limpio


def carpeta_de_datos(ruta: str | None) -> Path:
    """Carpeta de trabajo del proceso (y de la SQLite). Nunca dentro del repo: ahi podria haber un .env."""
    datos = Path(ruta).resolve() if ruta else Path(tempfile.gettempdir()).resolve() / "crimson-tienda-e2e"
    if datos == REPO or REPO in datos.parents:
        raise SystemExit(f"[e2e-local] La carpeta de datos no puede estar dentro del repositorio: {datos}")
    datos.mkdir(parents=True, exist_ok=True)
    if (datos / ".env").exists():
        raise SystemExit(f"[e2e-local] Hay un .env en {datos}: el API de pruebas no arranca leyendo variables de un archivo.")
    return datos


PANEL_ADMIN = "panel.e2e@example.com"  # dominio reservado para ejemplos
PANEL_ADMIN_2FA = "panel.2fa.e2e@example.com"  # O2-01d: el mismo administrador, con segundo factor


def crear_admin_del_panel(SessionLocal, password: str, totp_secret: str = "") -> None:
    """Administrador de la empresa de pruebas para las pruebas del panel (portal/e2e/mi-tienda.spec.ts). La contrasena
    la inventa Playwright al azar en cada corrida (E2E_PANEL_PASSWORD) y vive solo en la memoria de ese proceso y en
    esta base desechable: no hay ninguna escrita en el repositorio.
    Con totp_secret (E2E_PANEL_TOTP, tambien al azar en cada corrida) se crea ademas un segundo administrador con el 2FA
    ya activo: el inicio de sesion con codigo es el arranque del panel (O2-01d, el panel va en su propio trozo)."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.models import Tenant, TenantUser, User

    with SessionLocal() as db:
        t = db.scalar(select(Tenant).where(Tenant.slug == "crimson"))
        for email, secreto in ((PANEL_ADMIN, ""), (PANEL_ADMIN_2FA, totp_secret)):
            if email == PANEL_ADMIN_2FA and not secreto:
                continue
            u = db.scalar(select(User).where(User.email == email))
            if u is None:
                u = User(email=email, full_name="Administrador de prueba", password_hash=hash_password(password))
                db.add(u)
                db.flush()
            else:
                u.password_hash = hash_password(password)
            if secreto:
                u.totp_secret, u.totp_enabled, u.totp_last_step = secreto, True, None
            if t is not None and not db.scalar(select(TenantUser).where(TenantUser.user_id == u.id, TenantUser.tenant_id == t.id)):
                db.add(TenantUser(tenant_id=t.id, user_id=u.id, role_code="admin"))
        db.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="API local desechable para la compra de punta a punta de la tienda")
    ap.add_argument("--puerto", type=int, default=int(os.environ.get("E2E_API_PUERTO") or PUERTO))
    ap.add_argument("--base-url", default=os.environ.get("E2E_COMPRA_URL") or BASE_URL, help="dirección local de la tienda (enlaces de seguimiento)")
    ap.add_argument("--datos", default=os.environ.get("E2E_DATOS"), help="carpeta de trabajo fuera del repo (por defecto, la temporal del sistema)")
    ap.add_argument("--solo-preparar", action="store_true", help="migrar y sembrar, sin arrancar el servidor")
    args = ap.parse_args(argv)
    for salida in (sys.stdout, sys.stderr):  # la consola de Windows no siempre es UTF-8: un caracter raro no tumba el arranque
        salida.reconfigure(encoding="utf-8", errors="replace")

    datos = carpeta_de_datos(args.datos)
    externa = (os.environ.get("E2E_DATABASE_URL") or "").strip()
    if externa:
        database_url = externa
    else:
        archivo = datos / "tienda-e2e.db"
        for viejo in (
            archivo,
            archivo.with_name(archivo.name + "-journal"),
            archivo.with_name(archivo.name + "-wal"),
            archivo.with_name(archivo.name + "-shm"),
        ):
            viejo.unlink(missing_ok=True)  # base nueva en cada arranque
        database_url = f"sqlite:///{archivo.as_posix()}"

    # Candado de Railway sobre el entorno ORIGINAL: entorno_limpio quita las RAILWAY_* y despues ya no se verian.
    sys.path.insert(0, str(API))
    from scripts import seed_tienda_e2e as semilla

    try:
        semilla.revisar_entorno(database_url, "local", dict(os.environ))
    except semilla.SemillaRechazada as e:
        print(f"[e2e-local] No arranca. {e}", file=sys.stderr, flush=True)
        return 2

    panel_pw = os.environ.get("E2E_PANEL_PASSWORD") or ""  # se lee antes de limpiar el entorno
    panel_totp = os.environ.get("E2E_PANEL_TOTP") or ""
    limpio = entorno_limpio(dict(os.environ), database_url, args.base_url)
    limpio.pop("E2E_PANEL_PASSWORD", None)
    limpio.pop("E2E_PANEL_TOTP", None)
    os.environ.clear()
    os.environ.update(limpio)
    os.chdir(datos)

    # desde aqui se puede cargar la aplicacion: Settings se arma al importar, ya con el entorno limpio
    from app.core.config import settings
    from app.core.db import engine

    try:
        semilla.revisar_entorno(settings.database_url, settings.env)  # segunda barrera, ya con el entorno limpio
        semilla.revisar_base_antes_de_migrar(engine)  # una base real con tablas se rechaza ANTES de migrarla
    except semilla.SemillaRechazada as e:
        print(f"[e2e-local] No arranca. {e}", file=sys.stderr, flush=True)
        return 2

    from alembic.config import Config

    from alembic import command

    cfg = Config()  # sin alembic.ini: su ruta de guiones es relativa a api/ y su registro llenaria la consola
    cfg.set_main_option("script_location", str(API / "alembic"))
    command.upgrade(cfg, "head")

    from app.core.db import SessionLocal

    try:
        with SessionLocal() as db:
            resumen = semilla.sembrar(db)
    except semilla.SemillaRechazada as e:
        print(f"[e2e-local] No arranca. {e}", file=sys.stderr, flush=True)
        return 2
    if panel_pw:
        crear_admin_del_panel(SessionLocal, panel_pw, panel_totp)
    estados = ", ".join(f"{c}={p['estado']}" for c, p in resumen["productos"].items())
    print(f"[e2e-local] Base lista ({settings.database_url.split('://')[0]}), tienda sembrada: {estados}", flush=True)
    if args.solo_preparar:
        return 0

    import uvicorn

    print(f"[e2e-local] API en http://127.0.0.1:{args.puerto} | tienda en {args.base_url}", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.puerto, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
