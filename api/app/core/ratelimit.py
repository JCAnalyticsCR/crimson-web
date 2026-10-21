"""Limite de intentos por clave (IP) en ventana deslizante. En memoria: basta con una replica de la API.
Con varias replicas habria que moverlo a Redis (misma interfaz). La IP es la real del cliente (core/clientip.py); como
clave, una IPv6 cuenta por su red /64 (clientip.ip_key)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status

from .clientip import ip_key


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
    `forget` segundos despues de su ultimo fallo, o al acertar (clear). Como mucho `max_keys` claves en memoria: pasado
    el tope se olvidan primero las vencidas y, si no alcanza, las de fallo mas viejo (quien ataca ahora queda entre las
    recientes)."""

    def __init__(self, free: int, base: int, cap: int, forget: int, max_keys: int = 50_000):
        self.free, self.base, self.cap, self.forget, self.max_keys = free, base, cap, forget, max_keys
        self.state: dict[str, tuple[int, float]] = {}  # clave -> (fallos, momento del ultimo fallo)
        self.lock = Lock()
        self.clock = time.monotonic  # las pruebas lo reemplazan para simular la espera

    def _trim(self, now: float) -> None:
        """Con el lock tomado: no crecer sin limite."""
        if len(self.state) <= self.max_keys:
            return
        for k in [k for k, (_, t) in self.state.items() if now - t > self.forget]:
            self.state.pop(k, None)
        sobran = len(self.state) - int(self.max_keys * 0.8)
        if len(self.state) > self.max_keys and sobran > 0:
            for k, _ in sorted(self.state.items(), key=lambda kv: kv[1][1])[:sobran]:
                self.state.pop(k, None)

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
            self._trim(now)
            return n + 1

    def count(self, key: str) -> int:
        with self.lock:
            return self._live(key, self.clock())[0]

    def flagged(self, key: str) -> int:
        """Segundos que faltan para que la clave se olvide, si ya paso los fallos libres (0 = no los paso)."""
        now = self.clock()
        with self.lock:
            n, last = self._live(key, now)
            return int(self.forget - (now - last)) + 1 if n and n >= self.free else 0

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

    - Por IP: 20 fallidos (contra cualquier cuenta; se olvidan 15 min despues del ultimo) y despues espera progresiva
      desde 1 min (tope 15 min). Acertar no lo limpia: detras de una IP puede haber una oficina entera.
    - Por cuenta + IP: 5 fallidos y despues espera progresiva desde 30 s (tope 15 min). Frena solo a esa IP: 25
      fallidos seguidos desde una IP no le impiden entrar a la duena de la cuenta desde otra.
    - Por cuenta (todas las IPs): cuenta TODOS los fallidos (tambien los codigos 2FA equivocados) y se olvida
      ACCOUNT_FORGET despues del ultimo. Pasados ACCOUNT_FREE, quien NO es de confianza para esa cuenta (routers/auth.py:
      ni la cookie de equipo de la cuenta ni la IP de una sesion abierta) espera entre intentos desde 30 s hasta
      ACCOUNT_CAP (tope corto, nunca un bloqueo): el total de intentos contra una cuenta queda acotado (unos 60 por
      hora) aunque el atacante use muchas IPs, y un tercero que lo dispara una y otra vez no deja afuera a la duena: a
      lo sumo la hace esperar ACCOUNT_CAP desde un equipo nuevo, y desde los suyos no espera. Igual para un correo
      que no existe (no revela cuales existen). Acertar no lo limpia (si no, cada vez que la duena entra le devolveria
      margen al atacante); cambiar la contrasena si (forget_account): los fallidos eran contra la anterior.
    - Por equipo (la cookie de equipo de quien ya entro desde ahi): 5 fallidos y despues espera progresiva desde 30 s
      (tope 15 min). Una cookie robada no da intentos sin limite repartidos entre muchas IPs.
    Las IPv6 cuentan por su red /64 (clientip.ip_key).
    """

    ACCOUNT_FREE = 10
    ACCOUNT_CAP = 60
    ACCOUNT_FORGET = 3600

    def __init__(self):
        self.ip = FailureLimiter(free=20, base=60, cap=900, forget=900)
        self.account = FailureLimiter(free=5, base=30, cap=900, forget=3600)
        self.total = FailureLimiter(free=self.ACCOUNT_FREE, base=30, cap=self.ACCOUNT_CAP, forget=self.ACCOUNT_FORGET)
        self.device = FailureLimiter(free=5, base=30, cap=900, forget=3600)

    @staticmethod
    def _h(text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode()).hexdigest()[:32]

    @classmethod
    def _acct(cls, email: str, ip: str) -> str:
        return cls._h(f"{email.strip().lower()}|{ip_key(ip)}")

    @classmethod
    def _total(cls, email: str) -> str:
        return cls._h(f"{email.strip().lower()}|*")

    @classmethod
    def _dev(cls, device: str) -> str:
        return cls._h(f"equipo|{device}")

    def check(self, email: str, ip: str) -> None:
        """Por IP y por cuenta + IP (no necesitan la base)."""
        self.ip.check(ip_key(ip), "Demasiados intentos fallidos desde esta conexión. Esperá {espera} y probá de nuevo.")
        self.account.check(self._acct(email, ip), "Demasiados intentos fallidos con esta cuenta. Esperá {espera} y probá de nuevo.")

    def check_device(self, device: str) -> None:
        self.device.check(self._dev(device), "Demasiados intentos fallidos desde este equipo. Esperá {espera} y probá de nuevo.")

    def account_wait(self, email: str) -> int:
        """Segundos que espera quien no es de confianza para la cuenta (0 = puede intentar)."""
        return self.total.wait(self._total(email))

    def check_account(self, email: str) -> None:
        self.total.check(
            self._total(email),
            "Hubo muchos intentos fallidos con esta cuenta. Esperá {espera} y probá de nuevo; desde un equipo donde ya entraste no hace falta esperar.",
        )

    def fail(self, email: str, ip: str, device: str | None = None) -> int:
        """Registra un fallo. Devuelve los fallidos de la cuenta desde todas las IPs."""
        self.ip.fail(ip_key(ip))
        self.account.fail(self._acct(email, ip))
        if device:
            self.device.fail(self._dev(device))
        return self.total.fail(self._total(email))

    def account_failures(self, email: str) -> int:
        return self.total.count(self._total(email))

    def account_flagged(self, email: str) -> int:
        """Segundos que la cuenta sigue con muchos fallidos (desde un equipo que no es de confianza se espera entre
        intentos); 0 = normal. Para Ajustes -> Usuarios."""
        return self.total.flagged(self._total(email))

    def forget_account(self, email: str) -> None:
        """La contrasena cambio (o un admin la restablecio): los fallidos de la cuenta eran contra la anterior."""
        self.total.clear(self._total(email))

    def success(self, email: str, ip: str, device: str | None = None) -> None:
        self.account.clear(self._acct(email, ip))
        if device:
            self.device.clear(self._dev(device))

    def reset(self) -> None:
        self.ip.reset()
        self.account.reset()
        self.total.reset()
        self.device.reset()


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
