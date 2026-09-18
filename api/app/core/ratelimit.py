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
