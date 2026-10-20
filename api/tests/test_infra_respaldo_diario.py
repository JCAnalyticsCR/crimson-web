"""Respaldo diario automatico (plan 1.9): respalda, verifica restaurando, cifra con age, sube con rclone y avisa si falla.
Prueba mensual: baja el ultimo, revisa edad y hashes, descifra y restaura. age y rclone se simulan (sin red ni bucket)."""

import base64
import fnmatch
import json
import shutil
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, make_engine
from app.seeds import seed
from app.tools import prueba_restauracion as prueba
from app.tools import respaldo_diario as rd
from app.tools import restore as restore_tool
from tests.conftest import ADMIN

DESTINO = "nube:crimson-respaldos"
LLAVE_1 = "age1" + "q" * 58
LLAVE_2 = "age1" + "z" * 58
# 03:15 hora de Costa Rica del 2026-11-01, domingo y dia 1: va a diario/, semanal/ y mensual/ (no a anual/)
DOMINGO_1 = datetime(2026, 11, 1, 3, 15, tzinfo=timezone(timedelta(hours=-6)))


class Nube:
    """age y rclone de mentira: el "cifrado" es reversible pero no deja el zip legible; el bucket es un diccionario."""

    def __init__(self):
        self.objetos: dict[str, bytes] = {}
        self.llamadas: list[list[str]] = []
        self.falla: set[str] = set()
        self.en_disco_al_subir: set[str] = set()

    def instalar(self, monkeypatch):
        monkeypatch.setattr(rd, "herramienta", lambda nombre: nombre)
        monkeypatch.setattr(prueba, "herramienta", lambda nombre: nombre)
        monkeypatch.setattr(rd, "correr", self.correr)
        monkeypatch.setattr(prueba, "correr", self.correr)
        return self

    def correr(self, args, codigo, paso, timeout=1800):
        self.llamadas.append(list(args))
        exe = args[0]
        if exe in self.falla:
            raise rd.Fallo(codigo, paso, f"{exe} salio con codigo 1: error simulado")
        if exe == "age":
            src, out = Path(args[-1]), Path(args[args.index("-o") + 1])
            if "-d" in args:
                out.write_bytes(base64.b64decode(src.read_bytes()[len(rd.AGE_HEADER) + 1 :]))
            else:
                out.write_bytes(rd.AGE_HEADER + b"\n" + base64.b64encode(src.read_bytes()))
            return ""
        if args[1] == "copyto":
            src, dst = args[2], args[3]
            if dst.startswith("nube:"):
                self.objetos[dst] = Path(src).read_bytes()
                self.en_disco_al_subir.update(x.name for x in Path(src).parent.iterdir())
            else:
                Path(dst).write_bytes(self.objetos[src])
            return ""
        if args[1] == "lsf":
            carpeta, patron = args[-1], args[args.index("--include") + 1]
            nombres = [k[len(carpeta) :] for k in self.objetos if k.startswith(carpeta)]
            return "".join(n + "\n" for n in nombres if "/" not in n and fnmatch.fnmatch(n, patron))
        raise AssertionError(f"comando no simulado: {args}")


@pytest.fixture
def nube(monkeypatch):
    return Nube().instalar(monkeypatch)


@pytest.fixture
def avisos(monkeypatch):
    """Correos y latidos que saldrian (ninguno sale de verdad)."""
    enviados = {"correos": [], "latidos": []}

    class _Resp:
        def raise_for_status(self):
            return None

    def _post(url, headers=None, json=None, timeout=None):
        enviados["correos"].append(json)
        return _Resp()

    def _get(url, timeout=None):
        enviados["latidos"].append(url)
        return _Resp()

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setattr(httpx, "get", _get)
    return enviados


@pytest.fixture
def base_origen(tmp_path, monkeypatch):
    """Una base SQLite en archivo con datos semilla, que es la que respalda app.tools.backup (via DATABASE_URL)."""
    from app.core.config import settings

    url = f"sqlite:///{(tmp_path / 'origen.db').as_posix()}"
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as s:
        seed(s, *ADMIN)
    eng.dispose()
    monkeypatch.setattr(settings, "database_url", url)
    return url


@pytest.fixture
def trabajo(tmp_path):
    d = tmp_path / "trabajo"
    d.mkdir()
    return d


def _env(trabajo, **extra):
    env = {
        "RESPALDO_DESTINO": DESTINO,
        "RESPALDO_AGE_RECIPIENTS": f"{LLAVE_1}, {LLAVE_2}",
        "RESPALDO_AVISO_A": "operaciones@ejemplo.com",
        "RESPALDO_PING_URL": "https://latido.ejemplo.com/abc",
        "RESEND_API_KEY": "re_prueba_" + "x" * 20,
        "RESPALDO_TMP": str(trabajo),
        "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
    }
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def test_carpetas_por_fecha():
    assert rd.carpetas(date(2026, 10, 21)) == ["diario"]  # miercoles
    assert rd.carpetas(date(2026, 10, 25)) == ["diario", "semanal"]  # domingo
    assert rd.carpetas(date(2026, 12, 1)) == ["diario", "mensual"]
    assert rd.carpetas(date(2027, 1, 1)) == ["diario", "mensual", "anual"]


