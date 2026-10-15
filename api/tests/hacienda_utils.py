"""Utilidades de prueba para Hacienda y recepcion. Nada de llaves ni XML reales: todo se genera al correr
(certificados autofirmados con cedulas ficticias, comprobantes inventados)."""

from __future__ import annotations

import base64
import imaplib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

NS44 = "https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/"
NS43 = "https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.3/"
EMPRESA = "3102000000"  # cedula ficticia de la empresa semilla (seeds.py: 3-102-000000)


def make_p12(cedula_digits: str = EMPRESA, pin: str | None = None, *, days: int = 30, key_size: int = 2048, expired: bool = False) -> tuple[bytes, str]:
    """Certificado autofirmado con serialNumber=CPJ-<cedula> y serie corta (XAdESVerifier falla con ~48 digitos)."""
    pin = pin or secrets.token_urlsafe(10)
    key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA FICTICIA DE PRUEBA SA"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CPJ-{cedula_digits[:1]}-{cedula_digits[1:4]}-{cedula_digits[4:]}"),
        ]
    )
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA DE PRUEBA NO VALIDA")])
    now = datetime.now(UTC)
    nb, na = (now - timedelta(days=60), now - timedelta(days=1)) if expired else (now - timedelta(days=1), now + timedelta(days=days))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(int.from_bytes(os.urandom(8), "big") >> 1)
        .not_valid_before(nb)
        .not_valid_after(na)
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(b"prueba", key, cert, None, serialization.BestAvailableEncryption(pin.encode())), pin


def clave(fecha: str = "091026", emisor: str = "003101000001", consecutivo: str = "00100001010000000123", seguridad: str = "12345678") -> str:
    return f"506{fecha}{emisor.zfill(12)[-12:]}{consecutivo}1{seguridad}"


