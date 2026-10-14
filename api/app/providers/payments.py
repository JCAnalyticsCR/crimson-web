"""Pasarela ONVO Pay (plan 5.3 y 8), segun la documentacion oficial (docs.onvopay.com, llms-full.txt y openapi.yaml,
revisada el 2026-10-13). Lo que la doc fija y este adapter respeta:

- Llaves: `onvo_{test|live}_secret_key_...` (servidor) y `onvo_{test|live}_publishable_key_...` (navegador). El modo
  (pruebas o produccion) lo define la llave, no un ajuste aparte.
- Cobro: sesion de Checkout de un solo uso, POST /v1/checkout/sessions/one-time-link -> `url` alojada por ONVO.
  POST /v1/payment-intents NO devuelve pagina de pago (solo sirve con un metodo de pago ya tokenizado).
- Montos enteros en la unidad menor (centimos de colon / centavos de dolar). `metadata`: solo strings (<=40/500 car.).
- Webhook: ONVO manda el secreto del webhook tal cual en el header `X-Webhook-Secret` (sin HMAC ni marca de tiempo,
  o sea repetible). Por eso ningun pago se aplica sin volver a consultar el objeto a ONVO con la llave secreta.
- Eventos: checkout-session.succeeded, payment-intent.succeeded | failed | deferred. No hay evento de reembolso:
  POST /v1/refunds devuelve el resultado en la respuesta.

Sin llave secreta, create_checkout devuelve un intento 'simulated' (solo se ofrece fuera de produccion).
"""

from __future__ import annotations

import hmac
import re
import time
from dataclasses import dataclass

import httpx

KEY_RE = re.compile(r"^onvo_(test|live)_(secret|publishable)_key_\S{8,}$")


class OnvoError(Exception):
    """Respuesta de error de ONVO (o falla de red: status 0). `message` es apto para el log, nunca trae la llave."""

    def __init__(self, status: int, message: str):
        super().__init__(f"ONVO {status}: {message}")
        self.status, self.message = status, message


@dataclass
class Intent:
    id: str
    checkout_url: str | None
    status: str  # unpaid | paid | simulated
    raw: dict


def key_mode(key: str | None) -> str | None:
    m = KEY_RE.match(key or "")
    return m.group(1) if m else None


def key_problem(key: str, kind: str) -> str | None:
    """Mensaje legible si `key` no es una llave de ONVO del tipo esperado (secret | publishable)."""
    m = KEY_RE.match(key or "")
    if m and m.group(2) == kind:
        return None
    if m:
        return (
            "Esa es la llave publicable: en este campo va la llave secreta (onvo_test_secret_key_… u onvo_live_secret_key_…)"
            if kind == "secret"
            else "Esa es la llave secreta: no la pongas en el campo publicable (ese valor llega al navegador)"
        )
    if (key or "").startswith("webhook_secret_"):
        return "Ese es el secreto del webhook: va en su propio campo"
    return f"La llave {'secreta' if kind == 'secret' else 'publicable'} de ONVO empieza con onvo_test_{kind}_key_ u onvo_live_{kind}_key_"


def webhook_secret_problem(secret: str) -> str | None:
    if KEY_RE.match(secret or ""):
        return "Ese valor es una llave de API, no el secreto del webhook (ONVO → Desarrolladores → Webhooks)"
    if len(secret or "") < 16:
        return "El secreto del webhook es muy corto: copialo completo desde ONVO → Desarrolladores → Webhooks"
    return None


def cr_phone(raw: str | None) -> str | None:
    """ONVO pide el telefono con codigo de pais (+50688880000). Si no se puede normalizar, no se manda."""
    n = re.sub(r"\D", "", raw or "")
    if len(n) == 8:
        return f"+506{n}"
    if len(n) == 11 and n.startswith("506"):
        return f"+{n}"
    return None


class OnvoAdapter:
    name = "onvo"
    base = "https://api.onvopay.com/v1"

    def __init__(self, secret_key: str | None, public_key: str | None = None):
        self.secret, self.public = secret_key, public_key

    @property
    def mode(self) -> str | None:
        return key_mode(self.secret)

    def _req(self, method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict:
        try:
            r = httpx.request(
                method,
                f"{self.base}{path}",
                headers={"Authorization": f"Bearer {self.secret}", "Accept": "application/json"},
                json=json,
                params=params,
                timeout=20,
            )
        except httpx.HTTPError as e:
            raise OnvoError(0, f"sin respuesta de ONVO ({type(e).__name__})") from None
        if r.status_code >= 400:
            try:
                j = r.json()
                msg = j.get("message") or j.get("error") or r.reason_phrase
                msg = "; ".join(map(str, msg)) if isinstance(msg, list) else str(msg)
                if j.get("code"):
                    msg = f"{j['code']}: {msg}"
            except ValueError:
                msg = r.reason_phrase
            raise OnvoError(r.status_code, msg[:300])
        return r.json()

    def create_checkout(
        self,
        *,
        amount_cents: int,
        currency: str,
        description: str,
        metadata: dict,
        redirect_url: str,
        cancel_url: str,
        customer: dict | None = None,
    ) -> Intent:
        """Sesion de Checkout de un solo uso por el monto exacto (una linea: el total ya trae IVA, envio y descuentos)."""
        if not self.secret:
            return Intent(id=f"sim_{int(time.time())}", checkout_url=None, status="simulated", raw={"note": "sin credenciales ONVO"})
        body: dict = {
            "lineItems": [{"quantity": 1, "unitAmount": int(amount_cents), "currency": currency, "description": description[:250]}],
            "redirectUrl": redirect_url,
            "cancelUrl": cancel_url,
            "metadata": {str(k)[:40]: str(v)[:500] for k, v in metadata.items() if v is not None},
        }
        c = customer or {}
        if c.get("name"):
            body["customerName"] = str(c["name"])[:120]
        if c.get("email") and "@" in str(c["email"]):
            body["customerEmail"] = str(c["email"])[:200]
        if phone := cr_phone(c.get("phone")):
            body["customerPhone"] = phone
        j = self._req("POST", "/checkout/sessions/one-time-link", json=body)
        return Intent(id=str(j.get("id") or ""), checkout_url=j.get("url"), status=str(j.get("paymentStatus") or "unpaid"), raw=j)

    def get_checkout(self, session_id: str) -> dict:
        return self._req("GET", f"/checkout/sessions/{session_id}")

    def get_intent(self, intent_id: str) -> dict:
        return self._req("GET", f"/payment-intents/{intent_id}")

    def refund(self, intent_id: str, amount_cents: int | None = None, reason: str = "requested_by_customer", description: str | None = None) -> dict:
        if not self.secret:
            return {"id": f"sim_refund_{intent_id}", "status": "simulated"}
        body: dict = {"paymentIntentId": intent_id, "reason": reason}
        if amount_cents:
            body["amount"] = int(amount_cents)
        if description:
            body["description"] = description[:250]
        return self._req("POST", "/refunds", json=body)

    def ping(self) -> dict:
        """Lectura minima para probar la llave (lista 1 intencion de pago; no crea nada)."""
        return self._req("GET", "/payment-intents/account", params={"limit": 1})

    @staticmethod
    def verify_webhook(expected: str | None, received: str | None) -> bool:
        """Compara el header X-Webhook-Secret con el secreto guardado, en tiempo constante."""
        if not expected or not received:
            return False
        return hmac.compare_digest(expected.encode(), received.strip().encode())
