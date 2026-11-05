"""Cliente del IdP y la API de recepcion de Hacienda (token, envio, consulta).

- Token solo en memoria del proceso (nunca Redis ni la base), por (empresa, ambiente, sha256(usuario)).
- Guarda de produccion en el propio cliente: sin HACIENDA_PRODUCTION_ALLOWED, ENV=production y la huella de instancia
  guardada al activar, el constructor de un cliente de produccion lanza ProductionBlocked. Ninguna ruta de codigo puede
  hablar con produccion sin las tres llaves.
- httpx sale de http_factory(): las pruebas lo reemplazan por un transporte que falla ante cualquier peticion no simulada.
- Guarda de ambiente por URL (_guard): un cliente que no es de produccion nunca habla con una URL de produccion
  (endpoints.is_production_url, por host exacto), ni aunque alguien cambie ENDPOINTS por error.
- Ningun log con cuerpo, token, Authorization, usuario ni contrasena.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
import unicodedata
from dataclasses import dataclass

import httpx

from ...core.config import settings
from .endpoints import for_env, is_production_url

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
    http_status: int | None = None  # codigo HTTP de la respuesta (None: no hubo respuesta)
    gateway: bool = False  # 403 de la entrada (gateway de AWS) de Hacienda: el envio no llego a Hacienda


@dataclass
class StatusResult:
    kind: str  # procesando | aceptado | rechazado | error | not_found | retry
    xml: str | None = None
    detail: str | None = None
    retry_after: int | None = None
    http_status: int | None = None
    gateway: bool = False
    cause: str | None = None  # X-Error-Cause cruda (recortada a 300)


@dataclass
class ProbeResult:
    """Resultado de probe_estado: status ok | aviso | falla; motivo ruta | acceso | token | credenciales | esperar |
    sin_respuesta | redireccion | None. detail legible, sin secretos."""

    status: str
    motivo: str | None
    detail: str
    http_status: int | None = None


def _gateway_type(r: httpx.Response, *, body: bool) -> str | None:
    """Tipo del error del gateway de AWS que esta delante de la API (x-amzn-ErrorType o, en la consulta, el cuerpo)."""
    h = (r.headers.get("x-amzn-ErrorType") or "").strip()
    if h:
        return h.split(":", 1)[0][:80] or "gateway"
    if body:
        try:
            txt = r.text[:2000]
        except Exception:  # noqa: BLE001
            txt = ""
        for marca in ("IncompleteSignature", "MissingAuthenticationToken"):
            if marca in txt:
                return marca + "Exception"
    return None


def not_received(st: StatusResult, ref: str) -> bool:
    """La consulta de una clave que Hacienda todavia no tiene: el sandbox responde 400 con X-Error-Cause
    "El comprobante <clave> no ha sido recibido" en vez de 404. Solo con una clave de 50 digitos (no la ref del mensaje
    receptor). get_estado lo sigue devolviendo como 'retry' (el worker no cambia): esto lo usan la sonda de «Probar
    conexion», la prueba real y el smoke."""
    if st.http_status != 400 or not (len(ref or "") == 50 and (ref or "").isdigit()):
        return False
    raw = unicodedata.normalize("NFKD", st.cause or st.detail or "")
    plano = "".join(ch for ch in raw if not unicodedata.combining(ch)).lower()
    return "no ha sido recibido" in plano


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
        for k in ("token", "logout", "api"):
            self._guard(self.ep[k])
        self._user = username
        self._password = password
        self.tenant_id = tenant_id
        self.http = http or http_factory()
        self.remaining: int | None = None  # X-Ratelimit-Remaining de la ultima respuesta

    def __repr__(self) -> str:  # nunca el usuario ni la contrasena
        return f"HaciendaClient(environment={self.environment!r}, tenant_id={self.tenant_id})"

    def _guard(self, url: str) -> None:
        """La llave que bloquea de verdad: fuera de produccion, ninguna peticion a una URL de produccion."""
        if self.environment != "produccion" and is_production_url(url):
            raise ProductionBlocked(f"El cliente de {self.environment} no puede hablar con producción de Hacienda")

    # ---------- token
    def _key(self) -> tuple:
        return (self.tenant_id, self.environment, hashlib.sha256((self._user or "").encode()).hexdigest())

    def _grant(self, data: dict) -> dict:
        self._guard(self.ep["token"])
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
            self._guard(self.ep["logout"])
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
            url = self.ep["api"] + "recepcion"
            self._guard(url)
            try:
                r = self.http.post(url, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            except (httpx.TimeoutException, httpx.TransportError):
                return PostResult("retry", cause="Sin respuesta de Hacienda (se reintentará)")
            self._track(r)
            sc = r.status_code
            if 200 <= sc < 300:
                return PostResult("ok", location=r.headers.get("Location"), http_status=sc)
            if sc == 401 and intento == 0:
                continue
            if sc == 401:
                raise HaciendaAuthError(f"Hacienda rechazó el token en el ambiente {self.environment}")
            cause = (r.headers.get("X-Error-Cause") or "")[:300]
            if sc == 400 and "ya fue recibido" in cause.lower():
                return PostResult("already_received", cause=cause, http_status=sc)
            if sc == 400:
                return PostResult("rejected", cause=cause or "Hacienda rechazó el envío (400)", http_status=sc)
            if sc == 403:
                gw = _gateway_type(r, body=False)
                if gw:
                    # la semantica fiscal no cambia (sigue 'rejected'): solo el texto y las banderas
                    return PostResult(
                        "rejected",
                        cause=f"La entrada de Hacienda rechazó el envío antes de recibirlo (403 {gw}): el comprobante no llegó. No es un problema de credenciales; avise a soporte.",
                        http_status=sc,
                        gateway=True,
                    )
                return PostResult("rejected", cause=f"Hacienda negó el acceso: revise que las credenciales sean del ambiente {self.environment}", http_status=sc)
            if sc == 429:
                ra = r.headers.get("Retry-After")
                return PostResult("retry", cause="Hacienda pidió esperar (429)", retry_after=int(ra) if ra and ra.isdigit() else 60, http_status=sc)
            if sc >= 500:
                return PostResult("retry", cause=f"Hacienda respondió {sc} (se reintentará)", http_status=sc)
            return PostResult("rejected", cause=cause or f"Hacienda respondió {sc}", http_status=sc)
        return PostResult("retry", cause="Sin respuesta de Hacienda")

    # ---------- consulta
    def get_estado(self, ref: str) -> StatusResult:
        for intento in (0, 1):
            try:
                tok = self.token(force=intento == 1)
            except HaciendaUnavailable as e:
                return StatusResult("retry", detail=str(e))
            url = self.ep["api"] + "recepcion/" + ref
            self._guard(url)
            try:
                r = self.http.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=20)
            except (httpx.TimeoutException, httpx.TransportError):
                return StatusResult("retry", detail="Sin respuesta de Hacienda")
            self._track(r)
            sc = r.status_code
            if sc == 401 and intento == 0:
                continue
            if sc == 401:
                raise HaciendaAuthError(f"Hacienda rechazó el token en el ambiente {self.environment}")
            if sc == 404:
                return StatusResult("not_found", http_status=sc)
            if sc == 429:
                ra = r.headers.get("Retry-After")
                return StatusResult("retry", retry_after=int(ra) if ra and ra.isdigit() else 60, http_status=sc)
            if sc == 400:
                # la semantica de siempre para el worker: 'retry'. Leer "no ha sido recibido" es cosa de not_received()
                cause = (r.headers.get("X-Error-Cause") or "")[:300] or None
                return StatusResult("retry", detail=cause or "Hacienda respondió 400", http_status=sc, cause=cause)
            if sc == 403:
                # sin retry_after: hacienda_sync._apply_status no lo lee en la rama retry; en el flujo fiscal el 403 sigue
                # el ritmo normal de consultas y lo acota el GIVE_UP de 72 h
                gw = _gateway_type(r, body=True)
                cause = (r.headers.get("X-Error-Cause") or "")[:300] or None
                if gw:
                    return StatusResult(
                        "retry",
                        detail=f"La entrada de Hacienda rechazó la consulta antes de revisarla (403 {gw}): la dirección de consulta no es la que Hacienda atiende.",
                        http_status=sc,
                        gateway=True,
                        cause=cause,
                    )
                return StatusResult("retry", detail="Hacienda negó el acceso a la consulta (403)", http_status=sc, cause=cause)
            if sc >= 300:
                return StatusResult("retry", detail=f"Hacienda respondió {sc}", http_status=sc)
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
            return StatusResult(kind, xml=xml, detail=_detail_from(xml), http_status=sc)
        return StatusResult("retry")

    # ---------- sonda de «Probar conexion» (nunca lanza)
    def probe_estado(self, clave: str) -> ProbeResult:
        """Consulta de estado de una clave que no existe: confirma que la ruta de consulta del ambiente es la que Hacienda
        atiende, sin enviar nada. NUNCA propaga una excepcion ni registra cabeceras o el token."""
        try:
            self.token()
        except HaciendaAuthError as e:
            return ProbeResult("falla", "credenciales", str(e))
        except HaciendaUnavailable as e:
            return ProbeResult("aviso", "sin_respuesta", str(e))
        except ProductionBlocked as e:
            return ProbeResult("falla", "ruta", str(e))
        except Exception as e:  # noqa: BLE001
            return ProbeResult("aviso", None, f"No se pudo iniciar sesión ({type(e).__name__})")
        try:
            st = self.get_estado(clave)  # ya reintenta un 401 pidiendo otro token
        except HaciendaAuthError:
            return ProbeResult("falla", "token", "Hacienda no aceptó el token para consultar comprobantes (401)", 401)
        except ProductionBlocked as e:
            return ProbeResult("falla", "ruta", str(e))
        except Exception as e:  # noqa: BLE001
            return ProbeResult("aviso", None, f"La consulta falló ({type(e).__name__})")
        hs = st.http_status
        if st.kind == "not_found" or not_received(st, clave) or (hs is not None and 200 <= hs < 300):
            return ProbeResult("ok", None, "Hacienda respondió la consulta", hs)
        if st.gateway:
            return ProbeResult("falla", "ruta", st.detail or "La entrada de Hacienda rechazó la consulta (403)", hs)
        if hs == 403:
            return ProbeResult("falla", "acceso", st.detail or "Hacienda negó el acceso a la consulta (403)", hs)
        if hs == 400:
            return ProbeResult("aviso", None, st.cause or st.detail or "Hacienda respondió 400", hs)
        if hs == 429:
            return ProbeResult("aviso", "esperar", "Hacienda pidió esperar (429)", hs)
        if hs is not None and 300 <= hs < 400:
            return ProbeResult("aviso", "redireccion", f"Hacienda respondió una redirección ({hs}) que no se siguió", hs)
        return ProbeResult("aviso", "sin_respuesta", st.detail or "Sin respuesta de Hacienda", hs)

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
