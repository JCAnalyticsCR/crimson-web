"""Comprobantes electronicos v4.4 (y 4.3 historicos): lectura endurecida y construccion en el orden del XSD.

Lectura: lxml sin DTD, sin entidades ni red; busqueda por nombre local (sin depender del prefijo ni del namespace), asi
entran 4.4, 4.3 y los XML con prefijo ns0:. Montos con Decimal.
Construccion: MensajeReceptor y FE/TE/NC en el orden obligatorio del XSD oficial (xsd.py los valida antes de enviar).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from lxml import etree

CR = ZoneInfo("America/Costa_Rica")
NS = "https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/"
NS_MR = NS + "mensajeReceptor"
ROOT_NS = {"FE": NS + "facturaElectronica", "TE": NS + "tiqueteElectronico", "NC": NS + "notaCreditoElectronica"}
ROOT_TAG = {"FE": "FacturaElectronica", "TE": "TiqueteElectronico", "NC": "NotaCreditoElectronica"}
ROOTS = {
    "FacturaElectronica": "FE",
    "TiqueteElectronico": "TE",
    "NotaCreditoElectronica": "NC",
    "NotaDebitoElectronica": "ND",
    "FacturaElectronicaCompra": "FEC",
    "FacturaElectronicaExportacion": "FEE",
    "ReciboElectronicoPago": "REP",
}
IVA_CODES = ("01", "07", "08")  # IVA, IVA (calculo especial), IVA regimen de bienes usados
MAX_XML = 2 * 1024 * 1024
SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True)
Q5 = Decimal("0.00001")
# unidades de servicio (para separar servicios de mercancias en el resumen). A confirmar con el validador de pruebas.
SERVICE_UNITS = {"Sp", "Spe", "St", "Os", "h", "d", "Al", "Alc", "Cm", "I"}
# CodigoTarifaIVA (Nota 8.1) -> tarifa
RATE_OF_CODE = {
    "01": Decimal(0),
    "02": Decimal(1),
    "03": Decimal(2),
    "04": Decimal(4),
    "05": Decimal(0),
    "06": Decimal(4),
    "07": Decimal(8),
    "08": Decimal(13),
    "09": Decimal("0.5"),
    "10": Decimal(0),
    "11": Decimal(0),
}
CODE_OF_RATE = {Decimal(13): "08", Decimal(4): "04", Decimal(2): "03", Decimal(1): "02", Decimal("0.5"): "09"}


class XmlRejected(Exception):
    """El archivo no se acepta (DTD, tamano, mal formado, sin clave). Mensaje legible."""


class BuildError(Exception):
    """Faltan datos para armar el comprobante (lo arregla el usuario: CABYS, tarifa, perfil)."""


# ---------------------------------------------------------------- lectura
def norm_id(v: str | None) -> str | None:
    """Identificacion comparable: mayusculas sin separadores (desde nov-2026 hay cedulas juridicas alfanumericas)."""
    if v is None:
        return None
    s = re.sub(r"[^0-9A-Za-z]", "", str(v)).upper()
    return s or None


def same_id(a: str | None, b: str | None) -> bool:
    na, nb = norm_id(a), norm_id(b)
    if not na or not nb:
        return False
    if na.isdigit() and nb.isdigit():
        return int(na) == int(nb)
    return na == nb


def digits(v: str | None) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit())


def _head_has_dtd(data: bytes) -> bool:
    head = data[:4096]
    variants = [head.lower()]
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            variants.append(head.decode("utf-16", errors="ignore").lower().encode("utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            pass
    else:  # UTF-16 sin BOM tambien deja ver la marca con ceros intercalados
        variants.append(head.replace(b"\x00", b"").lower())
    return any(b"<!doctype" in v or b"<!entity" in v for v in variants)


def clean_bytes(data: bytes) -> bytes:
    """Quita BOM UTF-8 y espacios iniciales (varios facturadores los ponen)."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.lstrip()


