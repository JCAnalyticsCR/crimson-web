"""Multimedia en el Storage Bucket (S3 de Railway): firma V4, escritura, lectura con respaldo base/bucket, publico en
streaming o 302, privado con la misma firma de siempre, logos en correo y PDF, guion de traslado y respaldo.

El bucket es un doble en memoria detras de un httpx.MockTransport que VERIFICA la firma V4 de cada pedido (recalcula la
Authorization con lo que llego por el cable) y el sha256 del cuerpo de cada PUT, como hace S3."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import time
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import parse_qsl

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker
from starlette.responses import StreamingResponse

from app.core.config import settings
from app.core.db import Base, make_engine
from app.models import Media, Tenant
from app.seeds import seed
from app.services import backup as bk
from app.services import bucket_s3, media_store
from app.tools import media_al_bucket as mab
from tests.conftest import ADMIN
from tests.test_infra_respaldo_diario import DOMINGO_1, _env, avisos, base_origen, nube, trabajo  # noqa: F401
from tests.test_infra_respaldo_diario import rd as respaldo_diario

ENDPOINT, BUCKET, AK, SK = "https://t3.ejemplo.dev", "crimson-media", "AKPRUEBA", "secreto-de-prueba-" + "x" * 20
HOST = f"{BUCKET}.t3.ejemplo.dev"


def png(n: int, w: int = 120, h: int = 40) -> bytes:
    """PNG minimo valido para sniff() y para medir el logo (ancho y alto en la cabecera IHDR)."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" + f"archivo-{n}".encode() * 50


