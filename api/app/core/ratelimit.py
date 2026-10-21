"""Limite de intentos por clave (IP) en ventana deslizante. En memoria: basta con una replica de la API.
Con varias replicas habria que moverlo a Redis (misma interfaz)."""

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


login_limiter = SlidingWindow(limit=20, seconds=600)  # 20 intentos / 10 min por IP
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
order_proof_limiter = SlidingWindow(limit=10, seconds=3600)
# "Pagar con tarjeta" de Mi pedido, por pedido (ademas del limite por IP): cada clic abre una sesion en ONVO
order_card_limiter = SlidingWindow(limit=3, seconds=600)
# Solicitudes de acceso al portal del cliente (pagina publica): mismo criterio que las incidencias.
access_request_limiter = SlidingWindow(limit=5, seconds=3600)
access_request_global_limiter = SlidingWindow(limit=120, seconds=3600)
# Disponibilidad de la vitrina: liviana y cacheable, pero se consulta en cada pagina del catalogo
catalog_limiter = SlidingWindow(limit=240, seconds=60)
# /health/deep (publico, plan 1.8): el monitor pasa cada pocos minutos y el resultado se guarda 30 s (la base ya no se
# consulta en cada llamada); holgado para que un monitor detras del mismo proxy no reciba un 429 falso
health_limiter = SlidingWindow(limit=120, seconds=60)
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