def fe_xml(
    clave_: str,
    emisor: str = "3101000001",
    receptor: str | None = EMPRESA,
    total: float = 113000,
    iva: float = 13000,
    currency: str = "CRC",
    fx: str = "1",
    tipo: str = "FacturaElectronica",
    version: str = "4.4",
    prefix: str | None = None,
    referencia: str | None = None,
    fecha: str = "2026-10-09T10:00:00-06:00",
    otros_cargos: float = 0,
    impuesto_especifico: float = 0,
    consecutivo: str = "00100001010000000123",
    nombre: str = "Distribuidora Ficticia SA",
    correo: str = "ventas@proveedor.example",
) -> str:
    """Comprobante ficticio con namespace (y variante con prefijo ns0:)."""
    ns = (NS44 if version == "4.4" else NS43) + tipo[0].lower() + tipo[1:]
    p = f"{prefix}:" if prefix else ""
    decl = f'xmlns:{prefix}="{ns}"' if prefix else f'xmlns="{ns}"'
    tax = iva + impuesto_especifico
    net = total - tax - otros_cargos
    act = f"<{p}CodigoActividadEmisor>4651.0</{p}CodigoActividadEmisor>" if version == "4.4" else f"<{p}CodigoActividad>465100</{p}CodigoActividad>"
    rec = (
        f"<{p}Receptor><{p}Nombre>Empresa Receptora SA</{p}Nombre><{p}Identificacion><{p}Tipo>02</{p}Tipo><{p}Numero>{receptor}</{p}Numero></{p}Identificacion></{p}Receptor>"
        if receptor
        else ""
    )
    if version == "4.4":
        desglose = f"<{p}TotalDesgloseImpuesto><{p}Codigo>01</{p}Codigo><{p}CodigoTarifaIVA>08</{p}CodigoTarifaIVA><{p}TotalMontoImpuesto>{iva:.5f}</{p}TotalMontoImpuesto></{p}TotalDesgloseImpuesto>"
        if impuesto_especifico:
            desglose += f"<{p}TotalDesgloseImpuesto><{p}Codigo>05</{p}Codigo><{p}TotalMontoImpuesto>{impuesto_especifico:.5f}</{p}TotalMontoImpuesto></{p}TotalDesgloseImpuesto>"
        moneda = f"<{p}CodigoTipoMoneda><{p}CodigoMoneda>{currency}</{p}CodigoMoneda><{p}TipoCambio>{fx}</{p}TipoCambio></{p}CodigoTipoMoneda>"
    else:
        desglose = ""
        moneda = f"<{p}CodigoTipoMoneda><{p}CodigoMoneda>{currency}</{p}CodigoMoneda><{p}TipoCambio>{fx}</{p}TipoCambio></{p}CodigoTipoMoneda>"
    linea = (
        f"<{p}LineaDetalle><{p}NumeroLinea>1</{p}NumeroLinea><{p}CodigoCABYS>4523100000100</{p}CodigoCABYS><{p}Cantidad>1.000</{p}Cantidad>"
        f"<{p}UnidadMedida>Unid</{p}UnidadMedida><{p}Detalle>Switch de prueba</{p}Detalle><{p}PrecioUnitario>{net:.5f}</{p}PrecioUnitario>"
        f"<{p}MontoTotal>{net:.5f}</{p}MontoTotal><{p}SubTotal>{net:.5f}</{p}SubTotal><{p}Impuesto><{p}Codigo>01</{p}Codigo><{p}Tarifa>13.00</{p}Tarifa>"
        f"<{p}Monto>{iva:.5f}</{p}Monto></{p}Impuesto><{p}MontoTotalLinea>{net + iva:.5f}</{p}MontoTotalLinea></{p}LineaDetalle>"
    )
    ref = (
        f"<{p}InformacionReferencia><{p}TipoDocIR>01</{p}TipoDocIR><{p}Numero>{referencia}</{p}Numero><{p}FechaEmisionIR>{fecha}</{p}FechaEmisionIR>"
        f"<{p}Codigo>01</{p}Codigo><{p}Razon>Anula</{p}Razon></{p}InformacionReferencia>"
        if referencia
        else ""
    )
    otros = f"<{p}TotalOtrosCargos>{otros_cargos:.5f}</{p}TotalOtrosCargos>" if otros_cargos else ""
    return (
        f'<?xml version="1.0" encoding="utf-8"?><{p}{tipo} {decl}><{p}Clave>{clave_}</{p}Clave>{act}<{p}NumeroConsecutivo>{consecutivo}</{p}NumeroConsecutivo>'
        f"<{p}FechaEmision>{fecha}</{p}FechaEmision><{p}Emisor><{p}Nombre>{nombre}</{p}Nombre><{p}Identificacion><{p}Tipo>02</{p}Tipo><{p}Numero>{emisor}</{p}Numero>"
        f"</{p}Identificacion><{p}CorreoElectronico>{correo}</{p}CorreoElectronico></{p}Emisor>{rec}<{p}CondicionVenta>01</{p}CondicionVenta>"
        f"<{p}DetalleServicio>{linea}</{p}DetalleServicio><{p}ResumenFactura>{moneda}<{p}TotalVenta>{net:.5f}</{p}TotalVenta>"
        f"<{p}TotalVentaNeta>{net:.5f}</{p}TotalVentaNeta>{desglose}<{p}TotalImpuesto>{tax:.5f}</{p}TotalImpuesto>{otros}"
        f"<{p}TotalComprobante>{total:.5f}</{p}TotalComprobante></{p}ResumenFactura>{ref}</{p}{tipo}>"
    )