def parse_xml_bytes(data: bytes) -> etree._Element:
    if not isinstance(data, bytes):
        raise XmlRejected("El XML debe leerse como bytes")
    if len(data) > MAX_XML:
        raise XmlRejected("XML de más de 2 MB: rechazado")
    if _head_has_dtd(data):
        raise XmlRejected("XML con DTD o entidades: rechazado")
    try:
        root = etree.fromstring(clean_bytes(data), SAFE_PARSER)
    except etree.XMLSyntaxError as e:
        raise XmlRejected("XML mal formado") from e
    if root is None:
        raise XmlRejected("XML vacío")
    info = root.getroottree().docinfo
    if info.doctype or info.internalDTD is not None or info.externalDTD is not None:
        # un DOCTYPE despues de 4 KB (comentario largo delante) pasaba _head_has_dtd: la regla se cumple igual
        raise XmlRejected("XML con DTD o entidades: rechazado")
    return root


def to_utf8_text(data: bytes, root: etree._Element) -> str:
    """Texto UTF-8 para guardar. Si venia en UTF-8 se guarda tal cual (bytes exactos); si no, se reserializa en UTF-8
    (la firma XAdES no cubre la declaracion y la canonicalizacion la ignora)."""
    raw = clean_bytes(data)
    m = re.match(rb"<\?xml[^>]*encoding=[\"']([A-Za-z0-9_\-]+)", raw[:200])
    if not m or m.group(1).lower() in (b"utf-8", b"utf8"):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8").decode("utf-8")


def local(el) -> str:
    if not isinstance(el.tag, str):
        return ""
    return etree.QName(el).localname


def kids(el, name: str) -> list:
    return [c for c in el if local(c) == name] if el is not None else []


def child(el, name: str):
    if el is None:
        return None
    for c in el:
        if local(c) == name:
            return c
    return None


def path(el, *names: str):
    for n in names:
        el = child(el, n)
        if el is None:
            return None
    return el


def text(el, *names: str) -> str | None:
    e = path(el, *names) if names else el
    if e is None or e.text is None:
        return None
    t = e.text.strip()
    return t or None


def descendants(el, name: str) -> list:
    return [e for e in el.iter() if local(e) == name] if el is not None else []


def dec(v: str | None, default: Decimal = Decimal(0)) -> Decimal:
    if v is None or str(v).strip() == "":
        return default
    try:
        return Decimal(str(v).strip())
    except InvalidOperation:
        return default


def classify(root) -> str:
    name = local(root)
    if name in ROOTS:
        return "comprobante"
    if name == "MensajeHacienda":
        return "mensaje_hacienda"
    if name == "MensajeReceptor":
        return "mensaje_receptor"
    return "otro"


def parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CR)  # sin zona: hora de Costa Rica (Decreto 44739-H art. 13)
    return dt


@dataclass
class ParsedDoc:
    doc_type: str
    schema_version: str | None
    clave: str
    consecutive: str | None
    issue_dt: datetime | None
    issue_date: date | None
    issuer_name: str | None
    issuer_id_type: str | None
    issuer_id: str | None
    issuer_email: str | None
    issuer_activity_code: str | None
    receiver_id: str | None
    currency: str
    fx_rate: Decimal
    net: Decimal
    tax_total: Decimal
    iva_total: Decimal
    other_charges: Decimal
    iva_returned: Decimal
    tax_assumed: Decimal
    total: Decimal
    reference_clave: str | None
    lines: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _version(root) -> str | None:
    ns = etree.QName(root).namespace or ""
    m = re.search(r"/v(4\.\d)/", ns)
    return m.group(1) if m else None


