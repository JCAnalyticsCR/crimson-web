"""Cliente del IdP y la API de recepcion de Hacienda (token, envio, consulta).

- Token solo en memoria del proceso (nunca Redis ni la base), por (empresa, ambiente, sha256(usuario)).
- Guarda de produccion en el propio cliente: sin HACIENDA_PRODUCTION_ALLOWED, ENV=production y la huella de instancia
  guardada al activar, el constructor de un cliente de produccion lanza ProductionBlocked. Ninguna ruta de codigo puede
  hablar con produccion sin las tres llaves.
- httpx sale de http_factory(): las pruebas lo reemplazan por un transporte que falla ante cualquier peticion no simulada.
- Ningun log con cuerpo, token, Authorization, usuario ni contrasena.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from dataclasses import dataclass

import httpx

from ...core.config import settings
from .endpoints import for_env

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("crimson.hacienda")


class HaciendaAuthError(Exception):
    """Usuario/contrasena rechazados por el IdP. El mensaje no incluye el usuario."""


class HaciendaUnavailable(Exception):
    """Sin red o Hacienda caida: se reintenta despues."""


class ProductionBlocked(Exception):
    """El servidor no permite hablar con produccion (faltan llaves de D2)."""


def http_factory() -> httpx.Client:
    """Fabrica unica del cliente HTTP (las pruebas la reemplazan)."""
    return httpx.Client(timeout=20)


def instance_fingerprint() -> str:
    """Huella del servidor: Railway copia las variables al duplicar un ambiente; la huella no se copia con ellas."""
    raw = f"{settings.railway_environment_id or ''}|{settings.public_base_url}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def production_keys(saved_instance: str | None) -> tuple[bool, str | None]:
    """(permitido, motivo). Las tres llaves del servidor para mandar a produccion, evaluadas en el momento."""
    if not settings.hacienda_production_allowed:
        return False, "El servidor no permite producción (HACIENDA_PRODUCTION_ALLOWED)"
    if settings.env != "production":
        return False, "El servidor no permite producción (ENV no es production)"
    if not saved_instance or saved_instance != instance_fingerprint():
        return False, "Producción activada en otro servidor: reactivar"
    return True, None


@dataclass
class PostResult:
    kind: str  # ok | already_received | rejected | retry
    location: str | None = None
    cause: str | None = None
    retry_after: int | None = None


@dataclass
class StatusResult:
    kind: str  # procesando | aceptado | rechazado | error | not_found | retry
    xml: str | None = None
    detail: str | None = None
    retry_after: int | None = None


_cache: dict[tuple, dict] = {}
_cache_lock = threading.Lock()
# X-Ratelimit-Remaining en 0: el resto del lote del worker espera al siguiente ciclo
_rate = {"until": 0.0}


def rate_limited() -> bool:
    return time.monotonic() < _rate["until"]


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
    _rate["until"] = 0.0


def _detail_from(xml: str | None) -> str | None:
    if not xml:
        return None
    try:
        from .xml44 import parse_xml_bytes, text

        root = parse_xml_bytes(xml.encode("utf-8"))
        d = text(root, "DetalleMensaje")
        return d[:500] if d else None
    except Exception:  # noqa: BLE001
        return None


class HaciendaClient:
    def __init__(
        self,
        environment: str,
        username: str,
        password: str,
        *,
        tenant_id: int,
        production_instance: str | None = None,
        http: httpx.Client | None = None,
    ):
        if environment == "produccion":
            ok, why = production_keys(production_instance)
            if not ok:
                raise ProductionBlocked(why or "El servidor no permite producción")
        self.environment = environment
        self.ep = for_env(environment)
        self._user = username
        self._password = password
        self.tenant_id = tenant_id
        self.http = http or http_factory()
        self.remaining: int | None = None  # X-Ratelimit-Remaining de la ultima respuesta

    def __repr__(self) -> str:  # nunca el usuario ni la contrasena
        return f"HaciendaClient(environment={self.environment!r}, tenant_id={self.tenant_id})"

    # ---------- token
    def _key(self) -> tuple:
        return (self.tenant_id, self.environment, hashlib.sha256((self._user or "").encode()).hexdigest())

    def _grant(self, data: dict) -> dict:
        try:
            r = self.http.post(self.ep["token"], data=data, timeout=20)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise HaciendaUnavailable("Sin conexión con el servicio de autenticación de Hacienda") from e
        if r.status_code in (400, 401):
            raise HaciendaAuthError(f"Usuario o contraseña de Hacienda incorrectos para el ambiente {self.environment}")
        if r.status_code >= 500 or r.status_code == 429:
            raise HaciendaUnavailable(f"El servicio de autenticación de Hacienda respondió {r.status_code}")
        if r.status_code >= 300:
            raise HaciendaAuthError(f"Hacienda rechazó la autenticación ({r.status_code}) en el ambiente {self.environment}")
        body = r.json()
        now = time.monotonic()
        return {
            "access": body["access_token"],
            "exp": now + max(int(body.get("expires_in") or 300) - 30, 1),
            "refresh": body.get("refresh_token"),
            "refresh_exp": now + max(int(body.get("refresh_expires_in") or 0) - 30, 0),
        }

    def token(self, force: bool = False) -> str:
        key = self._key()
        with _cache_lock:
            cur = None if force else _cache.get(key)
            now = time.monotonic()
            if cur and cur["exp"] > now:
                return cur["access"]
            tok = None
            if cur and cur.get("refresh") and cur["refresh_exp"] > now:
                try:
                    tok = self._grant({"grant_type": "refresh_token", "client_id": self.ep["client_id"], "refresh_token": cur["refresh"]})
                except HaciendaAuthError:
                    tok = None
            if tok is None:
                tok = self._grant({"grant_type": "password", "client_id": self.ep["client_id"], "username": self._user, "password": self._password})
            _cache[key] = tok
            return tok["access"]

    def logout(self) -> None:
        with _cache_lock:
            cur = _cache.pop(self._key(), None)
        if cur and cur.get("refresh"):
            try:
                self.http.post(self.ep["logout"], data={"client_id": self.ep["client_id"], "refresh_token": cur["refresh"]}, timeout=10)
            except (httpx.TimeoutException, httpx.TransportError):
                pass

    def _track(self, r: httpx.Response) -> None:
        rem = r.headers.get("X-Ratelimit-Remaining")
        if rem is not None and str(rem).strip().lstrip("-").isdigit():
            self.remaining = int(rem)
            if self.remaining <= 0:
                _rate["until"] = time.monotonic() + 60

    # ---------- envio
    def post_recepcion(self, body: dict) -> PostResult:
        for intento in (0, 1):
            try:
                tok = self.token(force=intento == 1)
            except HaciendaUnavailable as e:
                return PostResult("retry", cause=str(e))
            try:
                r = self.http.post(self.ep["api"] + "recepcion", json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            except (httpx.TimeoutException, httpx.TransportError):
                return PostResult("retry", cause="Sin respuesta de Hacienda (se reintentará)")
            self._track(r)
            if 200 <= r.status_code < 300:
                return PostResult("ok", location=r.headers.get("Location"))
            if r.status_code == 401 and intento == 0:
                continue
            if r.status_code == 401:
                raise HaciendaAuthError(f"Hacienda rechazó el token en el ambiente {self.environment}")
            cause = (r.headers.get("X-Error-Cause") or "")[:300]
            if r.status_code == 400 and "ya fue recibido" in cause.lower():
                return PostResult("already_received", cause=cause)
            if r.status_code == 400:
                return PostResult("rejected", cause=cause or "Hacienda rechazó el envío (400)")
            if r.status_code == 403:
                return PostResult("rejected", cause=f"Hacienda negó el acceso: revise que las credenciales sean del ambiente {self.environment}")
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                return PostResult("retry", cause="Hacienda pidió esperar (429)", retry_after=int(ra) if ra and ra.isdigit() else 60)
            if r.status_code >= 500:
                return PostResult("retry", cause=f"Hacienda respondió {r.status_code} (se reintentará)")
            return PostResult("rejected", cause=cause or f"Hacienda respondió {r.status_code}")
        return PostResult("retry", cause="Sin respuesta de Hacienda")

    # ---------- consulta
    def get_estado(self, ref: str) -> StatusResult:
        for intento in (0, 1):
            try:
                tok = self.token(force=intento == 1)
            except HaciendaUnavailable as e:
                return StatusResult("retry", detail=str(e))
            try:
                r = self.http.get(self.ep["api"] + "recepcion/" + ref, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            except (httpx.TimeoutException, httpx.TransportError):
                return StatusResult("retry", detail="Sin respuesta de Hacienda")
            self._track(r)
            if r.status_code == 401 and intento == 0:
                continue
            if r.status_code == 401:
                raise HaciendaAuthError(f"Hacienda rechazó el token en el ambiente {self.environment}")
            if r.status_code == 404:
                return StatusResult("not_found")
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                return StatusResult("retry", retry_after=int(ra) if ra and ra.isdigit() else 60)
            if r.status_code >= 500 or r.status_code >= 300:
                return StatusResult("retry", detail=f"Hacienda respondió {r.status_code}")
            body = r.json() if r.content else {}
            state = str(body.get("ind-estado") or body.get("indEstado") or "").lower()
            raw = body.get("respuesta-xml") or body.get("respuestaXml")
            xml = None
            if raw:
                try:
                    xml = base64.b64decode(raw).decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    xml = None
            kind = {"recibido": "procesando", "procesando": "procesando", "aceptado": "aceptado", "rechazado": "rechazado", "error": "error"}.get(state, "procesando")
            return StatusResult(kind, xml=xml, detail=_detail_from(xml))
        return StatusResult("retry")

    def activities_lookup(self, identificacion: str) -> list[dict]:  # pragma: no cover - usa la funcion de modulo
        return lookup_activities(identificacion, self.http)


def lookup_activities(identificacion: str, http: httpx.Client | None = None) -> list[dict]:
    """Consulta publica de actividades registradas (sin credenciales). Lanza HaciendaUnavailable si falla."""
    from .endpoints import ACTIVITIES_URL

    client = http or http_factory()
    try:
        r = client.get(ACTIVITIES_URL, params={"identificacion": identificacion}, timeout=10)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise HaciendaUnavailable("No se pudo consultar Hacienda") from e
    if r.status_code != 200:
        raise HaciendaUnavailable(f"Hacienda respondió {r.status_code} a la consulta de actividades")
    body = r.json() or {}
    out = []
    for a in body.get("actividades") or []:
        code = str(a.get("codigo") or "").strip()
        if not code:
            continue
        out.append({"code": code, "description": a.get("descripcion"), "registered": str(a.get("estado") or "A").upper() == "A"})
    return out