def mensaje_hacienda(clave_: str, emisor: str = "3101000001", receptor: str = EMPRESA, mensaje: str = "1", detalle: str = "Aceptado") -> str:
    ns = NS44 + "mensajeHacienda"
    return (
        f'<?xml version="1.0" encoding="utf-8"?><MensajeHacienda xmlns="{ns}"><Clave>{clave_}</Clave><NombreEmisor>Proveedor</NombreEmisor>'
        f"<TipoIdentificacionEmisor>02</TipoIdentificacionEmisor><NumeroCedulaEmisor>{emisor}</NumeroCedulaEmisor><NombreReceptor>Empresa</NombreReceptor>"
        f"<TipoIdentificacionReceptor>02</TipoIdentificacionReceptor><NumeroCedulaReceptor>{receptor}</NumeroCedulaReceptor><Mensaje>{mensaje}</Mensaje>"
        f"<DetalleMensaje>{detalle}</DetalleMensaje><MontoTotalImpuesto>13000.00000</MontoTotalImpuesto><TotalFactura>113000.00000</TotalFactura></MensajeHacienda>"
    )


def mensaje_receptor(clave_: str, emisor: str, receptor: str, mensaje: str = "1", detalle: str | None = None, consecutivo: str = "00100001050000000359", condicion: str = "01") -> str:
    ns = NS44 + "mensajeReceptor"
    det = f"<DetalleMensaje>{detalle}</DetalleMensaje>" if detalle else ""
    cond = f"<CodigoActividad>6202.0</CodigoActividad><CondicionImpuesto>{condicion}</CondicionImpuesto>" if mensaje in ("1", "2") else ""
    return (
        f'<?xml version="1.0" encoding="utf-8"?><MensajeReceptor xmlns="{ns}"><Clave>{clave_}</Clave><NumeroCedulaEmisor>{emisor}</NumeroCedulaEmisor>'
        f"<FechaEmisionDoc>2026-10-10T09:00:00-06:00</FechaEmisionDoc><Mensaje>{mensaje}</Mensaje>{det}<MontoTotalImpuesto>13000.00000</MontoTotalImpuesto>"
        f"{cond}<TotalFactura>113000.00000</TotalFactura><NumeroCedulaReceptor>{receptor}</NumeroCedulaReceptor>"
        f"<NumeroConsecutivoReceptor>{consecutivo}</NumeroConsecutivoReceptor></MensajeReceptor>"
    )