def parse_comprobante(root, raw: bytes | None = None) -> ParsedDoc:
    name = local(root)
    if name not in ROOTS:
        raise XmlRejected("El XML no es un comprobante electrónico")
    clave = text(root, "Clave")
    if not clave or not re.fullmatch(r"\d{50}", clave):
        raise XmlRejected("El XML no contiene una Clave de 50 dígitos")
    issue_dt = parse_dt(text(root, "FechaEmision"))
    issue_date = issue_dt.astimezone(CR).date() if issue_dt else None
    em, rc = child(root, "Emisor"), child(root, "Receptor")
    res = child(root, "ResumenFactura")
    currency = (text(res, "CodigoTipoMoneda", "CodigoMoneda") or next((e.text.strip() for e in descendants(res, "CodigoMoneda") if e.text), None) or "CRC")[:3]
    fx = dec(text(res, "CodigoTipoMoneda", "TipoCambio") or next((e.text for e in descendants(res, "TipoCambio") if e.text), None), Decimal(1))
    tax_total = dec(text(res, "TotalImpuesto"))
    # IVA: desglose 4.4 (TotalDesgloseImpuesto) o, en 4.3 / sin desglose, la suma de los impuestos IVA de las lineas
    desglose = kids(res, "TotalDesgloseImpuesto") if res is not None else []
    if desglose:
        iva_total = sum((dec(text(dz, "TotalMontoImpuesto")) for dz in desglose if (text(dz, "Codigo") or "") in IVA_CODES), Decimal(0))
    else:
        line_taxes = [imp for ln in descendants(root, "LineaDetalle") for imp in kids(ln, "Impuesto")]
        if line_taxes:
            iva_total = Decimal(0)
            for imp in line_taxes:
                if (text(imp, "Codigo") or "") in IVA_CODES:
                    exo = dec(text(imp, "Exoneracion", "MontoExoneracion"))
                    iva_total += max(dec(text(imp, "Monto")) - exo, Decimal(0))
        else:
            iva_total = tax_total
    iva_total = min(iva_total, tax_total) if tax_total > 0 else iva_total
    lines = []
    for ln in descendants(root, "LineaDetalle"):
        imp = child(ln, "Impuesto")
        lines.append(
            {
                "number": text(ln, "NumeroLinea"),
                "cabys": text(ln, "CodigoCABYS") or text(ln, "Codigo"),
                "detail": text(ln, "Detalle"),
                "quantity": str(dec(text(ln, "Cantidad"))),
                "unit": text(ln, "UnidadMedida"),
                "price": str(dec(text(ln, "PrecioUnitario"))),
                "subtotal": str(dec(text(ln, "SubTotal"))),
                "tax_code": text(imp, "Codigo") if imp is not None else None,
                "tax_rate": text(imp, "Tarifa") if imp is not None else None,
                "tax": str(dec(text(imp, "Monto"))) if imp is not None else "0",
                "total": str(dec(text(ln, "MontoTotalLinea"))),
            }
        )
    p = ParsedDoc(
        doc_type=ROOTS[name],
        schema_version=_version(root),
        clave=clave,
        consecutive=text(root, "NumeroConsecutivo"),
        issue_dt=issue_dt,
        issue_date=issue_date,
        issuer_name=(text(em, "Nombre") or "")[:200] or None,
        issuer_id_type=text(em, "Identificacion", "Tipo"),
        issuer_id=norm_id(text(em, "Identificacion", "Numero")),
        issuer_email=(text(em, "CorreoElectronico") or "")[:200] or None,
        issuer_activity_code=text(root, "CodigoActividadEmisor") or text(root, "CodigoActividad"),
        receiver_id=norm_id(text(rc, "Identificacion", "Numero")) if rc is not None else None,
        currency=currency,
        fx_rate=fx if fx > 0 else Decimal(1),
        net=dec(text(res, "TotalVentaNeta") or text(res, "TotalVenta")),
        tax_total=tax_total,
        iva_total=iva_total,
        other_charges=dec(text(res, "TotalOtrosCargos")),
        iva_returned=dec(text(res, "TotalIVADevuelto")),
        tax_assumed=dec(text(res, "TotalImpAsumEmisorFabrica")),
        total=dec(text(res, "TotalComprobante")),
        reference_clave=text(root, "InformacionReferencia", "Numero"),
        lines=lines,
    )
    if p.iva_returned > 0 or p.tax_assumed > 0:
        p.warnings.append("IVA devuelto / asumido por fábrica: revisar el crédito con contabilidad")
    if p.issue_date and clave[3:9] != p.issue_date.strftime("%d%m%y"):
        p.warnings.append("La fecha de la clave no coincide con la fecha de emisión")
    return p


