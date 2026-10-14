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
# Solicitudes de acceso al portal del cliente (pagina publica): mismo criterio que las incidencias.
access_request_limiter = SlidingWindow(limit=5, seconds=3600)
access_request_global_limiter = SlidingWindow(limit=120, seconds=3600)
# Disponibilidad de la vitrina: liviana y cacheable, pero se consulta en cada pagina del catalogo
catalog_limiter = SlidingWindow(limit=240, seconds=60)
# "Avisarme cuando haya" (tienda): como un formulario publico
stock_alert_limiter = SlidingWindow(limit=10, seconds=3600)
