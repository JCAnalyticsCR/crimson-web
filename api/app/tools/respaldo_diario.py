"""Respaldo diario automatico: lo corre el servicio cron `respaldo` de Railway todos los dias a las 03:15 CR.
Ver infra/RESPALDO.md seccion 4 (configuracion) y seccion 5 (prueba de restauracion).

    python -m app.tools.respaldo_diario                  # respalda, verifica, cifra, sube y avisa si algo falla
    python -m app.tools.respaldo_diario --revisar        # solo revisa variables, herramientas y que la app cargue (no lee la base)
    python -m app.tools.respaldo_diario --probar-aviso   # manda un correo de prueba a RESPALDO_AVISO_A

Pasos:
1. app.tools.backup -> .zip temporal (toda la base, con archivos, sin pg_dump).
2. Verificacion: restaura ese .zip en una SQLite desechable y compara conteos y hashes (app.tools.restore). Con
   RESPALDO_VERIFICAR=hashes solo revisa los hashes del zip. Si la verificacion falla el respaldo cifrado IGUAL se sube
   (puede servir en Postgres) pero el proceso termina en falla y avisa.
3. Cifrado con age para las llaves PUBLICAS de RESPALDO_AGE_RECIPIENTS (custodios): este servicio cifra pero no puede
   descifrar. La llave de la prueba mensual (RESPALDO_AGE_RECIPIENTS_PRUEBA) solo abre la copia de diario/: lo que se
   guarda semanas, meses o anos (semanal/, mensual/, anual/) va en un segundo .age solo para los custodios.
4. Subida con rclone a RESPALDO_DESTINO/diario/ (y semanal/, mensual/, anual/ segun la fecha de Costa Rica): el .age y
   un resumen .json SIN datos personales (hashes, tamanos, filas, migracion, commit). El resumen se sube despues del
   .age: si el resumen existe, el .age esta completo.
5. Latido a RESPALDO_PING_URL (monitor tipo heartbeat: si un dia no llega, el monitor avisa aunque este proceso ni
   arranque). Si algo fallo: latido a <url>/fail y correo a RESPALDO_AVISO_A (por Resend, sin pasar por la base y sin
   cargar la configuracion de la app: si lo que fallo son las variables, el aviso igual sale).
6. La corrida (hash, tamano, filas o el error) queda en task_run (app.services.respaldo_estado). El correo de Salud la
   muestra cuando se integre el plan 1.8 (ver infra/RESPALDO.md 4.5). Si la tabla no existe o la base no responde,
   solo queda en el log.

Nunca sube nada sin cifrar y siempre borra los temporales (tienen datos personales).
Codigos de salida: 0 bien · 1 configuracion · 2 respaldo · 3 verificacion · 4 cifrado · 5 subida.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path

import httpx
from pydantic import ValidationError

FORMATO = "crimson-respaldo-diario"
VERSION = 1
OK, CONFIG, RESPALDO, VERIFICACION, CIFRADO, SUBIDA = 0, 1, 2, 3, 4, 5
AGE_HEADER = b"age-encryption.org/v1"
# llave publica de age (X25519 o de plugin). Una privada empieza con AGE-SECRET-KEY- y se rechaza.
_AGE_PUBLICA = re.compile(r"^age1[0-9a-z]{50,}$")
_CREDENCIAL_EN_URL = re.compile(r"(://)[^/\s:@]+:[^@\s]+@")
# remitente si falta MAIL_FROM: el mismo de app.services.mail (una prueba lo compara). Copia local a proposito: importar
# ese modulo carga la configuracion de la app, y el correo de falla tiene que salir aunque la configuracion este rota.
REMITENTE = "Crimson Consulting <info@crimsoncr.com>"


class Fallo(Exception):
    def __init__(self, codigo: int, paso: str, detalle: str):
        super().__init__(f"{paso}: {detalle}")
        self.codigo, self.paso, self.detalle = codigo, paso, detalle


@dataclass
class Config:
    destino: str
    recipients: list[str]  # custodios: todas las copias
    verificar: str = "restaurar"
    tmp: str | None = None
    prueba: list[str] = field(default_factory=list)  # prueba mensual (RESPALDO.md 4.4): solo la copia de diario/


def limpio(texto: str, largo: int = 500) -> str:
    """Mensajes de error para el log y el correo: sin usuario:clave@ de ninguna URL y con tope de largo."""
    return _CREDENCIAL_EN_URL.sub(r"\1***@", str(texto or "")).strip()[-largo:]


def describir(e: BaseException, largo: int = 300) -> str:
    """Una excepcion para el log y el correo. La de la configuracion (pydantic) nombra cada variable y su problema, nunca
    el valor: str() de un ValidationError trae input_value=..., que puede ser un secreto mal pegado."""
    if isinstance(e, ValidationError):
        partes = []
        for err in e.errors(include_url=False, include_input=False, include_context=False):
            campo = ".".join(str(x) for x in err.get("loc") or ()).upper()
            partes.append(f"{campo}: {err.get('msg')}" if campo else str(err.get("msg")))
        return limpio(f"configuracion de la app invalida: {'; '.join(partes)}", largo)
    return f"{type(e).__name__}: {limpio(e, largo)}"


def _llaves(env: Mapping[str, str], variable: str, problemas: list[str]) -> list[str]:
    llaves = [k for k in re.split(r"[\s,]+", env.get(variable) or "") if k]
    if any(not _AGE_PUBLICA.match(k) for k in llaves):
        privada = any(k.upper().startswith("AGE-SECRET-KEY-") for k in llaves)
        problemas.append(
            f"{variable} tiene una LLAVE PRIVADA: quitela de inmediato y rote ese par de llaves"
            if privada
            else f"{variable} tiene valores que no son llaves publicas age1..."
        )
    return llaves


def leer_config(env: Mapping[str, str]) -> Config:
    """Valida las variables. Los mensajes nombran la variable, nunca su valor (puede ser una llave pegada por error)."""
    problemas = []
    destino = (env.get("RESPALDO_DESTINO") or "").strip().rstrip("/")
    if not destino or ":" not in destino:
        problemas.append("RESPALDO_DESTINO (remoto de rclone, p. ej. respaldo:crimson-respaldos)")
    recipients = _llaves(env, "RESPALDO_AGE_RECIPIENTS", problemas)
    if not recipients:
        problemas.append("RESPALDO_AGE_RECIPIENTS (llaves PUBLICAS de age de los custodios, age1...)")
    prueba = _llaves(env, "RESPALDO_AGE_RECIPIENTS_PRUEBA", problemas)
    if set(prueba) & set(recipients):
        # la de prueba tambien abriria semanal/, mensual/ y anual/ (meses o anos de respaldos) desde GitHub
        problemas.append(
            "RESPALDO_AGE_RECIPIENTS_PRUEBA repite una llave de RESPALDO_AGE_RECIPIENTS: la llave de prueba va solo en RESPALDO_AGE_RECIPIENTS_PRUEBA"
        )
    verificar = (env.get("RESPALDO_VERIFICAR") or "restaurar").strip().lower()
    if verificar not in ("restaurar", "hashes"):
        problemas.append("RESPALDO_VERIFICAR (restaurar | hashes)")
    if problemas:
        raise Fallo(CONFIG, "configuracion", "faltan o estan mal: " + "; ".join(problemas))
    return Config(destino=destino, recipients=recipients, verificar=verificar, tmp=(env.get("RESPALDO_TMP") or None), prueba=prueba)


def herramienta(nombre: str) -> str:
    exe = shutil.which(nombre)
    if not exe:
        raise Fallo(CONFIG, "configuracion", f"no esta instalado '{nombre}' (lo trae infra/respaldo/Dockerfile)")
    return exe


def correr(args: list[str], codigo: int, paso: str, timeout: int = 1800) -> str:
    nombre = Path(args[0]).name
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired as e:
        raise Fallo(codigo, paso, f"{nombre} no termino en {timeout // 60} min") from e
    except OSError as e:
        raise Fallo(codigo, paso, f"no se pudo ejecutar {nombre}: {limpio(e)}") from e
    if r.returncode != 0:
        raise Fallo(codigo, paso, f"{nombre} salio con codigo {r.returncode}: {limpio(r.stderr or r.stdout)}")
    return r.stdout or ""


def sha256_archivo(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def carpetas(dia: date) -> list[str]:
    """Carpetas del bucket donde va el respaldo del dia (cada una con su regla de ciclo de vida, ver RESPALDO.md)."""
    out = ["diario"]
    if dia.weekday() == 6:
        out.append("semanal")
    if dia.day == 1:
        out.append("mensual")
        if dia.month == 1:
            out.append("anual")
    return out


# ---------- pasos ----------
def respaldar(tmp: Path, nombre: str) -> tuple[Path, dict]:
    from ..services import backup as bk
    from . import backup

    out = tmp / f"{nombre}.zip"
    try:
        rc = backup.main(["--salida", str(out), "--sin-pg-dump"])
    except Exception as e:  # noqa: BLE001  (base caida, credencial mala, disco lleno: todo es "no hubo respaldo")
        raise Fallo(RESPALDO, "respaldo", describir(e)) from e
    if rc != 0 or not out.is_file():
        raise Fallo(RESPALDO, "respaldo", f"app.tools.backup salio con codigo {rc}")
    with zipfile.ZipFile(out) as z:
        return out, bk.read_manifest(z)


def verificar(zip_path: Path, tmp: Path, modo: str) -> str:
    from ..services import backup as bk

    if modo == "hashes":
        try:
            with zipfile.ZipFile(zip_path) as z:
                bk.verify_zip(z, bk.read_manifest(z))
        except (bk.BackupError, zipfile.BadZipFile) as e:
            raise Fallo(VERIFICACION, "verificacion", limpio(e)) from e
        return "hashes"
    from . import restore

    destino = tmp / "verificacion.db"
    try:
        rc = restore.main([str(zip_path), "--target", f"sqlite:///{destino.as_posix()}"])
    except Exception as e:  # noqa: BLE001
        raise Fallo(VERIFICACION, "verificacion", describir(e)) from e
    finally:
        try:
            destino.unlink(missing_ok=True)  # es una copia en claro de toda la base: fuera del disco apenas se verifica
        except OSError:
            pass  # la carpeta temporal se borra completa al final de la corrida
    if rc != 0:
        raise Fallo(VERIFICACION, "verificacion", f"la restauracion de prueba salio con codigo {rc} (codigos en infra/RESPALDO.md seccion 5)")
    return "restaurado"


def cifrar(zip_path: Path, recipients: list[str], out: Path | None = None) -> Path:
    exe = herramienta("age")
    out = out or zip_path.with_name(zip_path.name + ".age")
    args = [exe]
    for k in recipients:
        args += ["-r", k]
    correr([*args, "-o", str(out), str(zip_path)], CIFRADO, "cifrado")
    # candado: lo que se sube tiene que ser un archivo de age, nunca el zip en claro
    if not out.is_file() or out.stat().st_size == 0:
        raise Fallo(CIFRADO, "cifrado", "age no produjo el archivo cifrado")
    with open(out, "rb") as f:
        if f.read(len(AGE_HEADER)) != AGE_HEADER:
            raise Fallo(CIFRADO, "cifrado", "el archivo producido no es un archivo de age: no se sube")
    return out


# La credencial del bucket solo escribe (no lee, no lista, no borra):
# --no-check-dest  no mira si el destino existe antes de subir (cada nombre es nuevo).
# --s3-no-head     no hace HEAD del objeto despues del PUT: con esa credencial da 403 y rclone daria la subida por
#                  fallida. La integridad la confirma el ETag/MD5 que devuelve el PUT. Con un remoto que no es S3 se ignora.
RCLONE_SUBIDA = ("--no-check-dest", "--s3-no-head", "--retries", "3", "--low-level-retries", "10", "--stats", "0")


def subir(local: Path, remoto: str) -> None:
    exe = herramienta("rclone")
    correr([exe, "copyto", str(local), remoto, *RCLONE_SUBIDA], SUBIDA, "subida")


# ---------- avisos ----------
def latido(url: str | None, ok: bool) -> None:
    if not url:
        return
    destino = url if ok else url.rstrip("/") + "/fail"
    try:
        httpx.get(destino, timeout=10)
    except Exception as e:  # noqa: BLE001  (el monitor avisa igual si el latido no llega)
        print(f"AVISO: no se pudo mandar el latido al monitor ({type(e).__name__})", file=sys.stderr)


def enviar_aviso(env: Mapping[str, str], asunto: str, html: str) -> bool:
    """Correo directo por Resend: no pasa por el outbox ni carga la configuracion de la app (si la base o las variables
    son lo que fallo, el aviso igual tiene que salir). Nunca lanza."""
    destinatarios = [x.strip() for x in (env.get("RESPALDO_AVISO_A") or "").split(",") if x.strip()]
    key = env.get("RESEND_API_KEY")
    if not destinatarios or not key:
        print("AVISO: sin RESPALDO_AVISO_A o RESEND_API_KEY: el correo de falla no se pudo mandar (queda el latido y el log)", file=sys.stderr)
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": env.get("MAIL_FROM") or REMITENTE, "to": destinatarios, "subject": asunto, "html": html},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"AVISO: el correo de falla no salio ({type(e).__name__})", file=sys.stderr)
        return False


def marco(titulo: str, cuerpo: str, preheader: str | None = None) -> str:
    """Diseno base de los correos (email_layout.render_static: sin base ni configuracion de la app; ese modulo solo
    importa la libreria estandar al cargarse)."""
    from ..services import email_layout as el

    return el.render_static(title=titulo, body=cuerpo, preheader=preheader, foot="Aviso automático del respaldo diario de la plataforma.")


def html_falla(fallos: list[Fallo], resumen: dict | None, fecha: date) -> str:
    from ..services import email_layout as el

    filas = el.data_rows([(f.paso, f.detalle) for f in fallos])
    subido = ""
    if resumen and resumen.get("subido"):
        subido = el.p_html(f"El respaldo cifrado <b>sí se subió</b> ({escape(resumen['archivo'])}), pero algo más falló.")
    cuerpo = (
        el.p_html(f"El respaldo diario de la plataforma del {fecha.isoformat()} <b>no terminó bien</b>.")
        + filas
        + subido
        + el.note(
            html="<b>Qué hacer:</b> revisar los registros del servicio <b>respaldo</b> en Railway y seguir infra/RESPALDO.md. "
            "Mientras no se corrija, la base no tiene respaldo nuevo fuera de Railway.",
            warn=True,
        )
    )
    return marco("El respaldo diario no terminó bien", cuerpo, f"Respaldo del {fecha.isoformat()}: {len(fallos)} paso(s) con falla.")


# ---------- corrida ----------
def ejecutar(env: Mapping[str, str] | None = None, ahora: datetime | None = None) -> int:
    from ..providers.einvoice import cr_now

    env = os.environ if env is None else env
    inicio = datetime.now(UTC)
    ahora_cr = ahora or cr_now()
    dia = ahora_cr.date()
    nombre = "crimson-" + ahora_cr.astimezone(UTC).strftime("%Y%m%d-%H%M%S")
    fallos: list[Fallo] = []
    resumen: dict | None = None
    tmp: Path | None = None
    try:
        cfg = leer_config(env)
        herramienta("age")
        herramienta("rclone")  # antes de leer la base: sin herramientas no se genera un zip que no se puede subir
        tmp = Path(tempfile.mkdtemp(prefix="crimson-respaldo-", dir=cfg.tmp))
        zip_path, m = respaldar(tmp, nombre)
        try:
            verificacion = verificar(zip_path, tmp, cfg.verificar)
        except Fallo as f:
            fallos.append(f)
            verificacion = "fallida"
        zip_sha, zip_bytes = sha256_archivo(zip_path), zip_path.stat().st_size
        dias = carpetas(dia)
        archivo = f"{nombre}.zip.age"  # mismo nombre en cada carpeta del bucket
        age_path = cifrar(zip_path, [*cfg.recipients, *cfg.prueba])
        # con llave de prueba, lo que se guarda mas que los 14 dias de diario/ va en otro .age solo para los custodios
        largo = cifrar(zip_path, cfg.recipients, tmp / f"{nombre}.custodios.zip.age") if cfg.prueba and len(dias) > 1 else age_path
        zip_path.unlink()  # el zip en claro sale del disco apenas existen los cifrados
        tot = m["totals"]
        resumen = {
            "formato": FORMATO,
            "version": VERSION,
            "archivo": archivo,
            "creado": m["created_at"],
            "fecha_cr": dia.isoformat(),
            "commit": env.get("RAILWAY_GIT_COMMIT_SHA") or None,
            "alembic_revisions": m.get("alembic_revisions") or [],
            "manifest_sha256": m["sha256"],
            "zip_sha256": zip_sha,
            "zip_bytes": zip_bytes,
            "age_sha256": sha256_archivo(age_path),
            "age_bytes": age_path.stat().st_size,
            "tablas": tot["tables"],
            "filas": tot["rows"],
            "archivos": tot["blobs"],
            "verificacion": verificacion,
            "destinatarios": len(cfg.recipients) + len(cfg.prueba),
            "llave_prueba": bool(cfg.prueba),
        }
        copias = {age_path: (resumen, tmp / f"{nombre}.json")}
        if largo is not age_path:
            de_custodios = {**resumen, "age_sha256": sha256_archivo(largo), "age_bytes": largo.stat().st_size}
            de_custodios.update(destinatarios=len(cfg.recipients), llave_prueba=False)
            copias[largo] = (de_custodios, tmp / f"{nombre}.custodios.json")
        for r, json_path in copias.values():
            json_path.write_text(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        for carpeta in dias:
            cifrado = age_path if carpeta == "diario" else largo
            subir(cifrado, f"{cfg.destino}/{carpeta}/{archivo}")
            subir(copias[cifrado][1], f"{cfg.destino}/{carpeta}/{nombre}.json")
            resumen["subido"] = True
    except Fallo as f:
        fallos.append(f)
    except Exception as e:  # noqa: BLE001  (cualquier sorpresa tambien avisa: un respaldo que no corre en silencio es lo peor)
        fallos.append(Fallo(RESPALDO, "inesperado", describir(e)))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    ping = env.get("RESPALDO_PING_URL") or None
    if fallos:
        for f in fallos:
            print(f"FALLO {f.paso}: {f.detalle}", file=sys.stderr)
        latido(ping, ok=False)
        enviar_aviso(env, f"Crimson: falló el respaldo diario ({fallos[0].paso})", html_falla(fallos, resumen, dia))
        registrar_corrida(env, nombre, inicio, ok=False, resumen=resumen, fallos=fallos)
        return fallos[0].codigo
    if resumen is None:  # no deberia pasar: sin fallos siempre hay resumen
        return RESPALDO
    print(
        f"OK respaldo diario {resumen['archivo']} sha256={resumen['zip_sha256']} filas={resumen['filas']} "
        f"archivos={resumen['archivos']} verificacion={resumen['verificacion']} carpetas={','.join(carpetas(dia))}"
    )
    latido(ping, ok=True)
    registrar_corrida(env, nombre, inicio, ok=True, resumen=resumen, fallos=[])
    return OK


def _url_base(env: Mapping[str, str]) -> str | None:
    """DATABASE_URL de la configuracion de la app; si la configuracion no carga (la corrida ya fallo por eso y ya aviso),
    la de la variable de entorno tal cual."""
    try:
        from ..core.config import settings

        return settings.database_url
    except Exception:  # noqa: BLE001
        url = (env.get("DATABASE_URL") or "").strip()
        for prefijo in ("postgres://", "postgresql://"):
            if url.startswith(prefijo):
                return "postgresql+psycopg://" + url[len(prefijo) :]
        return url or None


def registrar_corrida(env: Mapping[str, str], nombre: str, inicio: datetime, *, ok: bool, resumen: dict | None, fallos: list[Fallo]) -> bool:
    """La corrida va a task_run (plan 1.8) para el correo de Salud. Va despues del latido y del correo (si la base es lo
    que fallo, los avisos ya salieron) y nunca lanza ni cambia el codigo de salida."""
    try:
        from ..services import respaldo_estado

        if resumen is not None:
            resumen = {**resumen, "carpetas": carpetas(date.fromisoformat(resumen["fecha_cr"]))}
        elif fallos:
            resumen = {"paso": fallos[0].paso, "codigo": fallos[0].codigo}
        error = "; ".join(f"{f.paso}: {f.detalle}" for f in fallos) or None
        url = _url_base(env)
        if not url:
            print("AVISO: sin DATABASE_URL: la corrida del respaldo no queda en task_run", file=sys.stderr)
            return False
        return respaldo_estado.registrar(url, corrida=nombre, inicio=inicio, fin=datetime.now(UTC), ok=ok, resumen=resumen, error=error)
    except Exception as e:  # noqa: BLE001
        print(f"AVISO: no se pudo registrar la corrida del respaldo en task_run ({type(e).__name__})", file=sys.stderr)
        return False


def revisar_app() -> None:
    """Lo que la corrida real va a cargar: la configuracion de la app (Settings, validada con las variables de AHORA, igual
    que al arrancar) y los modulos de respaldo y restauracion. Sin esto, --revisar diria OK y la corrida fallaria."""
    try:
        from ..core.config import Settings

        Settings()
        from ..services import respaldo_estado  # noqa: F401
        from . import backup, restore  # noqa: F401
    except Exception as e:  # noqa: BLE001
        raise Fallo(CONFIG, "configuracion", describir(e)) from e


def revisar(env: Mapping[str, str]) -> int:
    """Antes de activar el cron: variables, herramientas y que age acepte las llaves (cifra unos bytes de prueba)."""
    try:
        cfg = leer_config(env)
        age = herramienta("age")
        herramienta("rclone")
        revisar_app()
        with tempfile.TemporaryDirectory(prefix="crimson-respaldo-revisar-") as d:
            p = Path(d) / "prueba.txt"
            p.write_text("prueba", encoding="utf-8")
            args = [age]
            for k in [*cfg.recipients, *cfg.prueba]:
                args += ["-r", k]
            correr([*args, "-o", str(p) + ".age", str(p)], CIFRADO, "cifrado", timeout=60)
    except Fallo as f:
        print(f"FALLO {f.paso}: {f.detalle}", file=sys.stderr)
        return f.codigo
    avisos = "si" if (env.get("RESPALDO_AVISO_A") and env.get("RESEND_API_KEY")) else "NO (sin RESPALDO_AVISO_A o RESEND_API_KEY)"
    prueba = f"{len(cfg.prueba)} de prueba (solo diario/)" if cfg.prueba else "sin llave de prueba"
    print(f"OK configuracion: destino {cfg.destino}, {len(cfg.recipients)} llave(s) publica(s) de custodios, {prueba}, verificacion {cfg.verificar}")
    print(f"   correo de falla: {avisos} · latido: {'si' if env.get('RESPALDO_PING_URL') else 'NO (sin RESPALDO_PING_URL)'}")
    return OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tools.respaldo_diario", description="Respaldo diario cifrado fuera de Railway (servicio cron)")
    ap.add_argument("--revisar", action="store_true", help="solo revisa variables y herramientas")
    ap.add_argument("--probar-aviso", action="store_true", help="manda un correo de prueba a RESPALDO_AVISO_A")
    a = ap.parse_args(argv)
    if a.revisar:
        return revisar(os.environ)
    if a.probar_aviso:  # sin latido de falla: dejaria el monitor en rojo hasta el respaldo del dia siguiente
        enviado = enviar_aviso(
            os.environ,
            "Crimson: prueba del aviso de respaldo",
            marco(
                "Prueba del aviso de respaldo",
                '<p style="margin:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:15px">Prueba del aviso de falla del respaldo diario. No hay que hacer nada.</p>',
            ),
        )
        print("OK aviso de prueba enviado" if enviado else "FALLO: el aviso de prueba no salio")
        return OK if enviado else CONFIG
    return ejecutar()


if __name__ == "__main__":
    raise SystemExit(main())
