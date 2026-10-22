"""Correo del comprador en el checkout: que se pueda escribir a esa direccion antes de confirmar la compra (pedido 21/10,
item 32: "verificar que el correo del cliente es real, de Gmail u otro proveedor existente").

Tres capas, de la mas barata a la mas cara:
1. Formato (email-validator, sin red).
2. Errores de dedo conocidos de los proveedores grandes (gmial.com, hotmial.com...): se pide corregir y se sugiere
   la direccion buena. Un dominio mal escrito puede existir (hay quien los registra para recoger correo ajeno), asi
   que estos no se consultan: se corrigen.
3. DNS: el dominio tiene que existir y tener registro MX. Consulta con tope corto; si el DNS no responde (caido,
   lento, sin configuracion) NO se bloquea la compra: la verificacion es una ayuda, no una puerta.

No se le escribe al buzon (nada de SMTP "VRFY" ni correos de prueba): solo se mira el dominio.
"""

from __future__ import annotations

import threading
import time
from difflib import get_close_matches

from email_validator import EmailNotValidError, validate_email

DNS_TIMEOUT = 2.5  # segundos para toda la consulta (el comprador espera al tocar "Continuar")

# proveedores comunes en Costa Rica: base de las sugerencias por parecido
COMMON_DOMAINS = (
    "gmail.com",
    "hotmail.com",
    "hotmail.es",
    "outlook.com",
    "outlook.es",
    "live.com",
    "msn.com",
    "yahoo.com",
    "yahoo.es",
    "icloud.com",
    "me.com",
    "protonmail.com",
    "proton.me",
    "racsa.co.cr",
    "ice.co.cr",
)

# errores de dedo que nadie usa de verdad: se corrigen sin consultar el DNS
TYPOS = {
    **dict.fromkeys(
        (
            "gmial.com",
            "gmai.com",
            "gmal.com",
            "gmaill.com",
            "gmali.com",
            "gamil.com",
            "gnail.com",
            "gmil.com",
            "gmail.co",
            "gmail.con",
            "gmail.cm",
            "gmail.om",
            "gmail.comm",
            "gmail.cmo",
            "gmailcom",
        ),
        "gmail.com",
    ),
    **dict.fromkeys(
        (
            "hotmial.com",
            "hotmal.com",
            "hotmai.com",
            "hotmil.com",
            "hotamil.com",
            "hotmali.com",
            "hotnail.com",
            "homail.com",
            "hotmail.co",
            "hotmail.con",
            "hotmail.cm",
            "hotmail.comm",
            "hotmail.cmo",
            "hotmailcom",
        ),
        "hotmail.com",
    ),
    **dict.fromkeys(("outlok.com", "outllok.com", "otulook.com", "outloo.com", "outlook.co", "outlook.con", "outlook.cm", "outlookcom"), "outlook.com"),
    **dict.fromkeys(("yaho.com", "yahooo.com", "yhoo.com", "yahoo.co", "yahoo.con", "yahoo.cm", "yahoocom"), "yahoo.com"),
    **dict.fromkeys(("iclod.com", "icoud.com", "icloud.co", "icloud.con"), "icloud.com"),
}
# terminaciones mal escritas de cualquier dominio: solo se sugieren (el dominio puede existir igual)
_TLD_FIX = ((".con", ".com"), (".cmo", ".com"), (".comm", ".com"), (".vom", ".com"), (".xom", ".com"), (".co.c", ".co.cr"))

# cache por proceso: el mismo dominio no se consulta en cada clic (gmail.com, hotmail.com...)
_TTL = {"ok": 6 * 3600, "nomx": 1800, "nodomain": 1800}
_MAX_CACHE = 5000
_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _query_mx(domain: str) -> str:
    """'ok' | 'nomx' | 'nodomain' | 'unknown'. Las pruebas la reemplazan (nunca salen a la red)."""
    import dns.exception
    import dns.resolver

    try:
        r = dns.resolver.Resolver()
        r.timeout = DNS_TIMEOUT
        r.lifetime = DNS_TIMEOUT
        ans = r.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN:
        return "nodomain"
    except dns.resolver.NoAnswer:
        return "nomx"
    except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.resolver.NoResolverConfiguration):
        return "unknown"
    except Exception:  # cualquier otra falla del DNS: no se bloquea al comprador
        return "unknown"
    # MX nulo (RFC 7505: "0 ."): el dominio declara que no recibe correo
    if not any(str(x.exchange).strip(".") for x in ans):
        return "nomx"
    return "ok"


def mx_status(domain: str) -> str:
    domain = domain.strip().lower().rstrip(".")
    now = time.monotonic()
    with _lock:
        hit = _cache.get(domain)
        if hit and hit[1] > now:
            return hit[0]
    st = _query_mx(domain)
    if st in _TTL:  # "unknown" no se guarda: el siguiente intento vuelve a preguntar
        with _lock:
            if len(_cache) >= _MAX_CACHE:
                _cache.clear()
            _cache[domain] = (st, now + _TTL[st])
    return st


def suggest_domain(domain: str) -> str | None:
    d = domain.lower()
    if d in TYPOS:
        return TYPOS[d]
    if d in COMMON_DOMAINS:
        return None
    for bad, good in _TLD_FIX:
        if d.endswith(bad):
            return d[: -len(bad)] + good
    near = get_close_matches(d, COMMON_DOMAINS, n=1, cutoff=0.84)
    return near[0] if near else None


def check_email(raw: str | None, dns: bool = True) -> dict:
    """{"ok", "reason", "message", "suggestion", "email"}. reason: formato | error_de_dedo | dominio | sin_correo | None.
    ok=True con DNS que no respondio (reason None): no se bloquea por una falla nuestra o del DNS."""
    s = (raw or "").strip()
    try:
        v = validate_email(s, check_deliverability=False)
    except EmailNotValidError:
        return {"ok": False, "reason": "formato", "message": "Escribí un correo válido: ahí te llega la confirmación", "suggestion": None, "email": s}
    local, domain = v.local_part, v.domain.lower()
    email = f"{local}@{domain}"
    fix = suggest_domain(domain)
    sug = f"{local}@{fix}" if fix else None
    if domain in TYPOS:
        return {
            "ok": False,
            "reason": "error_de_dedo",
            "message": f"¿Quisiste decir {sug}? Revisá el correo antes de seguir.",
            "suggestion": sug,
            "email": email,
        }
    if not dns:
        return {"ok": True, "reason": None, "message": None, "suggestion": sug, "email": email}
    st = mx_status(domain)
    if st == "nodomain":
        msg = f"El dominio «{domain}» no existe" + (f". ¿Quisiste decir {sug}?" if sug else ". Revisá el correo.")
        return {"ok": False, "reason": "dominio", "message": msg, "suggestion": sug, "email": email}
    if st == "nomx":
        msg = f"«{domain}» no recibe correos" + (f". ¿Quisiste decir {sug}?" if sug else ". Revisá el correo.")
        return {"ok": False, "reason": "sin_correo", "message": msg, "suggestion": sug, "email": email}
    # existe (o el DNS no respondio): pasa; si se parece mucho a uno comun, solo se sugiere
    return {"ok": True, "reason": None, "message": None, "suggestion": sug if st == "ok" else None, "email": email}