class FakeBucket:
    def __init__(self):
        self.objs: dict[str, tuple[bytes, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.down = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _check_header_signature(self, req: httpx.Request) -> None:
        auth = req.headers["authorization"]
        m = re.fullmatch(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request, SignedHeaders=([^,]+), Signature=([0-9a-f]{64})", auth)
        assert m and m.group(1) == AK and m.group(3) == "auto", auth
        names = m.group(4).split(";")
        extra = {n: req.headers[n] for n in names if n not in ("host", "x-amz-date", "x-amz-content-sha256")}
        now = datetime.strptime(req.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        esperado = bucket_s3.auth_headers(
            access_key=AK,
            secret_key=SK,
            region="auto",
            method=req.method,
            host=req.headers["host"],
            path=req.url.raw_path.decode().split("?")[0],
            query=dict(req.url.params),
            headers=extra,
            payload_hash=req.headers["x-amz-content-sha256"],
            now=now,
        )
        assert esperado["authorization"] == auth, "firma V4 invalida"

    def _check_presigned(self, req: httpx.Request) -> None:
        q = dict(parse_qsl(req.url.query.decode()))
        firma = q.pop("X-Amz-Signature")
        now = datetime.strptime(q["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        extra = {k: v for k, v in q.items() if k.startswith("response-")}
        url = bucket_s3.presign(
            access_key=AK, secret_key=SK, region="auto", scheme="https", host=req.url.host, path=req.url.path, expires=int(q["X-Amz-Expires"]), now=now, extra_query=extra
        )
        assert url.endswith(f"X-Amz-Signature={firma}"), "firma prefirmada invalida"
        assert now.timestamp() + int(q["X-Amz-Expires"]) >= time.time(), "URL prefirmada vencida"

    def handle(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("bucket caido")
        assert req.url.host == HOST
        key = req.url.path.lstrip("/")
        self.calls.append((req.method, key))
        if "X-Amz-Signature" in req.url.params:
            self._check_presigned(req)
        else:
            self._check_header_signature(req)
        if req.method == "PUT":
            if hashlib.sha256(req.content).hexdigest() != req.headers["x-amz-content-sha256"]:
                return httpx.Response(400, content=b"<Error><Code>XAmzContentSHA256Mismatch</Code></Error>")
            self.objs[key] = (req.content, req.headers.get("content-type", "application/octet-stream"))
            return httpx.Response(200, headers={"etag": '"x"'})
        if req.method == "DELETE":
            self.objs.pop(key, None)
            return httpx.Response(204)
        if key not in self.objs:
            return httpx.Response(404, content=b"" if req.method == "HEAD" else b"<Error><Code>NoSuchKey</Code></Error>")
        data, ctype = self.objs[key]
        q = req.url.params
        headers = {"content-type": q.get("response-content-type") or ctype, "content-length": str(len(data))}
        if q.get("response-cache-control"):
            headers["cache-control"] = q["response-cache-control"]
        return httpx.Response(200, headers=headers, content=b"" if req.method == "HEAD" else data)


@pytest.fixture
def bucket(monkeypatch):
    fake = FakeBucket()
    monkeypatch.setattr(settings, "media_storage", "s3")
    monkeypatch.setattr(settings, "media_s3_endpoint", ENDPOINT)
    monkeypatch.setattr(settings, "media_s3_bucket", BUCKET)
    monkeypatch.setattr(settings, "media_s3_access_key_id", AK)
    monkeypatch.setattr(settings, "media_s3_secret_access_key", SecretStr(SK))
    monkeypatch.setattr(settings, "media_s3_region", "auto")
    monkeypatch.setattr(settings, "media_public_redirect", False)
    monkeypatch.setattr(media_store, "transport", fake.transport())
    media_store.reset()
    yield fake
    media_store.reset()


def _subir(client, data: bytes, private: bool = False) -> dict:
    url = "/media?visibility=private&module=field" if private else "/media"
    r = client.post(url, files={"file": ("foto.png", data, "image/png")})
    assert r.status_code == 201, r.text
    return r.json()


def _fila(db, key: str) -> Media:
    db.expire_all()
    return db.scalar(sa.select(Media).where(Media.key == key))


# ---------- firma V4 ----------
def test_firma_v4_contra_los_vectores_publicados_por_aws():
    """Ejemplos de la documentacion de S3 (Signature Version 4): si la firma propia esta mal, esto falla."""
    now = datetime(2013, 5, 24, tzinfo=UTC)
    k = {"access_key": "AKIAIOSFODNN7EXAMPLE", "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "region": "us-east-1"}
    url = bucket_s3.presign(**k, scheme="https", host="examplebucket.s3.amazonaws.com", path="/test.txt", expires=86400, now=now)
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
    h = bucket_s3.auth_headers(
        **k, method="GET", host="examplebucket.s3.amazonaws.com", path="/test.txt", query=None, headers={"Range": "bytes=0-9"}, payload_hash=bucket_s3.EMPTY_SHA256, now=now
    )
    assert h["authorization"].endswith("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
    h = bucket_s3.auth_headers(
        **k, method="GET", host="examplebucket.s3.amazonaws.com", path="/", query={"max-keys": "2", "prefix": "J"}, headers={}, payload_hash=bucket_s3.EMPTY_SHA256, now=now
    )
    assert h["authorization"].endswith("Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7")
    with pytest.raises(ValueError):
        bucket_s3.presign(**k, scheme="https", host="h", path="/x", expires=8 * 24 * 3600, now=now)


def test_variables_con_los_nombres_de_railway(monkeypatch):
    from app.core.config import Settings

    for k, v in {"BUCKET": "b-123", "ENDPOINT": ENDPOINT, "ACCESS_KEY_ID": "id", "SECRET_ACCESS_KEY": "sk", "REGION": "auto", "MEDIA_STORAGE": "s3"}.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert (s.media_s3_bucket, s.media_s3_endpoint, s.media_s3_access_key_id, s.media_s3_secret_access_key.get_secret_value(), s.media_s3_region, s.media_storage) == (
        "b-123",
        ENDPOINT,
        "id",
        "sk",
        "auto",
        "s3",
    )
    monkeypatch.setenv("REGION", "")  # vacia en Railway = no tenerla
    assert Settings(_env_file=None).media_s3_region == "auto"


# ---------- escritura ----------
def test_sin_variables_sigue_en_la_base(client, auth, db_session, monkeypatch):
    media_store.reset()
    monkeypatch.setattr(settings, "media_storage", "s3")  # pedido, pero sin bucket: no rompe nada
    for k in ("media_s3_endpoint", "media_s3_bucket", "media_s3_access_key_id", "media_s3_secret_access_key"):
        monkeypatch.setattr(settings, k, None)
    assert media_store.backend() == "db"
    j = _subir(client, png(1))
    m = _fila(db_session, j["key"])
    assert m.storage == "db" and m.data == png(1)
    r = client.get(f"/media/f/{j['key']}")
    assert r.status_code == 200 and r.content == png(1)
    media_store.reset()


def test_subida_nueva_va_al_bucket_sin_bytes_en_la_base(client, auth, db_session, bucket):
    j = _subir(client, png(2))
    m = _fila(db_session, j["key"])
    assert m.storage == "s3" and m.data is None and m.size == len(png(2)) and m.sha256 == hashlib.sha256(png(2)).hexdigest()
    okey = f"media/{m.tenant_id}/{m.key}"
    assert bucket.objs[okey] == (png(2), "image/png")
    # el mismo archivo otra vez reutiliza la fila (dedupe por sha256): no sube de nuevo
    puts = sum(1 for c in bucket.calls if c[0] == "PUT")
    assert _subir(client, png(2))["id"] == j["id"]
    assert sum(1 for c in bucket.calls if c[0] == "PUT") == puts


def test_bucket_caido_al_subir_queda_en_la_base(client, auth, db_session, bucket):
    bucket.down = True
    j = _subir(client, png(3))
    m = _fila(db_session, j["key"])
    assert m.storage == "db" and m.data == png(3)
    bucket.down = False
    assert client.get(f"/media/f/{j['key']}").content == png(3)


def test_borrar_quita_el_objeto_del_bucket(client, auth, db_session, bucket):
    j = _subir(client, png(4))
    m = _fila(db_session, j["key"])
    assert f"media/{m.tenant_id}/{m.key}" in bucket.objs
    assert client.delete(f"/media/{j['id']}").status_code == 204
    assert f"media/{m.tenant_id}/{m.key}" not in bucket.objs


# ---------- lectura ----------
def test_publico_en_streaming_con_las_cabeceras_de_siempre(client, auth, db_session, bucket):
    j = _subir(client, png(5))
    r = client.get(f"/media/f/{j['key']}")
    assert r.status_code == 200 and r.content == png(5)
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff" and "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["content-type"] == "image/png" and r.headers["content-length"] == str(len(png(5)))
    assert ("GET", f"media/{_fila(db_session, j['key']).tenant_id}/{j['key']}") in bucket.calls
    # el navegador que ya lo tiene no hace bajar nada del bucket
    gets = len(bucket.calls)
    r2 = client.get(f"/media/f/{j['key']}", headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304 and len(bucket.calls) == gets
    # streaming: la respuesta no lleva los bytes en memoria y la columna data ni se carga
    m = _fila(db_session, j["key"])
    resp = media_store.response(m, {})
    assert isinstance(resp, StreamingResponse) and "data" in sa.inspect(m).unloaded


def test_publico_con_302_a_url_prefirmada_corta(client, auth, db_session, bucket, monkeypatch):
    monkeypatch.setattr(settings, "media_public_redirect", True)
    j = _subir(client, png(6))
    r = client.get(f"/media/f/{j['key']}", follow_redirects=False)
    assert r.status_code == 302
    loc = httpx.URL(r.headers["location"])
    q = dict(loc.params)
    assert loc.host == HOST and q["X-Amz-SignedHeaders"] == "host" and int(q["X-Amz-Expires"]) <= 2 * settings.media_presign_seconds
    assert q["response-content-type"] == "image/png" and q["response-content-disposition"].startswith("inline")
    edad = int(r.headers["cache-control"].split("max-age=")[1])
    assert 0 < edad <= 2 * settings.media_presign_seconds
    # misma ventana = misma URL (el navegador reutiliza la imagen)
    assert client.get(f"/media/f/{j['key']}", follow_redirects=False).headers["location"] == r.headers["location"]
    with httpx.Client(transport=bucket.transport()) as c:
        got = c.get(str(loc))
    assert got.status_code == 200 and got.content == png(6) and got.headers["content-type"] == "image/png"


def test_privado_sigue_pidiendo_firma_y_nunca_sale_prefirmado(client, auth, db_session, bucket, monkeypatch):
    monkeypatch.setattr(settings, "media_public_redirect", True)  # aun con 302 activo para los publicos
    j = _subir(client, png(7), private=True)
    m = _fila(db_session, j["key"])
    assert m.storage == "s3" and m.data is None and m.visibility == "private"
    gets = len(bucket.calls)
    assert client.get(f"/media/f/{j['key']}").status_code == 401  # sin firma
    assert client.get(f"/media/f/{j['key']}?e={int(time.time()) + 600}&s=firma-falsa").status_code == 403
    assert client.get(f"/media/f/{j['key']}?e={int(time.time()) - 10}&s=x").status_code == 403
    assert len(bucket.calls) == gets  # sin firma valida ni se pregunta al bucket
    signed = httpx.URL(j["signed_url"])
    r = client.get(f"/media/f/{j['key']}?{signed.query.decode()}", follow_redirects=False)
    assert r.status_code == 200 and r.content == png(7) and "location" not in r.headers
    assert r.headers["cache-control"].startswith("private, max-age=")
    with pytest.raises(ValueError):
        media_store.presigned_public_url(m, "inline")


def test_lectura_con_respaldo_entre_base_y_bucket(client, auth, db_session, bucket):
    tid = db_session.scalar(sa.select(Tenant.id).where(Tenant.slug == "crimson"))

    def fila(n: int, storage: str, data: bytes | None) -> Media:
        b = png(n)
        m = Media(tenant_id=tid, key=f"llave-{n}", filename="x.png", content_type="image/png", size=len(b), sha256=hashlib.sha256(b).hexdigest(), data=data, storage=storage)
        db_session.add(m)
        db_session.commit()
        return m

    en_base = fila(10, "db", png(10))
    copiado = fila(11, "s3", png(11))  # copiado y verificado, todavia con bytes en la base
    liberado = fila(12, "s3", None)
    bucket.objs[f"media/{tid}/llave-11"] = (png(11), "image/png")
    bucket.objs[f"media/{tid}/llave-12"] = (png(12), "image/png")

    assert media_store.read(en_base) == png(10) and not any(k.endswith("llave-10") for _, k in bucket.calls)
    assert media_store.read(liberado) == png(12)
    assert client.get("/media/f/llave-12").content == png(12)
    n = len(bucket.calls)
    assert client.get("/media/f/llave-11").content == png(11) and len(bucket.calls) == n + 1  # "s3" se lee del bucket

    bucket.down = True  # bucket caido: lo copiado sale de la base; lo liberado no esta en ningun lado -> 503
    assert media_store.read(copiado) == png(11)
    assert client.get("/media/f/llave-11").content == png(11)
    assert media_store.read(liberado) is None
    r = client.get("/media/f/llave-12")
    assert r.status_code == 503 and r.headers["retry-after"] == "60"
    assert client.get("/media/f/llave-10").content == png(10)  # lo de la base no depende del bucket


def test_logo_en_pdf_y_en_correo_desde_el_bucket(client, auth, db_session, bucket, monkeypatch):
    from app.routers.media import public_url
    from app.services import email_layout as el
    from app.services import order_print, render

    monkeypatch.setattr(settings, "public_base_url", "https://panel.ejemplo.test")
    j = _subir(client, png(20, 240, 80))
    m = _fila(db_session, j["key"])
    assert m.storage == "s3"
    t = db_session.get(Tenant, m.tenant_id)
    t.logo_url = public_url(m)
    db_session.commit()

    # hoja del pedido (lee la fila directo)
    assert order_print.logo_src(db_session, t) == "data:image/png;base64," + base64.b64encode(png(20, 240, 80)).decode()

    # correo y PDF: el logo se baja por su URL publica (como en produccion), en streaming y tambien con 302
    def _get(url, timeout=None, follow_redirects=False):
        u = httpx.URL(url)
        if u.host == "panel.ejemplo.test":
            r = client.get(u.path.removeprefix("/api"), follow_redirects=False)
            resp = httpx.Response(r.status_code, headers=r.headers, content=r.content, request=httpx.Request("GET", url))
            if follow_redirects and r.status_code in (301, 302):
                return _get(r.headers["location"], timeout, follow_redirects)
            return resp
        with httpx.Client(transport=bucket.transport()) as c:
            return c.get(url)

    monkeypatch.setattr(httpx, "get", _get)
    for redirect in (False, True):
        monkeypatch.setattr(settings, "media_public_redirect", redirect)
        el._LOGO_CACHE.clear()
        assert el.logo_size(t.logo_url) == (120, 40)
        html, adj = el.inline_logo(f'<img data-logo="1" src="{t.logo_url}" alt="x">')
        assert f"cid:{el.LOGO_CID}" in html and base64.b64decode(adj["content"]) == png(20, 240, 80)
        el._LOGO_CACHE.clear()
        assert render.brand_ctx(t)["logo"] == "data:image/png;base64," + base64.b64encode(png(20, 240, 80)).decode()
    el._LOGO_CACHE.clear()


# ---------- guion de traslado ----------
@pytest.fixture
def base_archivos(tmp_path):
    """Una base SQLite en archivo con 4 archivos en la base (uno de otra empresa) y la URL para el guion."""
    url = f"sqlite:///{(tmp_path / 'media.db').as_posix()}"
    eng = make_engine(url)
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as s:
        seed(s, *ADMIN)
        tid = s.scalar(sa.select(Tenant.id).where(Tenant.slug == "crimson"))
        otra = Tenant(slug="otra", name="Otra", default_currency="CRC")
        s.add(otra)
        s.flush()
        for i, t in ((1, tid), (2, tid), (3, tid), (4, otra.id)):
            b = png(100 + i)
            s.add(Media(tenant_id=t, key=f"k{i}", filename=f"f{i}.png", content_type="image/png", size=len(b), sha256=hashlib.sha256(b).hexdigest(), data=b))
        s.commit()
    yield url, eng
    eng.dispose()


def _estado(eng) -> dict[str, tuple[str, bool]]:
    with eng.connect() as c:
        return {r.key: (r.storage, r.tiene) for r in c.execute(sa.select(Media.key, Media.storage, Media.data.is_not(None).label("tiene")))}


def _correr(url, tmp_path, *args) -> tuple[int, dict]:
    ruta = tmp_path / f"resumen-{len(list(tmp_path.glob('resumen-*')))}.json"
    code = mab.main([*args, "--database-url", url, "--resumen", str(ruta)])
    return code, json.loads(ruta.read_text(encoding="utf-8"))


def test_guion_simular_copiar_liberar_y_revertir(base_archivos, bucket, tmp_path, capsys):
    url, eng = base_archivos
    total = sum(len(png(100 + i)) for i in range(1, 5))

    code, r = _correr(url, tmp_path, "simular")
    assert code == 0 and r["antes"]["pendiente"] == {"archivos": 4, "bytes": total} and r["bucket"] == "ok"
    assert not bucket.objs and all(m == "HEAD" for m, _ in bucket.calls)  # simular no escribe

    # copiar reanudable: un tope de 2 y despues el resto; nunca toca los bytes de la base
    code, r = _correr(url, tmp_path, "copiar", "--lote", "1", "--limite", "2")
    assert code == 0 and r["hechos"] == 2 and r["despues"]["pendiente"]["archivos"] == 2
    code, r = _correr(url, tmp_path, "copiar")
    assert code == 0 and r["hechos"] == 2 and r["despues"]["copiado"] == {"archivos": 4, "bytes": total}
    assert all(v == ("s3", True) for v in _estado(eng).values())
    assert len(bucket.objs) == 4 and sum(1 for m, _ in bucket.calls if m == "GET") >= 4  # cada uno se releyo
    puts = sum(1 for m, _ in bucket.calls if m == "PUT")
    code, r = _correr(url, tmp_path, "copiar")  # idempotente: nada que hacer
    assert code == 0 and r["hechos"] == 0 and sum(1 for m, _ in bucket.calls if m == "PUT") == puts

    # liberar sin --si: solo vista previa
    code, r = _correr(url, tmp_path, "liberar")
    assert code == mab.SIN_CONFIRMAR and r["hechos"] == 0 and all(v == ("s3", True) for v in _estado(eng).values())
    assert "Agregue --si" in capsys.readouterr().out

    # un objeto que desaparecio del bucket no se libera
    tid = next(k.split("/")[1] for k in bucket.objs if k.endswith("/k1"))
    del bucket.objs[f"media/{tid}/k1"]
    code, r = _correr(url, tmp_path, "liberar", "--si")
    assert code == mab.CON_ERRORES and r["hechos"] == 3 and [e["id"] for e in r["errores"]] and r["confirmado"] is True
    est = _estado(eng)
    assert est["k1"] == ("s3", True) and all(est[k] == ("s3", False) for k in ("k2", "k3", "k4"))
    assert "VACUUM FULL media" in capsys.readouterr().out
    # lo liberado se sigue leyendo (del bucket)
    with sessionmaker(bind=eng)() as s:
        m = s.scalar(sa.select(Media).where(Media.key == "k2"))
        assert media_store.read(m) == png(102)

    # revertir: todo vuelve a la base, verificado
    code, r = _correr(url, tmp_path, "revertir")
    assert code == 0 and r["hechos"] == 4 and r["despues"]["pendiente"] == {"archivos": 4, "bytes": total}
    with eng.connect() as c:
        for k, data in c.execute(sa.select(Media.key, Media.data)):
            assert bytes(data) == png(100 + int(k[1:]))


def test_guion_no_copia_bytes_corruptos_ni_sin_bucket(base_archivos, bucket, tmp_path, monkeypatch):
    url, eng = base_archivos
    with eng.begin() as c:
        c.execute(sa.update(Media).where(Media.key == "k2").values(data=b"\x89PNG\r\n\x1a\nalterado"))
    code, r = _correr(url, tmp_path, "copiar", "--tenant", "crimson")
    assert code == mab.CON_ERRORES and r["hechos"] == 2 and len(r["errores"]) == 1 and "sha256" in r["errores"][0]["motivo"]
    est = _estado(eng)
    assert est["k2"] == ("db", True) and est["k4"] == ("db", True)  # la otra empresa no se toco
    # sin bucket no hace nada
    media_store.reset()
    monkeypatch.setattr(settings, "media_s3_bucket", None)
    assert mab.main(["copiar", "--database-url", url, "--resumen", str(tmp_path / "x.json")]) == mab.CONFIG


# ---------- respaldo ----------
def test_respaldo_baja_del_bucket_y_restaura(client, auth, db_session, bucket, tmp_path):
    j = _subir(client, png(30))
    m = _fila(db_session, j["key"])
    assert m.storage == "s3" and m.data is None
    buf = BytesIO()
    man = bk.write_backup(db_session.connection(), Base.metadata, buf)
    db_session.commit()
    assert man["media_from_bucket"] == 1 and man["media_problems"] == []
    with zipfile.ZipFile(buf) as z:
        assert z.read(f"blobs/media/{m.id}_data") == png(30)
        bk.verify_zip(z, man)
        dest = make_engine(f"sqlite:///{(tmp_path / 'restaurada.db').as_posix()}")
        Base.metadata.create_all(dest)
        with dest.begin() as conn:
            bk.restore(conn, Base.metadata, z, man, log=lambda *_: None)
        with dest.connect() as conn:
            assert bk.verify_restored(conn, Base.metadata, man) == []
            row = conn.execute(sa.select(Media.data, Media.storage).where(Media.id == m.id)).one()
        dest.dispose()
    assert bytes(row.data) == png(30) and row.storage == "s3"  # autosuficiente aunque el bucket no exista

    # un objeto que falta en el bucket queda anotado (sin nombres) y el resto del respaldo sale
    bucket.objs.clear()
    man = bk.write_backup(db_session.connection(), Base.metadata, BytesIO())
    db_session.commit()
    assert man["media_problems"] == [{"id": m.id, "motivo": "bucket: 404 NoSuchKey"}]


def test_respaldo_diario_avisa_si_falta_un_archivo_del_bucket(base_origen, nube, avisos, trabajo, bucket):  # noqa: F811
    eng = make_engine(base_origen)
    b = png(40)
    with sessionmaker(bind=eng)() as s:
        tid = s.scalar(sa.select(Tenant.id).where(Tenant.slug == "crimson"))
        s.add(Media(tenant_id=tid, key="solo-bucket", filename="x.png", content_type="image/png", size=len(b), sha256=hashlib.sha256(b).hexdigest(), storage="s3"))
        s.add(Media(tenant_id=tid, key="perdido", filename="y.png", content_type="image/png", size=3, sha256="0" * 64, storage="s3"))
        s.commit()
    eng.dispose()
    bucket.objs[f"media/{tid}/solo-bucket"] = (b, "image/png")
    assert respaldo_diario.ejecutar(_env(trabajo), ahora=DOMINGO_1) == respaldo_diario.VERIFICACION
    resumen = json.loads(next(v for k, v in nube.objetos.items() if k.endswith(".json")))
    assert resumen["archivos_del_bucket"] == 1 and resumen["archivos_con_problema"] == 1 and resumen["verificacion"] == "restaurado"
    (correo,) = avisos["correos"]
    assert "archivos del bucket" in correo["html"] and "sí se subió" in correo["html"]
