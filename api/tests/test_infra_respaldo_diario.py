"""Respaldo diario automatico (plan 1.9): respalda, verifica restaurando, cifra con age, sube con rclone, avisa si falla y
deja la corrida en task_run (plan 1.8) para el correo de Salud. Prueba mensual: baja el ultimo, revisa edad y hashes,
descifra y restaura. age y rclone se simulan (sin red ni bucket)."""

import base64
import fnmatch
import json
import shutil
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, make_engine
from app.seeds import seed
from app.services import respaldo_estado
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


@pytest.fixture(autouse=True)
def _base_por_defecto(tmp_path, monkeypatch):
    """Las pruebas sin base_origen tambien registran la corrida: que sea en una SQLite desechable, nunca en ./crimson.db."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(tmp_path / 'sin-base.db').as_posix()}")


def _crear_task_run(url: str) -> None:
    """La tabla task_run del plan 1.8 (mismas columnas). Si el modelo ya la trae, checkfirst no la toca."""
    md = sa.MetaData()
    sa.Table(
        "task_run",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task", sa.String(80)),
        sa.Column("task_id", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("ok", sa.Boolean),
        sa.Column("summary", sa.JSON),
        sa.Column("error", sa.Text),
    )
    eng = make_engine(url)
    md.create_all(eng, checkfirst=True)
    eng.dispose()


def _corridas(url: str) -> list[dict]:
    eng = make_engine(url)
    with eng.connect() as c:
        filas = [dict(r) for r in c.execute(sa.text("SELECT * FROM task_run WHERE task = 'respaldo.diario' ORDER BY id")).mappings()]
    eng.dispose()
    for f in filas:
        if isinstance(f["summary"], str):
            f["summary"] = json.loads(f["summary"])
    return filas


def _revision(url: str, now: datetime, publica: bool = False) -> dict | None:
    eng = make_engine(url)
    with eng.connect() as c:
        r = (respaldo_estado.revision_publica if publica else respaldo_estado.revision)(c, now=now)
    eng.dispose()
    return r


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
    # credenciales que solo escriben: ni mirar el destino antes ni leer el objeto despues del PUT (403 = subida fallida)
    subidas_rclone = [a for a in nube.llamadas if a[0] == "rclone"]
    assert subidas_rclone and all("--no-check-dest" in a and "--s3-no-head" in a for a in subidas_rclone)
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


def test_prueba_mensual_no_confia_en_el_resumen_del_bucket(base_origen, nube, avisos, trabajo, tmp_path, capsys):
    """Quien pueda escribir en el bucket no puede colar rutas ni lineas extra en $GITHUB_OUTPUT ni un checkout raro, ni
    comandos de GitHub ("::...") en el log de la corrida."""
    assert rd.ejecutar(_env(trabajo, RAILWAY_GIT_COMMIT_SHA="main; rm -rf /"), ahora=DOMINGO_1) == rd.OK
    identidad = tmp_path / "llave-prueba.txt"
    identidad.write_text("AGE-SECRET-KEY-PRUEBA", encoding="utf-8")
    r = prueba.bajar(DESTINO, identidad, tmp_path / "r1", max_horas=100000)
    assert r["commit"] is None  # no es un SHA: se restaura con main
    clave = next(n for n in nube.objetos if n.startswith(f"{DESTINO}/diario/") and n.endswith(".json"))
    resumen = json.loads(nube.objetos[clave])
    revs = resumen["alembic_revisions"]
    nube.objetos[clave] = json.dumps({**resumen, "filas": "3\n::warning::x", "alembic_revisions": [*revs, "mala\n::error::x"]}).encode()
    salida = tmp_path / "github_output"
    env = {"RESPALDO_DESTINO": DESTINO, "RESPALDO_AGE_IDENTITY_FILE": str(identidad), "RESPALDO_MAX_HORAS": "100000", "GITHUB_OUTPUT": str(salida)}
    capsys.readouterr()
    assert prueba.main(["--dir", str(tmp_path / "r3")], env=env) == 0
    out = capsys.readouterr().out
    assert "::" not in out and "? filas" in out and all(r in out for r in revs)
    for valor in ("no-es-fecha", 12345, None):
        nube.objetos[clave] = json.dumps({**resumen, "creado": valor}).encode()
        with pytest.raises(rd.Fallo) as e:
            prueba.bajar(DESTINO, identidad, tmp_path / "r4", max_horas=100000)
        assert e.value.codigo == prueba.DESCARGA
    nube.objetos[clave] = json.dumps({**resumen, "archivo": resumen["archivo"] + "\nzip=/etc/passwd"}).encode()
    with pytest.raises(rd.Fallo) as e:
        prueba.bajar(DESTINO, identidad, tmp_path / "r2", max_horas=100000)
    assert e.value.codigo == prueba.DESCARGA


def test_corridas_quedan_en_task_run_y_salud_las_ve(base_origen, nube, avisos, trabajo):
    _crear_task_run(base_origen)
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.OK
    (buena,) = _corridas(base_origen)
    r = buena["summary"]
    assert buena["ok"] in (True, 1) and buena["error"] is None and buena["task_id"].startswith("crimson-")
    assert len(r["zip_sha256"]) == 64 and r["filas"] > 0 and r["age_bytes"] > 0 and r["verificacion"] == "restaurado"
    assert r["carpetas"] == ["diario", "semanal", "mensual"]
    assert ADMIN[0] not in json.dumps(r)  # el resumen de la corrida no lleva datos personales

    ahora = datetime.now(UTC)
    rev = _revision(base_origen, ahora + timedelta(hours=2))
    assert rev["ok"] is True and rev["critico"] is False and rev["filas"] == r["filas"]
    assert rev["sha256"].startswith(r["zip_sha256"][:16]) and rev["tamano"].endswith("MB")
    assert _revision(base_origen, ahora + timedelta(hours=2), publica=True) == {
        "ok": True,
        "critico": False,
        "vencido": False,
        "horas_desde_el_ultimo_bueno": 2.0,
    }
    vencida = _revision(base_origen, ahora + timedelta(hours=30))
    assert vencida["ok"] is False and "vencido" in vencida and vencida["critico"] is False  # aviso, nunca 503
    assert _revision(base_origen, ahora + timedelta(hours=30), publica=True)["vencido"] is True

    nube.falla.add("rclone")
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1 + timedelta(days=1)) == rd.SUBIDA
    mala = _corridas(base_origen)[-1]
    assert mala["ok"] in (False, 0) and "subida" in mala["error"]
    rev = _revision(base_origen, datetime.now(UTC))
    assert rev["ok"] is False and "subida" in rev["fallo"] and rev["filas"] == r["filas"]  # muestra el ultimo bueno
    # /health/deep (lo consulta un monitor externo): ni el error (hosts, usuarios, bucket) ni filas, tamano o hash
    publica = _revision(base_origen, datetime.now(UTC), publica=True)
    assert set(publica) == {"ok", "critico", "vencido", "horas_desde_el_ultimo_bueno"} and publica["ok"] is False


def test_configuracion_incompleta_tambien_queda_en_task_run(nube, avisos, trabajo, tmp_path, monkeypatch):
    from app.core.config import settings

    url = f"sqlite:///{(tmp_path / 'con-task-run.db').as_posix()}"
    _crear_task_run(url)
    monkeypatch.setattr(settings, "database_url", url)
    assert rd.ejecutar(_env(trabajo, RESPALDO_DESTINO=None), ahora=DOMINGO_1) == rd.CONFIG
    (mala,) = _corridas(url)
    assert mala["ok"] in (False, 0) and "RESPALDO_DESTINO" in mala["error"] and mala["summary"]["paso"] == "configuracion"
    assert _revision(url, datetime.now(UTC))["ultimo"] == "ninguno salió bien"


def test_sin_task_run_el_respaldo_sigue(base_origen, nube, avisos, trabajo, capsys):
    eng = make_engine(base_origen)
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE IF EXISTS task_run"))  # antes del plan 1.8 (o si se borro)
    eng.dispose()
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.OK
    assert "no tiene task_run" in capsys.readouterr().err
    assert _revision(base_origen, datetime.now(UTC)) is None
    assert avisos["latidos"] == ["https://latido.ejemplo.com/abc"]


def test_registrar_con_la_base_caida_no_lanza(tmp_path, capsys):
    url = f"sqlite:///{(tmp_path / 'no-existe' / 'x.db').as_posix()}"
    ahora = datetime.now(UTC)
    assert respaldo_estado.registrar(url, corrida="crimson-x", inicio=ahora, fin=ahora, ok=False, resumen=None, error="respaldo: caida") is False
    assert "no se pudo registrar" in capsys.readouterr().err


def test_task_run_con_columnas_en_espanol_y_sellos_de_tiempo(tmp_path):
    """El plan 1.8 describe task_run como "tarea, inicio, fin, ok, resumen, error" y el repo usa TimestampMixin
    (created_at/updated_at NOT NULL con default solo en Python): la corrida entra igual, con el resumen como texto."""
    url = f"sqlite:///{(tmp_path / 'es.db').as_posix()}"
    md = sa.MetaData()
    sa.Table(
        "task_run",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tarea", sa.String(40), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True)),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("resumen", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    eng = make_engine(url)
    md.create_all(eng)
    eng.dispose()
    ahora = datetime.now(UTC)
    resumen = {"filas": 7, "age_bytes": 2_097_152, "zip_sha256": "f" * 64, "verificacion": "restaurado"}
    assert respaldo_estado.registrar(url, corrida="crimson-x", inicio=ahora, fin=ahora, ok=True, resumen=resumen, error=None) is True
    rev = _revision(url, ahora + timedelta(hours=1))
    assert rev["ok"] is True and rev["filas"] == 7 and rev["tamano"] == "2.0 MB" and rev["sha256"].startswith("f" * 16)


def test_task_run_con_columna_obligatoria_desconocida_avisa_sin_romper(tmp_path, capsys):
    """Si task_run exige algo que el respaldo no sabe llenar (p. ej. tenant_id), lo dice en el log nombrandolo."""
    url = f"sqlite:///{(tmp_path / 'tenant.db').as_posix()}"
    md = sa.MetaData()
    sa.Table(
        "task_run",
        md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.Column("task", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
    )
    eng = make_engine(url)
    md.create_all(eng)
    eng.dispose()
    ahora = datetime.now(UTC)
    assert respaldo_estado.registrar(url, corrida="crimson-x", inicio=ahora, fin=ahora, ok=False, resumen=None, error="x") is False
    assert "tenant_id" in capsys.readouterr().err


def test_task_run_del_modelo_recibe_la_corrida(base_origen, nube, avisos, trabajo):
    """Contrato con el plan 1.8: con el modelo TaskRun en Base.metadata (la tabla la crea el modelo, no esta prueba), la
    corrida queda registrada y Salud la lee. Se activa sola al integrar 1.8; si las columnas no calzan, falla aqui."""
    if "task_run" not in Base.metadata.tables:
        pytest.skip("task_run llega con el plan 1.8: esta prueba se activa sola al integrarlo")
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.OK
    ahora = datetime.now(UTC)
    rev = _revision(base_origen, ahora)
    assert rev is not None and rev["ok"] is True and rev["filas"] != "—" and rev["verificacion"] == "restaurado"
    assert _revision(base_origen, ahora, publica=True)["ok"] is True


def test_remitente_igual_al_de_mail():
    from app.services import mail

    assert rd.REMITENTE == mail.DEFAULT_FROM


def _settings_invalida():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, env="production", jwt_secret="corto-pero-secreto", database_url="postgresql://u:p@h/db")
    return e.value


def test_configuracion_de_la_app_rota_igual_avisa(nube, avisos, trabajo, monkeypatch, capsys):
    """Si la configuracion de la app no carga (falta JWT_SECRET con ENV=production, por ejemplo), la corrida falla como
    respaldo (codigo 2), el correo sale igual (sin cargar app.services.mail) y nada nombra el valor de la variable."""
    error = _settings_invalida()

    def _rota(tmp, nombre):
        raise error

    monkeypatch.setattr(rd, "respaldar", _rota)
    # como en el proceso real: importar la configuracion (o lo que la importa) vuelve a fallar
    monkeypatch.setitem(sys.modules, "app.core.config", None)
    monkeypatch.setitem(sys.modules, "app.services.mail", None)
    assert rd.ejecutar(_env(trabajo), ahora=DOMINGO_1) == rd.RESPALDO
    (correo,) = avisos["correos"]
    assert correo["from"] == rd.REMITENTE and "JWT_SECRET" in correo["html"]
    err = capsys.readouterr().err
    assert "corto-pero-secreto" not in correo["html"] and "corto-pero-secreto" not in err
    assert avisos["latidos"] == ["https://latido.ejemplo.com/abc/fail"]
    assert "task_run" in err  # el registro avisa en el log y no cambia el codigo de salida


def test_revisar_valida_la_configuracion_de_la_app(nube, avisos, trabajo, monkeypatch, capsys):
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./no-se-abre.db")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert rd.revisar(_env(trabajo)) == rd.OK
    assert "OK configuracion" in capsys.readouterr().out

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("JWT_SECRET", "corto-pero-secreto")
    assert rd.revisar(_env(trabajo)) == rd.CONFIG
    err = capsys.readouterr().err
    assert "FALLO configuracion" in err and "JWT_SECRET" in err and "corto-pero-secreto" not in err


class _Diag:
    sqlstate, table_name, column_name, datatype_name, constraint_name = "23505", "user", None, None, "user_email_key"


class _PgError(Exception):
    diag = _Diag()


def _zip_de_prueba(tmp_path) -> Path:
    from app.tools import backup

    z = tmp_path / "respaldo.zip"
    assert backup.main(["--salida", str(z), "--sin-pg-dump"]) == 0
    return z


@pytest.mark.parametrize(
    "error",
    [
        sa.exc.IntegrityError(
            "INSERT INTO user (email) VALUES (%(email)s)",
            {"email": "persona@ejemplo.com"},
            _PgError('duplicate key value violates unique constraint "user_email_key"\nDETAIL: Key (email)=(persona@ejemplo.com) already exists.'),
        ),
        ValueError("invalid literal for int() with base 10: 'persona@ejemplo.com'"),
    ],
)
def test_restore_no_muestra_datos_de_las_filas(base_origen, tmp_path, monkeypatch, capsys, error):
    """La prueba mensual corre en GitHub Actions de un repo publico: un error al restaurar no puede mostrar valores."""
    z = _zip_de_prueba(tmp_path)

    def _falla(conn, md, zf, m, log=print):
        raise error

    monkeypatch.setattr(restore_tool.bk, "restore", _falla)
    target = f"sqlite:///{(tmp_path / 'destino.db').as_posix()}"
    capsys.readouterr()
    assert restore_tool.main([str(z), "--target", target]) == 6
    salida = capsys.readouterr()
    texto = salida.out + salida.err
    assert "persona@ejemplo.com" not in texto and "ERROR al restaurar" in texto
    if isinstance(error, sa.exc.IntegrityError):
        assert "restriccion user_email_key" in texto and "tabla user" in texto and "sqlstate 23505" in texto
    # en una maquina de confianza, el detalle completo
    assert restore_tool.main([str(z), "--target", f"sqlite:///{(tmp_path / 'destino2.db').as_posix()}", "--detalle-error"]) == 6
    assert "persona@ejemplo.com" in capsys.readouterr().err
