"""Respaldo diario automatico: lo corre el servicio cron `respaldo` de Railway todos los dias a las 03:15 CR.
Ver infra/RESPALDO.md seccion 4 (configuracion) y seccion 5 (prueba de restauracion).

    python -m app.tools.respaldo_diario                  # respalda, verifica, cifra, sube y avisa si algo falla
    python -m app.tools.respaldo_diario --revisar        # solo revisa variables y herramientas (no lee la base)
    python -m app.tools.respaldo_diario --probar-aviso   # manda un correo de prueba a RESPALDO_AVISO_A

Pasos:
1. app.tools.backup -> .zip temporal (toda la base, con archivos, sin pg_dump).
2. Verificacion: restaura ese .zip en una SQLite desechable y compara conteos y hashes (app.tools.restore). Con
   RESPALDO_VERIFICAR=hashes solo revisa los hashes del zip. Si la verificacion falla el respaldo cifrado IGUAL se sube
   (puede servir en Postgres) pero el proceso termina en falla y avisa.
3. Cifrado con age para las llaves PUBLICAS de RESPALDO_AGE_RECIPIENTS: este servicio cifra pero no puede descifrar.
4. Subida con rclone a RESPALDO_DESTINO/diario/ (y semanal/, mensual/, anual/ segun la fecha de Costa Rica): el .age y
   un resumen .json SIN datos personales (hashes, tamanos, filas, migracion, commit). El resumen se sube despues del
   .age: si el resumen existe, el .age esta completo.
5. Latido a RESPALDO_PING_URL (monitor tipo heartbeat: si un dia no llega, el monitor avisa aunque este proceso ni
   arranque). Si algo fallo: latido a <url>/fail y correo a RESPALDO_AVISO_A (por Resend, sin pasar por la base).

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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path

import httpx

FORMATO = "crimson-respaldo-diario"
VERSION = 1
OK, CONFIG, RESPALDO, VERIFICACION, CIFRADO, SUBIDA = 0, 1, 2, 3, 4, 5
AGE_HEADER = b"age-encryption.org/v1"
# llave publica de age (X25519 o de plugin). Una privada empieza con AGE-SECRET-KEY- y se rechaza.
_AGE_PUBLICA = re.compile(r"^age1[0-9a-z]{50,}$")
_CREDENCIAL_EN_URL = re.compile(r"(://)[^/\s:@]+:[^@\s]+@")


class Fallo(Exception):
    def __init__(self, codigo: int, paso: str, detalle: str):
        super().__init__(f"{paso}: {detalle}")
        self.codigo, self.paso, self.detalle = codigo, paso, detalle


@dataclass
class Config:
    destino: str
    recipients: list[str]
    verificar: str = "restaurar"
    tmp: str | None = None


def limpio(texto: str, largo: int = 500) -> str:
    """Mensajes de error para el log y el correo: sin usuario:clave@ de ninguna URL y con tope de largo."""
    return _CREDENCIAL_EN_URL.sub(r"\1***@", str(texto or "")).strip()[-largo:]


def leer_config(env: Mapping[str, str]) -> Config:
    """Valida las variables. Los mensajes nombran la variable, nunca su valor (puede ser una llave pegada por error)."""
    problemas = []
    destino = (env.get("RESPALDO_DESTINO") or "").strip().rstrip("/")
    if not destino or ":" not in destino:
        problemas.append("RESPALDO_DESTINO (remoto de rclone, p. ej. respaldo:crimson-respaldos)")
    recipients = [k for k in re.split(r"[\s,]+", env.get("RESPALDO_AGE_RECIPIENTS") or "") if k]
    if not recipients:
        problemas.append("RESPALDO_AGE_RECIPIENTS (llaves PUBLICAS de age, age1...)")
    elif any(not _AGE_PUBLICA.match(k) for k in recipients):
        privada = any(k.upper().startswith("AGE-SECRET-KEY-") for k in recipients)
        problemas.append(
            "RESPALDO_AGE_RECIPIENTS tiene una LLAVE PRIVADA: quitela de inmediato y rote ese par de llaves"
            if privada
            else "RESPALDO_AGE_RECIPIENTS tiene valores que no son llaves publicas age1..."
        )
    verificar = (env.get("RESPALDO_VERIFICAR") or "restaurar").strip().lower()
    if verificar not in ("restaurar", "hashes"):
        problemas.append("RESPALDO_VERIFICAR (restaurar | hashes)")
    if problemas:
        raise Fallo(CONFIG, "configuracion", "faltan o estan mal: " + "; ".join(problemas))
    return Config(destino=destino, recipients=recipients, verificar=verificar, tmp=(env.get("RESPALDO_TMP") or None))


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
        raise Fallo(RESPALDO, "respaldo", f"{type(e).__name__}: {limpio(e, 300)}") from e
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
        raise Fallo(VERIFICACION, "verificacion", f"{type(e).__name__}: {limpio(e, 300)}") from e
    finally:
        try:
            destino.unlink(missing_ok=True)  # es una copia en claro de toda la base: fuera del disco apenas se verifica
        except OSError:
            pass  # la carpeta temporal se borra completa al final de la corrida
    if rc != 0:
        raise Fallo(VERIFICACION, "verificacion", f"la restauracion de prueba salio con codigo {rc} (codigos en infra/RESPALDO.md seccion 5)")
    return "restaurado"


def cifrar(zip_path: Path, recipients: list[str]) -> Path:
    exe = herramienta("age")
    out = zip_path.with_name(zip_path.name + ".age")
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


def subir(local: Path, remoto: str) -> None:
    exe = herramienta("rclone")
    # --no-check-dest: las credenciales del bucket solo escriben (no leen ni listan); cada nombre es nuevo
    correr([exe, "copyto", str(local), remoto, "--no-check-dest", "--retries", "3", "--low-level-retries", "10", "--stats", "0"], SUBIDA, "subida")


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
    """Correo directo por Resend (no por el outbox: si la base es lo que fallo, el aviso igual tiene que salir)."""
    destinatarios = [x.strip() for x in (env.get("RESPALDO_AVISO_A") or "").split(",") if x.strip()]
    key = env.get("RESEND_API_KEY")
    if not destinatarios or not key:
        print("AVISO: sin RESPALDO_AVISO_A o RESEND_API_KEY: el correo de falla no se pudo mandar (queda el latido y el log)", file=sys.stderr)
        return False
    from ..services.mail import DEFAULT_FROM

    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {key}"},
            json={"from": env.get("MAIL_FROM") or DEFAULT_FROM, "to": destinatarios, "subject": asunto, "html": html},
            timeout=20,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"AVISO: el correo de falla no salio ({type(e).__name__})", file=sys.stderr)
        return False


def html_falla(fallos: list[Fallo], resumen: dict | None, fecha: date) -> str:
    items = "".join(f"<li><b>{escape(f.paso)}</b>: {escape(f.detalle)}</li>" for f in fallos)
    subido = ""
    if resumen and resumen.get("subido"):
        subido = f"<p>El respaldo cifrado <b>sí se subió</b> ({escape(resumen['archivo'])}), pero algo más falló.</p>"
    return (
        f"<p>El respaldo diario de la plataforma del {fecha.isoformat()} <b>no terminó bien</b>.</p><ul>{items}</ul>{subido}"
        "<p>Qué hacer: revisar los registros del servicio <b>respaldo</b> en Railway y seguir infra/RESPALDO.md. "
        "Mientras no se corrija, la base no tiene respaldo nuevo fuera de Railway.</p>"
    )


# ---------- corrida ----------
def ejecutar(env: Mapping[str, str] | None = None, ahora: datetime | None = None) -> int:
    from ..providers.einvoice import cr_now

    env = os.environ if env is None else env
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
        age_path = cifrar(zip_path, cfg.recipients)
        zip_path.unlink()  # el zip en claro sale del disco apenas existe el cifrado
        tot = m["totals"]
        resumen = {
            "formato": FORMATO,
            "version": VERSION,
            "archivo": age_path.name,
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
            "destinatarios": len(cfg.recipients),
        }
        json_path = tmp / f"{nombre}.json"
        json_path.write_text(json.dumps(resumen, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        for carpeta in carpetas(dia):
            subir(age_path, f"{cfg.destino}/{carpeta}/{age_path.name}")
            subir(json_path, f"{cfg.destino}/{carpeta}/{json_path.name}")
            resumen["subido"] = True
    except Fallo as f:
        fallos.append(f)
    except Exception as e:  # noqa: BLE001  (cualquier sorpresa tambien avisa: un respaldo que no corre en silencio es lo peor)
        fallos.append(Fallo(RESPALDO, "inesperado", f"{type(e).__name__}: {limpio(e, 300)}"))
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)

    ping = env.get("RESPALDO_PING_URL") or None
    if fallos:
        for f in fallos:
            print(f"FALLO {f.paso}: {f.detalle}", file=sys.stderr)
        latido(ping, ok=False)
        enviar_aviso(env, f"Crimson: falló el respaldo diario ({fallos[0].paso})", html_falla(fallos, resumen, dia))
        return fallos[0].codigo
    if resumen is None:  # no deberia pasar: sin fallos siempre hay resumen
        return RESPALDO
    print(
        f"OK respaldo diario {resumen['archivo']} sha256={resumen['zip_sha256']} filas={resumen['filas']} "
        f"archivos={resumen['archivos']} verificacion={resumen['verificacion']} carpetas={','.join(carpetas(dia))}"
    )
    latido(ping, ok=True)
    return OK


def revisar(env: Mapping[str, str]) -> int:
    """Antes de activar el cron: variables, herramientas y que age acepte las llaves (cifra unos bytes de prueba)."""
    try:
        cfg = leer_config(env)
        age = herramienta("age")
        herramienta("rclone")
        with tempfile.TemporaryDirectory(prefix="crimson-respaldo-revisar-") as d:
            p = Path(d) / "prueba.txt"
            p.write_text("prueba", encoding="utf-8")
            args = [age]
            for k in cfg.recipients:
                args += ["-r", k]
            correr([*args, "-o", str(p) + ".age", str(p)], CIFRADO, "cifrado", timeout=60)
    except Fallo as f:
        print(f"FALLO {f.paso}: {f.detalle}", file=sys.stderr)
        return f.codigo
    avisos = "si" if (env.get("RESPALDO_AVISO_A") and env.get("RESEND_API_KEY")) else "NO (sin RESPALDO_AVISO_A o RESEND_API_KEY)"
    print(f"OK configuracion: destino {cfg.destino}, {len(cfg.recipients)} llave(s) publica(s), verificacion {cfg.verificar}")
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
            os.environ, "Crimson: prueba del aviso de respaldo", "<p>Prueba del aviso de falla del respaldo diario. No hay que hacer nada.</p>"
        )
        print("OK aviso de prueba enviado" if enviado else "FALLO: el aviso de prueba no salio")
        return OK if enviado else CONFIG
    return ejecutar()


if __name__ == "__main__":
    raise SystemExit(main())