def parse_mensaje_hacienda(root) -> dict:
    return {
        "clave": text(root, "Clave"),
        "issuer_id": norm_id(text(root, "NumeroCedulaEmisor")),
        "receiver_id": norm_id(text(root, "NumeroCedulaReceptor")),
        "mensaje": text(root, "Mensaje"),
        "detalle": (text(root, "DetalleMensaje") or "")[:500] or None,
        "tax_total": dec(text(root, "MontoTotalImpuesto")),
        "total": dec(text(root, "TotalFactura")),
    }


def parse_mensaje_receptor(root) -> dict:
    return {
        "clave": text(root, "Clave"),
        "issuer_id": norm_id(text(root, "NumeroCedulaEmisor")),
        "fecha": parse_dt(text(root, "FechaEmisionDoc")),
        "mensaje": text(root, "Mensaje"),
        "detalle": (text(root, "DetalleMensaje") or "")[:160] or None,
        "tax_total": dec(text(root, "MontoTotalImpuesto")),
        "activity_code": text(root, "CodigoActividad"),
        "condition": text(root, "CondicionImpuesto"),
        "credit": dec(text(root, "MontoTotalImpuestoAcreditar"), None) if text(root, "MontoTotalImpuestoAcreditar") else None,
        "applicable": dec(text(root, "MontoTotalDeGastoAplicable"), None) if text(root, "MontoTotalDeGastoAplicable") else None,
        "total": dec(text(root, "TotalFactura")),
        "receiver_id": norm_id(text(root, "NumeroCedulaReceptor")),
        "consecutive": text(root, "NumeroConsecutivoReceptor"),
    }


# ---------------------------------------------------------------- construccion
def m5(x) -> str:
    return f"{Decimal(str(x)).quantize(Q5, rounding=ROUND_HALF_UP):.5f}"


def _tipo_cambio(moneda: str, fx) -> Decimal:
    """1 en colones; en otra moneda la tasa del documento, nunca 1 (services/fx ya la resuelve al armar el payload)."""
    if moneda == "CRC":
        return Decimal(1)
    try:
        v = Decimal(str(fx)) if fx is not None else Decimal(0)
    except InvalidOperation:
        v = Decimal(0)
    if v <= 1:
        raise BuildError(f"Falta el tipo de cambio de {moneda} del comprobante (TipoCambio no puede ser 1)")
    return v