class FakeHacienda:
    """IdP + API de recepcion de Hacienda en memoria (httpx.MockTransport). Programable: respuestas del POST,
    secuencia de estados del GET, errores. Registra todo lo que recibe; falla ante cualquier URL de produccion."""

    API = "https://api.comprobanteselectronicos.go.cr/recepcion-sandbox/v1/"
    TOKEN = "https://idp.comprobanteselectronicos.go.cr/auth/realms/rut-stag/protocol/openid-connect/token"

    def __init__(self, username: str = "usuario-pruebas@ejemplo", password: str = "Clave-ATV-de-prueba!", sequence=("recibido", "procesando", "aceptado")):
        self.username, self.password = username, password
        self.sequence = list(sequence)
        self.tokens: list[dict] = []
        self.posts: list[dict] = []
        self.gets: list[str] = []
        self.post_plan: list = []  # cada item: (status, headers) o "timeout"
        self.get_plan: dict[str, list] = {}  # ref -> estados o (status_code,)
        self.known: dict[str, int] = {}  # ref -> cuantas consultas lleva
        self.final: dict[str, str] = {}  # ref -> estado final forzado (aceptado/rechazado)
        self.production_hits: list[str] = []
        self.activities: list[dict] = [{"estado": "A", "codigo": "6202.0", "descripcion": "Actividad de prueba"}]
        self.fail_token = 0

    def install(self, monkeypatch):
        from app.providers.hacienda import client as hclient

        hclient.reset_cache()
        monkeypatch.setattr(hclient, "http_factory", lambda: httpx.Client(transport=httpx.MockTransport(self.handler)))
        return self

    def _respuesta(self, ref: str, estado: str) -> str:
        detalle = "Este comprobante fue rechazado por la prueba" if estado == "rechazado" else "Aceptado"
        xml = mensaje_hacienda(ref[:50], mensaje="3" if estado == "rechazado" else "1", detalle=detalle)
        return base64.b64encode(xml.encode()).decode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/realms/rut/" in url or "/recepcion/v1/" in url:
            self.production_hits.append(url)
            raise AssertionError(f"La prueba intentó hablar con PRODUCCIÓN: {url}")
        if url.startswith("https://api.hacienda.go.cr/fe/ae"):
            return httpx.Response(200, json={"actividades": self.activities})
        if url == self.TOKEN:
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.tokens.append({k: v for k, v in form.items() if k != "password"})
            if self.fail_token:
                self.fail_token -= 1
                return httpx.Response(401, json={"error": "invalid_grant"})
            if form.get("grant_type") == "password" and (form.get("username") != self.username or form.get("password") != self.password):
                return httpx.Response(401, json={"error": "invalid_grant"})
            n = len(self.tokens)
            return httpx.Response(200, json={"access_token": f"tok-{n}", "expires_in": 300, "refresh_token": f"ref-{n}", "refresh_expires_in": 36000})
        if url.endswith("/logout"):
            return httpx.Response(204)
        if url == self.API + "recepcion" and request.method == "POST":
            body = json.loads(request.content)
            self.posts.append(body)
            ref = body["clave"] + (f"-{body['consecutivoReceptor']}" if body.get("consecutivoReceptor") else "")
            if self.post_plan:
                plan = self.post_plan.pop(0)
                if plan == "timeout":
                    self.known.setdefault(ref, 0)  # SI llego a Hacienda: solo se perdio la respuesta
                    raise httpx.ReadTimeout("simulado", request=request)
                if plan == "timeout_lost":
                    raise httpx.ReadTimeout("simulado", request=request)
                status, headers = plan
                if status < 300:
                    self.known.setdefault(ref, 0)
                return httpx.Response(status, headers=headers)
            if ref in self.known:  # como Hacienda: el mismo documento dos veces
                return httpx.Response(400, headers={"X-Error-Cause": "El comprobante " + ref[:50] + " ya fue recibido anteriormente"})
            self.known.setdefault(ref, 0)
            return httpx.Response(201, headers={"Location": self.API + "recepcion/" + ref})
        if url.startswith(self.API + "recepcion/") and request.method == "GET":
            ref = url[len(self.API + "recepcion/") :]
            self.gets.append(ref)
            if ref in self.get_plan and self.get_plan[ref]:
                item = self.get_plan[ref].pop(0)
                if isinstance(item, int):
                    return httpx.Response(item)
                estado = item
            elif ref not in self.known:
                return httpx.Response(404)
            else:
                i = self.known[ref]
                self.known[ref] = i + 1
                estado = self.final.get(ref) or self.sequence[min(i, len(self.sequence) - 1)]
            body = {"clave": ref[:50], "ind-estado": estado}
            if estado in ("aceptado", "rechazado"):
                body["respuesta-xml"] = self._respuesta(ref, estado)
            return httpx.Response(200, json=body)
        raise AssertionError(f"Petición no simulada: {request.method} {url}")

    def bodies(self) -> list[bytes]:
        return [base64.b64decode(p["comprobanteXml"]) for p in self.posts]


