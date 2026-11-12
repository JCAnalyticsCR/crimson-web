"""Cliente S3 minimo (Firma V4 de AWS) sobre httpx, para el Storage Bucket de Railway.

Por que no boto3/botocore: solo se usan cinco operaciones (PUT, GET, HEAD, DELETE y URL prefirmada de GET). boto3 suma
~20 MB de paquetes y varias decenas de MB de RAM al importarse en cada proceso (api, worker y respaldo), y otra cadena
de dependencias que vigilar con pip-audit. httpx ya es dependencia fijada del proyecto. La firma se comprueba contra los
vectores de prueba publicados por AWS (tests/test_media_bucket.py): si la firma esta mal, esas pruebas fallan.

Nada de este modulo lee la configuracion de la app: recibe un S3Config. La decision de cuando usar el bucket esta en
services/media_store.py.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
UNSIGNED = "UNSIGNED-PAYLOAD"
ALGO = "AWS4-HMAC-SHA256"
MAX_PRESIGN = 7 * 24 * 3600  # tope de la Firma V4


class S3Error(Exception):
    """Respuesta de error del bucket o falla de red. status 0 = no hubo respuesta (red, DNS, tiempo)."""

    def __init__(self, status: int, code: str = "", detail: str = ""):
        super().__init__(f"S3 {status} {code} {detail}".strip())
        self.status, self.code = status, code


@dataclass(frozen=True)
class S3Config:
    endpoint: str  # https://t3.storageapi.dev (sin el bucket)
    bucket: str
    access_key: str
    secret_key: str
    region: str = "auto"
    path_style: bool = False  # Railway: virtual-hosted (bucket.endpoint); los buckets viejos pueden pedir path-style


def _enc(s: str, safe: str = "-_.~") -> str:
    return quote(s, safe=safe)


def canonical_query(params: Mapping[str, str]) -> str:
    return "&".join(f"{_enc(str(k))}={_enc(str(v))}" for k, v in sorted(params.items()))


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signing_key(secret_key: str, day: str, region: str, service: str = "s3") -> bytes:
    k = _hmac(("AWS4" + secret_key).encode(), day)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def _signature(secret_key: str, amz_date: str, region: str, canonical_request: str) -> tuple[str, str]:
    scope = f"{amz_date[:8]}/{region}/s3/aws4_request"
    sts = "\n".join([ALGO, amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    return scope, hmac.new(signing_key(secret_key, amz_date[:8], region), sts.encode(), hashlib.sha256).hexdigest()


def auth_headers(
    *,
    access_key: str,
    secret_key: str,
    region: str,
    method: str,
    host: str,
    path: str,
    query: Mapping[str, str] | None,
    headers: Mapping[str, str] | None,
    payload_hash: str,
    now: datetime,
) -> dict[str, str]:
    """Cabeceras firmadas (Authorization incluida). Se firman todas las cabeceras que se pasan + host y x-amz-*."""
    amz = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    h = {k.lower(): " ".join(str(v).split()) for k, v in (headers or {}).items()}
    h.update({"host": host, "x-amz-date": amz, "x-amz-content-sha256": payload_hash})
    names = sorted(h)
    signed = ";".join(names)
    canon = "\n".join([method, path, canonical_query(query or {}), "".join(f"{k}:{h[k]}\n" for k in names), signed, payload_hash])
    scope, sig = _signature(secret_key, amz, region, canon)
    h["authorization"] = f"{ALGO} Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return h


def presign(
    *,
    access_key: str,
    secret_key: str,
    region: str,
    scheme: str,
    host: str,
    path: str,
    expires: int,
    now: datetime,
    extra_query: Mapping[str, str] | None = None,
    method: str = "GET",
) -> str:
    """URL prefirmada (solo la cabecera host firmada, cuerpo sin firmar)."""
    if not 1 <= expires <= MAX_PRESIGN:
        raise ValueError("vigencia de la URL prefirmada fuera de rango")
    amz = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    q = {
        **(extra_query or {}),
        "X-Amz-Algorithm": ALGO,
        "X-Amz-Credential": f"{access_key}/{amz[:8]}/{region}/s3/aws4_request",
        "X-Amz-Date": amz,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    canon = "\n".join([method, path, canonical_query(q), f"host:{host}\n", "host", UNSIGNED])
    _, sig = _signature(secret_key, amz, region, canon)
    return f"{scheme}://{host}{path}?{canonical_query(q)}&X-Amz-Signature={sig}"


_CODE = re.compile(rb"<Code>([^<]{1,80})</Code>")


def _error(r: httpx.Response) -> S3Error:
    try:
        body = r.read()[:2000]
    except Exception:  # noqa: BLE001
        body = b""
    m = _CODE.search(body or b"")
    return S3Error(r.status_code, m.group(1).decode(errors="replace") if m else "")


class S3Client:
    """Un cliente por proceso (httpx.Client es seguro entre hilos). transport: para las pruebas (httpx.MockTransport)."""

    def __init__(self, cfg: S3Config, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self.cfg = cfg
        u = urlsplit(cfg.endpoint.strip().rstrip("/"))
        if u.scheme not in ("https", "http") or not u.netloc:
            raise ValueError("ENDPOINT del bucket invalido (se espera https://host)")
        self.scheme, self.netloc, self.base = u.scheme, u.netloc, u.path.rstrip("/")
        self.http = httpx.Client(transport=transport or httpx.HTTPTransport(retries=2), timeout=httpx.Timeout(timeout, connect=5.0))

    def close(self) -> None:
        self.http.close()

    def target(self, key: str) -> tuple[str, str]:
        k = _enc(key, safe="-_.~/")
        if self.cfg.path_style:
            return self.netloc, f"{self.base}/{_enc(self.cfg.bucket)}/{k}"
        return f"{self.cfg.bucket}.{self.netloc}", f"{self.base}/{k}"

    def _send(self, method: str, key: str, *, content: bytes = b"", headers: Mapping[str, str] | None = None, stream: bool = False) -> httpx.Response:
        host, path = self.target(key)
        payload = hashlib.sha256(content).hexdigest() if content else EMPTY_SHA256
        h = auth_headers(
            access_key=self.cfg.access_key,
            secret_key=self.cfg.secret_key,
            region=self.cfg.region,
            method=method,
            host=host,
            path=path,
            query=None,
            headers=headers,
            payload_hash=payload,
            now=datetime.now(UTC),
        )
        req = self.http.build_request(method, f"{self.scheme}://{host}{path}", content=content or None, headers=h)
        try:
            return self.http.send(req, stream=stream)
        except httpx.HTTPError as e:
            raise S3Error(0, type(e).__name__) from e

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """El cuerpo va firmado (x-amz-content-sha256 = sha256 real): si llega alterado, el bucket lo rechaza."""
        r = self._send("PUT", key, content=data, headers={"content-type": content_type})
        if r.status_code not in (200, 201):
            raise _error(r)

    def get(self, key: str) -> bytes:
        r = self._send("GET", key)
        if r.status_code != 200:
            raise _error(r)
        return r.content

    def open(self, key: str) -> httpx.Response:
        """GET en streaming: el que llama itera r.iter_bytes() y SIEMPRE cierra r (r.close())."""
        r = self._send("GET", key, stream=True)
        if r.status_code != 200:
            err = _error(r)
            r.close()
            raise err
        return r

    def head(self, key: str) -> dict | None:
        r = self._send("HEAD", key)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise S3Error(r.status_code)
        return {"size": int(r.headers.get("content-length") or -1), "etag": r.headers.get("etag"), "content_type": r.headers.get("content-type")}

    def delete(self, key: str) -> None:
        r = self._send("DELETE", key)
        if r.status_code not in (200, 202, 204, 404):
            raise _error(r)

    def presign_get(self, key: str, expires: int, now: datetime | None = None, response: Mapping[str, str] | None = None) -> str:
        """response: cabeceras que el bucket pone en la respuesta (response-content-type, -disposition, -cache-control)."""
        host, path = self.target(key)
        return presign(
            access_key=self.cfg.access_key,
            secret_key=self.cfg.secret_key,
            region=self.cfg.region,
            scheme=self.scheme,
            host=host,
            path=path,
            expires=expires,
            now=now or datetime.now(UTC),
            extra_query=response,
        )