def cr_iso(dt: datetime) -> str:
    """Hora de Costa Rica con zona y sin microsegundos: 2026-10-14T23:30:00-06:00."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CR)
    return dt.astimezone(CR).replace(microsecond=0).isoformat(timespec="seconds")


class _B:
    def __init__(self, ns: str):
        self.ns = ns

    def el(self, parent, tag: str, value=None):
        e = etree.SubElement(parent, f"{{{self.ns}}}{tag}") if parent is not None else etree.Element(f"{{{self.ns}}}{tag}", nsmap={None: self.ns})
        if value is not None:
            e.text = str(value)
        return e


def mensaje_receptor_element(
    *,
    clave: str,
    issuer_id: str,
    emitted_at: datetime,
    message_code: str,
    detail: str | None,
    tax_total: Decimal,
    activity_code: str | None,
    condition_code: str | None,
    credit: Decimal | None,
    applicable: Decimal | None,
    include_amounts: bool,
    total: Decimal,
    receiver_digits: str,
    consecutive: str,
) -> etree._Element:
    if message_code not in ("1", "2", "3"):
        raise BuildError("Mensaje debe ser 1, 2 o 3")
    if detail is not None and not (5 <= len(detail) <= 160):
        raise BuildError("El detalle del mensaje debe tener de 5 a 160 caracteres")
    if message_code in ("2", "3") and not detail:
        raise BuildError("El detalle es obligatorio en aceptación parcial y rechazo")
    b = _B(NS_MR)
    root = b.el(None, "MensajeReceptor")
    b.el(root, "Clave", clave)
    b.el(root, "NumeroCedulaEmisor", issuer_id)
    b.el(root, "FechaEmisionDoc", cr_iso(emitted_at))
    b.el(root, "Mensaje", message_code)
    if detail:
        b.el(root, "DetalleMensaje", detail)
    if tax_total and abs(tax_total) > 0:
        b.el(root, "MontoTotalImpuesto", m5(abs(tax_total)))
    if message_code in ("1", "2"):
        if condition_code != "05" and activity_code:
            b.el(root, "CodigoActividad", activity_code)
        if condition_code:
            b.el(root, "CondicionImpuesto", condition_code)
        if include_amounts:
            b.el(root, "MontoTotalImpuestoAcreditar", m5(abs(credit or 0)))
            b.el(root, "MontoTotalDeGastoAplicable", m5(abs(applicable or 0)))
    b.el(root, "TotalFactura", m5(abs(total)))
    b.el(root, "NumeroCedulaReceptor", receiver_digits)
    b.el(root, "NumeroConsecutivoReceptor", consecutive)
    return root


def build_mensaje_receptor(r, doc, tenant, receiver_digits: str) -> etree._Element:
    """r: ReceptionResponse; doc: ReceivedDocument. Montos en positivo (NC incluida; DecimalDineroType >= 0)."""
    from ...services.reception_calc import code_of, xml_amounts

    cond = r.iva_condition_code
    iva_total = Decimal(str(doc.iva_total)) if doc.iva_total is not None else Decimal(str(doc.tax_total or 0))
    action = {"1": "aceptada", "2": "parcial", "3": "rechazada"}[r.message_code]
    include = xml_amounts(action, cond, Decimal(str(r.tax_total or 0)), iva_total) if cond else False
    return mensaje_receptor_element(
        clave=doc.clave,
        issuer_id=doc.issuer_id or "",
        emitted_at=r.emitted_at,
        message_code=r.message_code,
        detail=r.detail,
        tax_total=Decimal(str(r.tax_total or 0)),
        activity_code=r.activity_code,
        condition_code=code_of(cond) if cond else None,
        credit=Decimal(str(r.credit_amount)) if r.credit_amount is not None else None,
        applicable=Decimal(str(r.applicable_amount)) if r.applicable_amount is not None else None,
        include_amounts=include,
        total=Decimal(str(r.total or 0)),
        receiver_digits=receiver_digits,
        consecutive=r.consecutive,
    )


def rate_code_for(rate: Decimal, preferred: str | None = None) -> str:
    """CodigoTarifaIVA: el del producto si corresponde a la tarifa; si no, por tarifa. 0 % sin codigo -> error."""
    rate = Decimal(str(rate)).normalize()
    if preferred and preferred in RATE_OF_CODE and RATE_OF_CODE[preferred] == rate:
        return preferred
    for r, code in CODE_OF_RATE.items():
        if r == rate:
            return code
    raise BuildError(f"Tarifa de IVA {rate} % sin código de tarifa: indíquelo en el impuesto del producto (01, 05, 10 u 11)")


def build_comprobante(p: dict) -> etree._Element:
    """Arma FE / TE / NC 4.4 en el orden del XSD. p trae todo resuelto (ver services/einvoice.py::_direct_payload)."""
    dt = p["doc_type"]
    if dt not in ROOT_TAG:
        raise BuildError(f"Tipo de comprobante no soportado en emisión directa: {dt}")
    b = _B(ROOT_NS[dt])
    root = b.el(None, ROOT_TAG[dt])
    b.el(root, "Clave", p["clave"])
    b.el(root, "ProveedorSistemas", p["proveedor_sistemas"])
    b.el(root, "CodigoActividadEmisor", p["activity_code"])
    b.el(root, "NumeroConsecutivo", p["consecutive"])
    b.el(root, "FechaEmision", cr_iso(p["fecha"]))
    em = p["emisor"]
    e = b.el(root, "Emisor")
    b.el(e, "Nombre", em["name"][:100])
    ide = b.el(e, "Identificacion")
    b.el(ide, "Tipo", em["id_type"])
    b.el(ide, "Numero", em["id_number"])
    if em.get("commercial_name") and len(em["commercial_name"]) >= 3:
        b.el(e, "NombreComercial", em["commercial_name"][:80])
    ub = b.el(e, "Ubicacion")
    b.el(ub, "Provincia", em["province"])
    b.el(ub, "Canton", em["canton"])
    b.el(ub, "Distrito", em["district"])
    if em.get("barrio") and 5 <= len(em["barrio"]) <= 50:
        b.el(ub, "Barrio", em["barrio"])
    b.el(ub, "OtrasSenas", em["other_signs"][:250])
    phone = digits(em.get("phone"))
    if phone and len(phone) >= 3:
        tel = b.el(e, "Telefono")
        b.el(tel, "CodigoPais", digits(em.get("phone_country")) or "506")
        b.el(tel, "NumTelefono", phone[:20])
    b.el(e, "CorreoElectronico", em["email"][:160])
    rc = p.get("receptor")
    if rc:
        r = b.el(root, "Receptor")
        b.el(r, "Nombre", rc["name"][:100])
        idr = b.el(r, "Identificacion")
        b.el(idr, "Tipo", rc["id_type"])
        b.el(idr, "Numero", rc["id_number"][:20])
        if rc.get("email"):
            b.el(r, "CorreoElectronico", rc["email"][:160])
    elif dt == "FE":
        raise BuildError("La factura electrónica requiere receptor con identificación (use Tiquete si es consumidor final)")
    b.el(root, "CondicionVenta", p.get("sale_condition") or "01")
    if (p.get("sale_condition") or "01") == "02":
        b.el(root, "PlazoCredito", str(int(p.get("credit_days") or 0)))
    ds = b.el(root, "DetalleServicio")
    tot = {k: Decimal(0) for k in ("sg", "se", "mg", "me", "desc")}
    desglose: dict[tuple[str, str], Decimal] = {}
    if not p["lines"]:
        raise BuildError("La factura no tiene líneas")
    for i, ln in enumerate(p["lines"], start=1):
        if not ln.get("cabys") or not re.fullmatch(r"\d{13}", str(ln["cabys"])):
            raise BuildError(f"La línea {i} no tiene CABYS")
        qty, price = Decimal(str(ln["quantity"])), Decimal(str(ln["unit_price"]))
        monto_total = (qty * price).quantize(Q5, rounding=ROUND_HALF_UP)
        subtotal = Decimal(str(ln["subtotal"])).quantize(Q5, rounding=ROUND_HALF_UP)
        descuento = max(monto_total - subtotal, Decimal(0))
        tax = Decimal(str(ln["tax"])).quantize(Q5, rounding=ROUND_HALF_UP)
        rate = Decimal(str(ln["rate"]))
        rcode = ln["rate_code"]
        ld = b.el(ds, "LineaDetalle")
        b.el(ld, "NumeroLinea", i)
        b.el(ld, "CodigoCABYS", ln["cabys"])
        if ln.get("code"):
            cc = b.el(ld, "CodigoComercial")
            b.el(cc, "Tipo", "01")
            b.el(cc, "Codigo", str(ln["code"])[:20])
        b.el(ld, "Cantidad", f"{qty.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):.3f}")
        b.el(ld, "UnidadMedida", ln["unit"])
        detail = (ln.get("detail") or "").strip()
        b.el(ld, "Detalle", (detail if len(detail) >= 3 else (detail + " ---")[:3])[:200])
        b.el(ld, "PrecioUnitario", m5(price))
        b.el(ld, "MontoTotal", m5(monto_total))
        if descuento > 0:
            dz = b.el(ld, "Descuento")
            b.el(dz, "MontoDescuento", m5(descuento))
            b.el(dz, "CodigoDescuento", "07")
        b.el(ld, "SubTotal", m5(subtotal))
        b.el(ld, "BaseImponible", m5(subtotal))
        imp = b.el(ld, "Impuesto")
        b.el(imp, "Codigo", "01")
        b.el(imp, "CodigoTarifaIVA", rcode)
        b.el(imp, "Tarifa", f"{rate.quantize(Decimal('0.01')):.2f}")
        b.el(imp, "Monto", m5(tax))
        b.el(ld, "ImpuestoAsumidoEmisorFabrica", m5(0))
        b.el(ld, "ImpuestoNeto", m5(tax))
        b.el(ld, "MontoTotalLinea", m5(subtotal + tax))
        service = ln["unit"] in SERVICE_UNITS
        exento = rcode == "10"
        tot[("s" if service else "m") + ("e" if exento else "g")] += monto_total
        tot["desc"] += descuento
        desglose[("01", rcode)] = desglose.get(("01", rcode), Decimal(0)) + tax
    rs = b.el(root, "ResumenFactura")
    ctm = b.el(rs, "CodigoTipoMoneda")
    moneda = p.get("currency") or "CRC"
    b.el(ctm, "CodigoMoneda", moneda)
    b.el(ctm, "TipoCambio", m5(_tipo_cambio(moneda, p.get("fx"))))
    gravado, exento = tot["sg"] + tot["mg"], tot["se"] + tot["me"]
    for tag, key in (("TotalServGravados", "sg"), ("TotalServExentos", "se"), ("TotalMercanciasGravadas", "mg"), ("TotalMercanciasExentas", "me")):
        b.el(rs, tag, m5(tot[key]))
    b.el(rs, "TotalGravado", m5(gravado))
    b.el(rs, "TotalExento", m5(exento))
    venta = gravado + exento
    b.el(rs, "TotalVenta", m5(venta))
    b.el(rs, "TotalDescuentos", m5(tot["desc"]))
    neta = venta - tot["desc"]
    b.el(rs, "TotalVentaNeta", m5(neta))
    impuesto = Decimal(0)
    for (code, rcode), amount in desglose.items():
        tdi = b.el(rs, "TotalDesgloseImpuesto")
        b.el(tdi, "Codigo", code)
        b.el(tdi, "CodigoTarifaIVA", rcode)
        b.el(tdi, "TotalMontoImpuesto", m5(amount))
        impuesto += amount
    b.el(rs, "TotalImpuesto", m5(impuesto))
    total = neta + impuesto
    mp = b.el(rs, "MedioPago")
    b.el(mp, "TipoMedioPago", p.get("payment_method") or "01")
    b.el(mp, "TotalMedioPago", m5(total))
    b.el(rs, "TotalComprobante", m5(total))
    ref = p.get("reference")
    if ref:
        ir = b.el(root, "InformacionReferencia")
        b.el(ir, "TipoDocIR", ref.get("doc_type") or "01")
        b.el(ir, "Numero", ref["number"])
        b.el(ir, "FechaEmisionIR", cr_iso(ref["date"]))
        b.el(ir, "Codigo", ref.get("code") or "01")
        b.el(ir, "Razon", (ref.get("reason") or "Anulación del comprobante")[:180])
    elif dt == "NC":
        raise BuildError("La nota de crédito requiere la referencia al comprobante original")
    return root


def build_factura(p: dict) -> etree._Element:
    return build_comprobante({**p, "doc_type": "FE"})


def build_tiquete(p: dict) -> etree._Element:
    return build_comprobante({**p, "doc_type": "TE"})


def build_nota_credito(p: dict) -> etree._Element:
    return build_comprobante({**p, "doc_type": "NC"})


def mensaje_hacienda_simulado(clave: str, emisor_id: str, receptor_id: str | None, tax_total, total, mensaje: str = "1") -> str:
    """MensajeHacienda de simulacion: misma forma, con el aviso explicito de que no tiene validez fiscal."""
    ns = NS + "mensajeHacienda"
    b = _B(ns)
    root = b.el(None, "MensajeHacienda")
    b.el(root, "Clave", clave)
    b.el(root, "NombreEmisor", "SIMULACION")
    b.el(root, "TipoIdentificacionEmisor", "02")
    b.el(root, "NumeroCedulaEmisor", (emisor_id or "0")[:20])
    if receptor_id:
        b.el(root, "NombreReceptor", "SIMULACION")
        b.el(root, "TipoIdentificacionReceptor", "02")
        b.el(root, "NumeroCedulaReceptor", receptor_id[:20])
    b.el(root, "Mensaje", mensaje)
    b.el(root, "DetalleMensaje", "SIMULACIÓN · sin validez fiscal")
    b.el(root, "MontoTotalImpuesto", m5(abs(Decimal(str(tax_total or 0)))))
    b.el(root, "TotalFactura", m5(abs(Decimal(str(total or 0)))))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8").decode("utf-8")
