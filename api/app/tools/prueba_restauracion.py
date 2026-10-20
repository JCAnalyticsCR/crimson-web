"""Prueba mensual de restauracion, paso 1: baja el respaldo diario mas reciente, revisa su edad y sus hashes y lo
descifra. El paso 2 es `python -m app.tools.restore <zip> --target <base vacia>` con el codigo del commit del respaldo.
Lo corre .github/workflows/restore-test.yml; tambien sirve a mano en una maquina de confianza. Ver infra/RESPALDO.md.

    python -m app.tools.prueba_restauracion --dir /tmp/restauracion

Variables: RESPALDO_DESTINO (remoto de rclone con credenciales de SOLO LECTURA), RESPALDO_AGE_IDENTITY_FILE (archivo
con la llave privada de la prueba) y RESPALDO_MAX_HORAS (edad maxima del ultimo respaldo; por defecto 36). Escribe
`zip`, `commit` y `archivo` en $GITHUB_OUTPUT si existe.
Codigos: 0 bien · 1 configuracion · 2 no hay respaldo reciente · 3 descarga · 4 descifrado o hash distinto.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .respaldo_diario import FORMATO, Fallo, correr, herramienta, sha256_archivo

CONFIG, SIN_RESPALDO, DESCARGA, DESCIFRADO = 1, 2, 3, 4
# lo que viene del bucket es dato: solo nombres y SHA con la forma esperada (nada de rutas ni saltos de linea)
_ARCHIVO = re.compile(r"^crimson-\d{8}-\d{6}\.zip\.age$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def ultimo_resumen(rclone: str, destino: str) -> str:
    lista = correr([rclone, "lsf", "--files-only", "--include", "crimson-*.json", f"{destino}/diario/"], DESCARGA, "listar")
    nombres = sorted(n.strip() for n in lista.splitlines() if n.strip().endswith(".json"))
    if not nombres:
        raise Fallo(SIN_RESPALDO, "listar", f"no hay respaldos en {destino}/diario/")
    return nombres[-1]  # el nombre lleva la fecha UTC: el orden alfabetico es el cronologico


def bajar(destino: str, identidad: Path, carpeta: Path, max_horas: float = 36, ahora: datetime | None = None) -> dict:
    rclone, age = herramienta("rclone"), herramienta("age")
    if not identidad.is_file():
        raise Fallo(CONFIG, "configuracion", "RESPALDO_AGE_IDENTITY_FILE no apunta a un archivo")
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = ultimo_resumen(rclone, destino)
    resumen_path = carpeta / "resumen.json"
    correr([rclone, "copyto", f"{destino}/diario/{nombre}", str(resumen_path)], DESCARGA, "descarga")
    resumen = json.loads(resumen_path.read_text(encoding="utf-8"))
    if resumen.get("formato") != FORMATO or not _ARCHIVO.match(str(resumen.get("archivo") or "")):
        raise Fallo(DESCARGA, "descarga", f"{nombre} no es un resumen de respaldo de Crimson")
    if not _COMMIT.match(str(resumen.get("commit") or "")):
        resumen["commit"] = None  # lo que no es un SHA no se usa para hacer checkout (ni entra a $GITHUB_OUTPUT)
    creado = datetime.fromisoformat(resumen["creado"])
    horas = ((ahora or datetime.now(UTC)) - creado).total_seconds() / 3600
    if horas > max_horas:
        raise Fallo(SIN_RESPALDO, "edad", f"el ultimo respaldo ({resumen['archivo']}) tiene {horas:.0f} h: el respaldo diario no esta corriendo")
    cifrado = carpeta / "respaldo.zip.age"
    correr([rclone, "copyto", f"{destino}/diario/{resumen['archivo']}", str(cifrado)], DESCARGA, "descarga")
    if sha256_archivo(cifrado) != resumen["age_sha256"]:
        raise Fallo(DESCIFRADO, "hash", "el archivo cifrado del bucket no coincide con su resumen (danado o reemplazado)")
    zip_path = carpeta / "respaldo.zip"
    correr([age, "-d", "-i", str(identidad), "-o", str(zip_path), str(cifrado)], DESCIFRADO, "descifrado")
    cifrado.unlink()
    if sha256_archivo(zip_path) != resumen["zip_sha256"]:
        raise Fallo(DESCIFRADO, "hash", "el zip descifrado no coincide con el que se respaldo")
    return {**resumen, "zip": str(zip_path), "horas": round(horas, 1)}


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(prog="python -m app.tools.prueba_restauracion", description="Baja y descifra el ultimo respaldo diario")
    ap.add_argument("--dir", required=True, help="carpeta de trabajo (fuera del repositorio; se borra al terminar la prueba)")
    a = ap.parse_args(argv)
    destino = (env.get("RESPALDO_DESTINO") or "").strip().rstrip("/")
    identidad = env.get("RESPALDO_AGE_IDENTITY_FILE") or ""
    if ":" not in destino or not identidad:
        print("FALLO configuracion: faltan RESPALDO_DESTINO o RESPALDO_AGE_IDENTITY_FILE", file=sys.stderr)
        return CONFIG
    try:
        r = bajar(destino, Path(identidad), Path(a.dir), float(env.get("RESPALDO_MAX_HORAS") or 36))
    except Fallo as f:
        print(f"FALLO {f.paso}: {f.detalle}", file=sys.stderr)
        return f.codigo
    print(f"OK {r['archivo']}: {r['horas']} h, {r['filas']} filas, migracion {', '.join(r['alembic_revisions']) or '-'}, commit {r.get('commit') or '-'}")
    salida = env.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as f:
            f.write(f"zip={r['zip']}\ncommit={r.get('commit') or ''}\narchivo={r['archivo']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