def test_respaldo_diario_completo(base_origen, nube, avisos, trabajo, capsys):
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.OK

    nombres = sorted(nube.objetos)
    for carpeta in ("diario", "semanal", "mensual"):
        assert sum(1 for n in nombres if n.startswith(f"{DESTINO}/{carpeta}/crimson-")) == 2  # .zip.age y .json
    assert not any("/anual/" in n for n in nombres)
    cifrados = [n for n in nombres if n.endswith(".zip.age")]
    resumenes = [n for n in nombres if n.endswith(".json")]
    for n in cifrados:  # lo subido es de age, nunca el zip en claro
        datos = nube.objetos[n]
        assert datos.startswith(rd.AGE_HEADER) and b"manifest.json" not in datos and not datos.startswith(b"PK")
    resumen = json.loads(nube.objetos[resumenes[0]])
    assert resumen["verificacion"] == "restaurado" and resumen["filas"] > 0 and resumen["destinatarios"] == 2
    assert resumen["commit"] == "a" * 40 and resumen["fecha_cr"] == "2026-11-01"
    assert ADMIN[0] not in nube.objetos[resumenes[0]].decode()  # el resumen no lleva datos personales
    # el resumen se sube DESPUES de su .age en cada carpeta (si existe el resumen, el cifrado esta completo)
    subidas = [a[3] for a in nube.llamadas if a[0] == "rclone"]
    assert subidas.index(resumenes[0]) > subidas.index(resumenes[0].replace(".json", ".zip.age"))
    age = next(a for a in nube.llamadas if a[0] == "age")
    assert age[1:5] == ["-r", LLAVE_1, "-r", LLAVE_2]
    assert all("--no-check-dest" in a for a in nube.llamadas if a[0] == "rclone")  # credenciales que solo escriben
    # mientras se sube, en disco solo queda lo cifrado y el resumen: ni el zip ni la base de verificacion en claro
    assert nube.en_disco_al_subir and all(n.endswith((".zip.age", ".json")) for n in nube.en_disco_al_subir)
    assert list(trabajo.iterdir()) == []  # y al terminar no queda nada
    assert avisos["latidos"] == ["https://latido.ejemplo.com/abc"] and avisos["correos"] == []
    assert "OK respaldo diario crimson-" in capsys.readouterr().out


def test_sin_cifrado_no_se_sube_nada(base_origen, nube, avisos, trabajo):
    nube.falla.add("age")
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.CIFRADO
    assert nube.objetos == {}
    assert list(trabajo.iterdir()) == []
    assert avisos["latidos"] == ["https://latido.ejemplo.com/abc/fail"]
    (correo,) = avisos["correos"]
    assert correo["to"] == ["operaciones@ejemplo.com"] and "cifrado" in correo["subject"]


def test_cifrado_que_no_es_age_no_se_sube(base_origen, nube, avisos, trabajo, monkeypatch):
    """Si 'age' devolviera el zip tal cual (una herramienta cambiada o rota), el candado lo detiene."""

    def _copia(args, codigo, paso, timeout=1800):
        if args[0] == "age":
            shutil.copyfile(args[-1], args[args.index("-o") + 1])
            return ""
        return Nube.correr(nube, args, codigo, paso, timeout)

    monkeypatch.setattr(rd, "correr", _copia)
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.CIFRADO
    assert nube.objetos == {} and len(avisos["correos"]) == 1


def test_configuracion_incompleta_avisa_sin_respaldar(nube, avisos, trabajo, monkeypatch, capsys):
    from app.tools import backup

    monkeypatch.setattr(backup, "main", lambda argv=None: pytest.fail("no debe respaldar sin configuracion"))
    privada = "AGE-SECRET-KEY-1" + "Q" * 58
    assert rd.ejecutar(_env(trabajo, RESPALDO_AGE_RECIPIENTS=privada, RESPALDO_DESTINO=None), ahora=DOMINGO_1) == rd.CONFIG
    err = capsys.readouterr().err
    assert "RESPALDO_DESTINO" in err and "LLAVE PRIVADA" in err
    (correo,) = avisos["correos"]
    assert privada not in err and privada not in correo["html"]  # se nombra la variable, nunca su valor
    assert nube.objetos == {} and avisos["latidos"][-1].endswith("/fail")


def test_verificacion_fallida_igual_sube_y_avisa(base_origen, nube, avisos, trabajo, monkeypatch):
    monkeypatch.setattr(restore_tool, "main", lambda argv=None: 7)
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.VERIFICACION
    resumenes = [n for n in nube.objetos if n.endswith(".json")]
    assert resumenes and json.loads(nube.objetos[resumenes[0]])["verificacion"] == "fallida"
    (correo,) = avisos["correos"]
    assert "verificacion" in correo["subject"] and "sí se subió" in correo["html"]
    assert list(trabajo.iterdir()) == []


def test_subida_fallida_avisa(base_origen, nube, avisos, trabajo):
    nube.falla.add("rclone")
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.SUBIDA
    assert list(trabajo.iterdir()) == []
    (correo,) = avisos["correos"]
    assert "subida" in correo["subject"] and avisos["latidos"] == ["https://latido.ejemplo.com/abc/fail"]


