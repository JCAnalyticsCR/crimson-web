"""Cliente del WebService de SiReTT Online (el ERP de Eurocomp), con Crimson como CLIENTE (operaciones wsc_).

Documentacion: foro.sirettonline.net, tema 427 "WebServices para conectividad con Aplicaciones de Terceros"; firmas
verificadas contra el WSDL de Eurocomp (https://eurocompcr.com/webservice.php?wsdl, 2026-10-15).

- SOAP RPC (nusoap, namespace urn:server). Respuesta ISO-8859-1; el catalogo completo pesa ~34 MB.
- Credencial: API Key (sirett_cli_ + 48 hex) con ws_cid=0, o la heredada ID + contrasena (obsoleta en SiReTT).
- Cada respuesta trae `result` (0 = OK). Las consultas masivas (bodega completa) admiten UNA por hora por cliente
  (result 20); un articulo o una lista de hasta 50 codigos no tiene limite.
- Permisos reales de Crimson en Eurocomp: solo las variantes `_bodega_all_` (y el estado de preventas). Las demas
  responden 4 (sin permiso).
Nunca se registra la credencial: ni en logs, ni en errores, ni en la respuesta guardada.
"""

from __future__ import annotations

from html import escape

import httpx
from lxml import etree

from ..core.config import settings

RESULT = {
    0: "OK",
    1: "La cuenta no existe en Eurocomp",
    2: "La cuenta no tiene acceso al WebService",
    3: "Credencial incorrecta",
    4: "Sin permiso para esa consulta (lo habilita Eurocomp en la ficha de Crimson)",
    5: "Eurocomp tiene el WebService deshabilitado",
    6: "Artículo no encontrado",
    9: "Bodega no encontrada o no autorizada",
    20: "Eurocomp permite una consulta del catálogo completo por hora",
}
MAX_CODES = 50  # tope de *_items_list
# sin DTD, sin entidades, sin red; huge_tree porque el catalogo completo es un solo documento grande
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True, remove_comments=True)


class SirettError(Exception):
    def __init__(self, code: int, message: str | None = None):
        self.code = code
        self.message = message or RESULT.get(code, f"Código {code} de SiReTT")
        super().__init__(f"SiReTT {code}: {self.message}")


def _local(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) else ""


class SirettClient:
    def __init__(self, url: str, ws_id: int, secret: str, timeout: float = 240):
        self.url, self.ws_id, self.secret, self.timeout = url, ws_id, secret, timeout

    @classmethod
    def from_settings(cls) -> SirettClient | None:
        """None = sin credencial configurada (o un ID que no es numero)."""
        key = settings.eurocomp_ws_key.get_secret_value().strip() if settings.eurocomp_ws_key else ""
        if key:
            return cls(settings.eurocomp_ws_url, 0, key)
        pw = settings.eurocomp_ws_password.get_secret_value() if settings.eurocomp_ws_password else ""
        raw_id = (settings.eurocomp_ws_id or "").strip()
        if pw and raw_id.isdigit():
            return cls(settings.eurocomp_ws_url, int(raw_id), pw)
        return None

    @property
    def method(self) -> str:
        return "api_key" if self.ws_id == 0 else "id_contrasena"

    def _envelope(self, op: str, params: dict) -> bytes:
        args = f'<ws_cid xsi:type="xsd:int">{self.ws_id}</ws_cid><ws_passwd xsi:type="xsd:string">{escape(self.secret)}</ws_passwd>'
        for k, v in params.items():
            kind = "int" if isinstance(v, int) else "string"
            args += f'<{k} xsi:type="xsd:{kind}">{escape(str(v))}</{k}>'
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:urn="urn:server">'
            f"<soapenv:Body><urn:{op}>{args}</urn:{op}></soapenv:Body></soapenv:Envelope>"
        ).encode()

    def call(self, op: str, **params) -> etree._Element:
        try:
            r = httpx.post(
                self.url,
                content=self._envelope(op, params),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"urn:server#{op}"'},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise SirettError(-1, f"Eurocomp no respondió ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise SirettError(-1, f"Eurocomp respondió HTTP {r.status_code}")
        try:
            root = etree.fromstring(r.content, PARSER)
        except etree.XMLSyntaxError:
            raise SirettError(-1, "Respuesta de Eurocomp ilegible") from None
        res = next((e for e in root.iter() if _local(e.tag) == "result"), None)
        if res is None:
            fault = next((e.text for e in root.iter() if _local(e.tag) == "faultstring"), None)
            raise SirettError(-1, f"Eurocomp respondió un error SOAP: {(fault or 'sin detalle')[:160]}")
        code = int((res.text or "-1").strip())
        if code != 0:
            raise SirettError(code)
        return root

    @staticmethod
    def rows(root) -> list[dict]:
        """Cada articulo (items_Row) como dict de textos limpios; precios_extra queda fuera (no viene poblado)."""
        out = []
        for e in root.iter():
            kids = [c for c in e if _local(c.tag)]
            names = {_local(c.tag) for c in kids}
            if "codigo" in names and "descripcion" in names:
                out.append({_local(c.tag): (c.text or "").strip() for c in kids if _local(c.tag) != "precios_extra"})
        return out

    # ---------- consultas ----------
    def bodega_items(self, bid: int) -> list[dict]:
        """Catalogo completo de la bodega (todos los articulos, con o sin existencia). Una vez por hora."""
        return self.rows(self.call("wsc_request_bodega_all_items", bid=bid))

    def items_list(self, bid: int, codes: list[str]) -> list[dict]:
        """Articulos puntuales al momento (sin limite de frecuencia), de a 50 codigos."""
        out: list[dict] = []
        clean = [c.strip() for c in codes if c and c.strip() and "," not in c]
        for i in range(0, len(clean), MAX_CODES):
            try:
                out += self.rows(self.call("wsc_request_bodega_all_items_list", bid=bid, icodigos=",".join(clean[i : i + MAX_CODES])))
            except SirettError as e:
                if e.code != 6:  # ninguno de esos codigos existe: lista vacia, no error
                    raise
        return out


def image_url(path: str | None, base: str | None = None) -> str | None:
    """SiReTT entrega la ruta relativa (upload/items/2026/8/x.jpg): se arma la URL publica del sitio del proveedor."""
    p = (path or "").strip()
    if not p:
        return None
    if p.startswith(("http://", "https://")):
        return p
    root = (base or settings.eurocomp_ws_url).split("/webservice.php")[0].rstrip("/")
    return f"{root}/{p.lstrip('/')}"
