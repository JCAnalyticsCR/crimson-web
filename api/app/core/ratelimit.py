"""Limite de intentos por clave (IP) en ventana deslizante. En memoria: basta con una replica de la API.
Con varias replicas habria que moverlo a Redis (misma interfaz). La IP es la real del cliente (core/clientip.py)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status


class SlidingWindow:
    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds = limit, seconds
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = Lock()

    def hit(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.seconds:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.seconds - (now - q[0])) + 1
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados intentos. Espere unos minutos.", headers={"Retry-After": str(retry)})
            q.append(now)
            if len(self.hits) > 50_000:  # higiene: no crecer sin limite
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > self.seconds][:10_000]:
                    self.hits.pop(k, None)

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()


class FailureLimiter:
    """Cuenta solo FALLOS por clave, con espera progresiva. Los primeros `free` fallos no esperan; desde ahi cada
    intento nuevo espera base * 2^(fallos - free) segundos desde el ultimo fallo (tope `cap`). La clave se olvida
    `forget` segundos despues de su ultimo fallo, o al acertar (clear)."""

    def __init__(self, free: int, base: int, cap: int, forget: int):
        self.free, self.base, self.cap, self.forget = free, base, cap, forget
        self.state: dict[str, tuple[int, float]] = {}  # clave -> (fallos, momento del ultimo fallo)
        self.lock = Lock()
        self.clock = time.monotonic  # las pruebas lo reemplazan para simular la espera

    def _live(self, key: str, now: float) -> tuple[int, float]:
        n, last = self.state.get(key, (0, 0.0))
        if n and now - last > self.forget:
            self.state.pop(key, None)
            return 0, 0.0
        return n, last

    def wait(self, key: str) -> int:
        """Segundos que faltan para poder intentar de nuevo (0 = puede)."""
        now = self.clock()
        with self.lock:
            n, last = self._live(key, now)
            if n < self.free:
                return 0
            pause = min(self.cap, self.base * 2 ** (n - self.free))
            return max(0, int(pause - (now - last)) + 1) if now - last < pause else 0

    def check(self, key: str, message: str) -> None:
        w = self.wait(key)
        if w:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, message.format(espera=_espera(w)), headers={"Retry-After": str(w)})

    def fail(self, key: str) -> int:
        now = self.clock()
        with self.lock:
            n, _ = self._live(key, now)
            self.state[key] = (n + 1, now)
            if len(self.state) > 50_000:  # higiene: no crecer sin limite
                for k in [k for k, (_, t) in self.state.items() if now - t > self.forget][:10_000]:
                    self.state.pop(k, None)
            return n + 1

    def count(self, key: str) -> int:
        with self.lock:
            return self._live(key, self.clock())[0]

    def clear(self, key: str) -> None:
        with self.lock:
            self.state.pop(key, None)

    def reset(self) -> None:
        with self.lock:
            self.state.clear()


def _espera(seg: int) -> str:
    return f"{seg} segundos" if seg < 90 else f"{-(-seg // 60)} minutos"


class LoginLimiter:
    """Inicio de sesion (plan 1.10). Solo cuentan los intentos FALLIDOS (entrar bien no gasta cupo).

    - Por IP: 20 fallidos seguidos (contra cualquier cuenta) y despues espera progresiva desde 1 min (tope 15 min).
    - Por cuenta + IP: 5 fallidos y despues espera progresiva desde 30 s (tope 15 min). Frena solo a esa IP: 25
      fallidos desde una IP no le impiden entrar a la duena de la cuenta desde otra.
    - Por cuenta (todas las IPs, bloqueo en la base, routers/auth.py): cada IP aporta como mucho ACCOUNT_SHARE
      fallidos, asi que un tercero no puede bloquear la cuenta desde una sola IP.
    """

    ACCOUNT_SHARE = 3

    def __init__(self):
        self.ip = FailureLimiter(free=20, base=60, cap=900, forget=900)
        self.account = FailureLimiter(free=5, base=30, cap=900, forget=3600)

    @staticmethod
    def _acct(email: str, ip: str) -> str:
        import hashlib

        return hashlib.sha256(f"{email.strip().lower()}|{ip}".encode()).hexdigest()[:32]

    def check(self, email: str, ip: str) -> None:
        self.ip.check(ip, "Demasiados intentos fallidos desde esta conexión. Esperá {espera} y probá de nuevo.")
        self.account.check(self._acct(email, ip), "Demasiados intentos fallidos con esta cuenta. Esperá {espera} y probá de nuevo.")

    def fail(self, email: str, ip: str) -> bool:
        """Registra un fallo. Devuelve True si este fallo todavia cuenta para el bloqueo de la cuenta en la base."""
        self.ip.fail(ip)
        return self.account.fail(self._acct(email, ip)) <= self.ACCOUNT_SHARE

    def success(self, email: str, ip: str) -> None:
        self.account.clear(self._acct(email, ip))

    def reset(self) -> None:
        self.ip.reset()
        self.account.reset()


login_limiter = LoginLimiter()
public_limiter = SlidingWindow(limit=30, seconds=600)  # checkouts publicos por IP
webhook_limiter = SlidingWindow(limit=240, seconds=60)  # webhooks de ONVO por empresa: holgado para rafagas reales
# Reporte de incidencias desde la pagina web: una persona real no manda mas de 5 por hora; el tope global
# frena una inundacion desde muchas IPs sin tumbar el resto de la API.
incident_limiter = SlidingWindow(limit=5, seconds=3600)
incident_global_limiter = SlidingWindow(limit=120, seconds=3600)
# "Solicitar levantamiento" (vendedor): cada una es un correo a la oficina. Por empresa y usuario.
survey_request_limiter = SlidingWindow(limit=10, seconds=3600)
# Tienda en linea: cotizar el carrito se llama en cada cambio (generoso); el pedido y la busqueda de "Mi pedido"
# son los que se abusan (adivinar numeros de pedido, inundar con pedidos falsos).
quote_limiter = SlidingWindow(limit=300, seconds=600)
checkout_global_limiter = SlidingWindow(limit=300, seconds=3600)
order_lookup_limiter = SlidingWindow(limit=10, seconds=600)
# Comprobantes de pago: 10 por hora por pedido y por IP (subir el 11.o de OTRO pedido sigue entrando); el tope por
# IP frena a quien prueba con muchos enlaces de pedido
order_proof_limiter = SlidingWindow(limit=10, seconds=3600)
order_proof_ip_limiter = SlidingWindow(limit=60, seconds=3600)
# Solicitudes de acceso al portal del cliente (pagina publica): mismo criterio que las incidencias.
access_request_limiter = SlidingWindow(limit=5, seconds=3600)
access_request_global_limiter = SlidingWindow(limit=120, seconds=3600)
# Disponibilidad de la vitrina: liviana y cacheable, pero se consulta en cada pagina del catalogo
catalog_limiter = SlidingWindow(limit=240, seconds=60)
# "Avisarme cuando haya" (tienda): como un formulario publico
stock_alert_limiter = SlidingWindow(limit=10, seconds=3600)
# Gestion de usuarios: acciones que mandan correo o generan credenciales, por admin
admin_users_limiter = SlidingWindow(limit=30, seconds=3600)
# Portal de aliados (usuarios externos): cada invitacion es un correo con la marca de la empresa (por empresa aliada y
# por dia; revocar y volver a invitar tambien cuenta), cada archivo se guarda en la base (por persona) y cada ticket
# avisa por correo a administracion y supervision (por persona)
ally_invite_limiter = SlidingWindow(limit=20, seconds=86400)
ally_media_limiter = SlidingWindow(limit=120, seconds=3600)
ally_ticket_limiter = SlidingWindow(limit=20, seconds=3600)
# Precios que envia el aliado, respuestas a solicitudes y envios de levantamientos: cada uno avisa por correo a la
# vendedora, a administracion o a los revisores (y llena Aprobaciones). Por persona.
ally_notify_limiter = SlidingWindow(limit=30, seconds=3600)
# Reasignaciones desde el portal de aliados (tecnico de un levantamiento, equipo de una OT): cada una puede escribirle
# "Tiene un trabajo nuevo" a su tecnico desde el remitente de la empresa. Por persona; aparte del tope de avisos a la
# oficina para que repartir el trabajo del dia no le trabe el envio de precios.
ally_assign_limiter = SlidingWindow(limit=60, seconds=3600)
# Reautenticacion con la contrasena del admin (se cuenta cada intento, antes de verificar)
reauth_limiter = SlidingWindow(limit=10, seconds=900)
# Enlaces publicos con token (invitacion, restablecer): revisar y confirmar
token_limiter = SlidingWindow(limit=20, seconds=600)
# "Olvide mi contrasena" (autoservicio)
pwreset_ip_limiter = SlidingWindow(limit=5, seconds=3600)
pwreset_email_limiter = SlidingWindow(limit=3, seconds=3600)  # clave: sha256 del correo; al pasarse se ignora en silencio
pwreset_global_limiter = SlidingWindow(limit=100, seconds=3600)
# Hacienda: "Probar conexion" pide un token al IdP (no abusar) y "Revisar buzon ahora" abre IMAP, por empresa
hacienda_test_limiter = SlidingWindow(limit=5, seconds=900)
inbox_run_limiter = SlidingWindow(limit=4, seconds=300)