def test_base_caida_avisa(nube, avisos, trabajo, monkeypatch):
    from app.tools import backup

    def _caida(argv=None):
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("could not connect: postgresql://usuario:clave-secreta@db.interna:5432/x"))

    monkeypatch.setattr(backup, "main", _caida)
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.RESPALDO
    (correo,) = avisos["correos"]
    assert "OperationalError" in correo["html"] and "clave-secreta" not in correo["html"]  # sin usuario:clave@ de la URL
    assert nube.objetos == {} and list(trabajo.iterdir()) == []


def test_sin_correo_configurado_queda_el_latido(base_origen, nube, avisos, trabajo):
    nube.falla.add("rclone")
    assert rd.ejecutar(_env(trabajo, RESEND_API_KEY=None), ahora=DOMINGO_1) == rd.SUBIDA
    assert avisos["correos"] == [] and avisos["latidos"] == ["https://latido.ejemplo.com/abc/fail"]


def test_prueba_mensual_baja_el_ultimo_y_restaura(base_origen, nube, avisos, trabajo, tmp_path):
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.OK
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1 + timedelta(days=1)) == rd.OK
    identidad = tmp_path / "llave-prueba.txt"
    identidad.write_text("AGE-SECRET-KEY-PRUEBA", encoding="utf-8")
    salida = tmp_path / "github_output"
    env = {"RESPALDO_DESTINO": DESTINO, "RESPALDO_AGE_IDENTITY_FILE": str(identidad), "RESPALDO_MAX_HORAS": "100000", "GITHUB_OUTPUT": str(salida)}
    carpeta = tmp_path / "restauracion"
    assert prueba.main(["--dir", str(carpeta)], env=env) == 0
    out = dict(linea.split("=", 1) for linea in salida.read_text(encoding="utf-8").splitlines())
    assert out["commit"] == "a" * 40 and out["archivo"].startswith("crimson-20261102-")  # el mas reciente
    assert not (carpeta / "respaldo.zip.age").exists()
    target = f"sqlite:///{(tmp_path / 'restaurada.db').as_posix()}"
    assert restore_tool.main([out["zip"], "--target", target]) == 0
    eng = make_engine(target)
    with eng.connect() as c:
        assert c.scalar(sa.text("SELECT count(*) FROM user WHERE email = :e"), {"e": ADMIN[0]}) == 1
    eng.dispose()


def test_prueba_mensual_detecta_respaldo_viejo_y_alterado(base_origen, nube, avisos, trabajo, tmp_path):
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.OK
    identidad = tmp_path / "llave-prueba.txt"
    identidad.write_text("AGE-SECRET-KEY-PRUEBA", encoding="utf-8")
    # el cron dejo de correr: el ultimo respaldo tiene 3 dias
    with pytest.raises(rd.Fallo) as e:
        prueba.bajar(DESTINO, identidad, tmp_path / "r1", max_horas=36, ahora=datetime.now(UTC) + timedelta(days=3))
    assert e.value.codigo == prueba.SIN_RESPALDO
    # alguien reemplazo el cifrado en el bucket
    clave = next(n for n in nube.objetos if n.startswith(f"{DESTINO}/diario/") and n.endswith(".zip.age"))
    nube.objetos[clave] = nube.objetos[clave] + b"x"
    with pytest.raises(rd.Fallo) as e:
        prueba.bajar(DESTINO, identidad, tmp_path / "r2")
    assert e.value.codigo == prueba.DESCIFRADO
    # bucket vacio
    nube.objetos.clear()
    with pytest.raises(rd.Fallo) as e:
        prueba.bajar(DESTINO, identidad, tmp_path / "r3")
    assert e.value.codigo == prueba.SIN_RESPALDO


def test_prueba_mensual_no_confia_en_el_resumen_del_bucket(base_origen, nube, avisos, trabajo, tmp_path):
    """Quien pueda escribir en el bucket no puede colar rutas ni lineas extra en $GITHUB_OUTPUT ni un checkout raro."""
    assert rd.ejecutar(_env(trabajo, RAILWAY_GIT_COMMIT_SHA="main; rm -rf /"), ahora=DOMINGO_1) == rd.OK
    identidad = tmp_path / "llave-prueba.txt"
    identidad.write_text("AGE-SECRET-KEY-PRUEBA", encoding="utf-8")
    r = prueba.bajar(DESTINO, identidad, tmp_path / "r1", max_horas=100000)
    assert r["commit"] is None  # no es un SHA: se restaura con main
    clave = next(n for n in nube.objetos if n.startswith(f"{DESTINO}/diario/") and n.endswith(".json"))
    resumen = json.loads(nube.objetos[clave])
    nube.objetos[clave] = json.dumps({**resumen, "archivo": resumen["archivo"] + "\nzip=/etc/passwd"}).encode()
    with pytest.raises(rd.Fallo) as e:
        prueba.bajar(DESTINO, identidad, tmp_path / "r2", max_horas=100000)
    assert e.value.codigo == prueba.DESCARGA