class FakeImapUid:
    """Servidor IMAP en memoria con UID. Registra si alguien llama store (no debe: nunca se marca nada leido)."""

    def __init__(self, messages: list[bytes], uidvalidity: int = 1, password: str | None = None, start_uid: int = 1):
        self.msgs = {start_uid + i: m for i, m in enumerate(messages)}
        self.uidvalidity = uidvalidity
        self.password = password
        self.stored: list = []
        self.searches: list[str] = []
        self.fetched_bodies: list[int] = []
        self.sizes: dict[int, int] = {}
        self.fail_on: set[int] = set()

    def login(self, user, password):
        if self.password and password != self.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"Logged in"]

    def select(self, folder="INBOX"):
        return "OK", [str(len(self.msgs)).encode()]

    def status(self, folder, what):
        return "OK", [f'"{folder}" (UIDVALIDITY {self.uidvalidity})'.encode()]

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        if cmd == "SEARCH":
            crit = args[-1]
            self.searches.append(crit)
            if crit.startswith("UID "):
                lo = int(crit[4:].split(":")[0])
                uids = [u for u in sorted(self.msgs) if u >= lo]
                if not uids and self.msgs:
                    uids = [max(self.msgs)]  # como los servidores reales: "N:*" devuelve el ultimo aunque no haya nuevos
            else:
                uids = sorted(self.msgs)
            return "OK", [" ".join(map(str, uids)).encode()]
        if cmd == "FETCH":
            uid, what = int(args[0]), args[1]
            m = self.msgs[uid]
            if "RFC822.SIZE" in what:
                return "OK", [f"{uid} (UID {uid} RFC822.SIZE {self.sizes.get(uid, len(m))})".encode()]
            if "BODY.PEEK[]" in what:
                self.fetched_bodies.append(uid)
                return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(m)}}}".encode(), m), b")"]
        raise AssertionError(f"Comando IMAP no esperado: {cmd} {args}")

    def store(self, *args):
        self.stored.append(args)
        return "OK", []

    def search(self, *args):  # el lector viejo usaba SEARCH UNSEEN: ya no
        raise AssertionError("No se debe usar SEARCH sin UID")

    def logout(self):
        return "BYE", []


def email_with(attachments: list[tuple[str, bytes, str]], subject: str = "Factura electronica", sender: str = "proveedor@proveedor.example") -> bytes:
    """Correo MIME con adjuntos (nombre, bytes, subtipo)."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Message-ID"] = f"<{secrets.token_hex(8)}@proveedor.example>"
    msg.attach(MIMEText("Adjuntamos la factura.", "plain"))
    for name, data, sub in attachments:
        part = MIMEApplication(data, _subtype=sub)
        part["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(part)
    return msg.as_bytes()


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


ADMIN_PASSWORD = "Crimson-2026-seguro"  # el de conftest.ADMIN (cuenta de prueba local)
PDF = b"%PDF-1.4\n% factura de prueba\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def role_headers(client, db_session, tenant_id: int, email: str, role: str) -> dict:
    """Crea un usuario con ese rol y devuelve su encabezado de autorizacion (sin tocar el del admin)."""
    from app.core.security import hash_password
    from app.models import TenantUser, User

    pw = "Rol-prueba-2026"
    u = User(email=email, full_name=role.capitalize(), password_hash=hash_password(pw))
    db_session.add(u)
    db_session.flush()
    db_session.add(TenantUser(tenant_id=tenant_id, user_id=u.id, role_code=role))
    db_session.commit()
    admin = client.headers.pop("Authorization", None)
    tok = client.post("/auth/login", json={"email": email, "password": pw}).json()["access_token"]
    if admin:
        client.headers["Authorization"] = admin
    return {"Authorization": f"Bearer {tok}"}


def load_credentials(client, p12: bytes, pin: str, env: str = "pruebas", user: str = "usuario-pruebas@ejemplo", password: str = "Clave-ATV-de-prueba!", **extra):
    data = {"admin_password": ADMIN_PASSWORD, "atv_user": user, "atv_password": password, "pin": pin, **extra}
    return client.put(f"/settings/hacienda/credentials/{env}", data=data, files={"p12": ("llave.p12", p12, "application/x-pkcs12")})


def modo_pruebas(client, p12: bytes, pin: str):
    r = load_credentials(client, p12, pin)
    assert r.status_code == 200, r.text
    r = client.put("/settings/hacienda/mode", json={"mode": "pruebas"})
    assert r.status_code == 200 and r.json()["mode"] == "pruebas", r.text
