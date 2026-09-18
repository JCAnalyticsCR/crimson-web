"""Pasarelas de pago (plan 5.3 y 8). ONVO: Payment Intent + checkout hosteado + webhook firmado; nunca se confirma por redirect.

Este adapter implementa el contrato y la verificacion de firma; las llamadas HTTP reales se activan cuando existan
credenciales (client secret cifrado por tenant). Sin credenciales, create_intent devuelve un intent 'simulado'.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx


@dataclass
class Intent:
    id: str
    checkout_url: str | None
    status: str  # requires_payment | succeeded | failed | simulated
    raw: dict


class OnvoAdapter:
    name = "onvo"
    base = "https://api.onvopay.com/v1"

    def __init__(self, secret_key: str | None, public_key: str | None = None):
        self.secret, self.public = secret_key, public_key

    def create_intent(self, amount_cents: int, currency: str, description: str, metadata: dict, return_url: str) -> Intent:
        if not self.secret:
            return Intent(id=f"sim_{int(time.time())}", checkout_url=None, status="simulated", raw={"note": "sin credenciales ONVO"})
        r = httpx.post(
            f"{self.base}/payment-intents",
            headers={"Authorization": f"Bearer {self.secret}"},
            json={"amount": amount_cents, "currency": currency, "description": description, "metadata": metadata, "returnUrl": return_url},
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        return Intent(id=j.get("id", ""), checkout_url=j.get("checkoutUrl") or j.get("url"), status=j.get("status", "requires_payment"), raw=j)

    def refund(self, intent_id: str, amount_cents: int | None = None) -> dict:
        if not self.secret:
            return {"id": f"sim_refund_{intent_id}", "status": "simulated"}
        r = httpx.post(
            f"{self.base}/refunds",
            headers={"Authorization": f"Bearer {self.secret}"},
            json={"paymentIntentId": intent_id, **({"amount": amount_cents} if amount_cents else {})},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    @staticmethod
    def verify_signature(secret: str, header: str | None, body: bytes, tolerance: int = 300) -> bool:
        """Firma estilo 't=<ts>,v1=<hmac_sha256(ts.body)>' con ventana de 300 s (mismo estandar que exige el plan)."""
        if not header or not secret:
            return False
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        ts, sig = parts.get("t"), parts.get("v1")
        if not ts or not sig or abs(time.time() - int(ts)) > tolerance:
            return False
        expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    @staticmethod
    def sign(secret: str, body: bytes, ts: int | None = None) -> str:
        ts = ts or int(time.time())
        return f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + body, hashlib.sha256).hexdigest()}"
