"""Lectura de .p12 (PKCS#12, RFC 7292) sin depender del proveedor "legacy" de OpenSSL (reunion 2, pedido 6).

Por que existe: las llaves criptograficas de Hacienda (ATV/TRIBU-CR) pueden venir cifradas con algoritmos viejos
(pbeWithSHAAnd40BitRC2-CBC para los certificados y 3DES para la llave). OpenSSL 3 dejo RC2 en el proveedor "legacy";
si el servidor no lo carga (CRYPTOGRAPHY_OPENSSL_NO_LEGACY, otra compilacion), cryptography responde lo mismo que con
un PIN incorrecto ("Invalid password or PKCS12 data") y el admin no sabe que pasa. Aqui:

- parse(): lee la estructura (DER o BER con longitudes indefinidas, como los que genera Java). Si no se puede leer,
  el archivo esta danado o no es un .p12 (P12Format).
- mac_ok(): verifica la MAC con el PIN. Si no coincide, el PIN es incorrecto (no el archivo).
- open_p12(): descifra las bolsas con RC2 (40/128 bits, implementado aqui segun RFC 2268), 3DES, RC4 o PBES2
  (PBKDF2 + AES/3DES) y devuelve (llave, certificado, cadena), como pkcs12.load_key_and_certificates.

El PIN y el contenido viven solo en variables locales (ver xades.py). Nada se escribe a disco ni a logs.
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives.ciphers import algorithms as alg
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class P12Format(Exception):
    """El archivo no tiene la forma de un .p12 (danado, truncado o de otro tipo)."""


class P12Password(Exception):
    """El PIN no abre el contenido (MAC distinta o relleno invalido al descifrar)."""


class P12Unsupported(Exception):
    """El .p12 usa un algoritmo que no se reconoce."""


# ---------------------------------------------------------------- OID
def _oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for n in parts[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append(0x80 | (n & 0x7F))
            n >>= 7
        out.extend(reversed(chunk))
    return bytes(out)


OID_DATA = _oid("1.2.840.113549.1.7.1")
OID_ENCRYPTED_DATA = _oid("1.2.840.113549.1.7.6")
OID_KEY_BAG = _oid("1.2.840.113549.1.12.10.1.1")
OID_SHROUDED_KEY_BAG = _oid("1.2.840.113549.1.12.10.1.2")
OID_CERT_BAG = _oid("1.2.840.113549.1.12.10.1.3")
OID_SAFE_CONTENTS_BAG = _oid("1.2.840.113549.1.12.10.1.6")
OID_X509_CERT = _oid("1.2.840.113549.1.9.22.1")
OID_PBES2 = _oid("1.2.840.113549.1.5.13")
OID_PBKDF2 = _oid("1.2.840.113549.1.5.12")
# pbeWithSHAAnd...: (cifrado, bytes de la llave)
PKCS12_PBE = {
    _oid("1.2.840.113549.1.12.1.1"): ("rc4", 16),
    _oid("1.2.840.113549.1.12.1.2"): ("rc4", 5),
    _oid("1.2.840.113549.1.12.1.3"): ("3des", 24),
    _oid("1.2.840.113549.1.12.1.4"): ("3des", 16),
    _oid("1.2.840.113549.1.12.1.5"): ("rc2", 16),
    _oid("1.2.840.113549.1.12.1.6"): ("rc2", 5),
}
PBES2_CIPHERS = {
    _oid("2.16.840.1.101.3.4.1.2"): ("aes", 16),
    _oid("2.16.840.1.101.3.4.1.22"): ("aes", 24),
    _oid("2.16.840.1.101.3.4.1.42"): ("aes", 32),
    _oid("1.2.840.113549.3.7"): ("3des", 24),
}
HMAC_PRF = {
    _oid("1.2.840.113549.2.7"): "sha1",
    _oid("1.2.840.113549.2.8"): "sha224",
    _oid("1.2.840.113549.2.9"): "sha256",
    _oid("1.2.840.113549.2.10"): "sha384",
    _oid("1.2.840.113549.2.11"): "sha512",
}
DIGESTS = {
    _oid("1.3.14.3.2.26"): "sha1",
    _oid("2.16.840.1.101.3.4.2.4"): "sha224",
    _oid("2.16.840.1.101.3.4.2.1"): "sha256",
    _oid("2.16.840.1.101.3.4.2.2"): "sha384",
    _oid("2.16.840.1.101.3.4.2.3"): "sha512",
    _oid("1.2.840.113549.2.5"): "md5",
}


# ---------------------------------------------------------------- BER minimo
class Node:
    __slots__ = ("tag", "cons", "content", "children")

    def __init__(self, tag: int, cons: bool, content: bytes, children: list[Node]):
        self.tag, self.cons, self.content, self.children = tag, cons, content, children

    def octets(self) -> bytes:
        """Valor de un OCTET STRING (o de un [n] IMPLICIT OCTET STRING), primitivo o construido por trozos (BER)."""
        if not self.cons:
            return self.content
        return b"".join(c.octets() for c in self.children)

    def integer(self) -> int:
        if self.tag != 0x02 or self.cons:
            raise P12Format("se esperaba un entero")
        return int.from_bytes(self.content, "big", signed=True)

    def kids(self, n: int | None = None) -> list[Node]:
        if not self.cons or (n is not None and len(self.children) < n):
            raise P12Format("estructura incompleta")
        return self.children


def _read(buf: bytes, i: int, depth: int = 0) -> tuple[Node, int]:
    if depth > 40:
        raise P12Format("anidamiento excesivo")
    if i + 2 > len(buf):
        raise P12Format("archivo truncado")
    tag = buf[i]
    if tag & 0x1F == 0x1F:
        raise P12Format("etiqueta no soportada")
    cons = bool(tag & 0x20)
    ln = buf[i + 1]
    i += 2
    if ln == 0x80:  # longitud indefinida (BER): hijos hasta 00 00
        if not cons:
            raise P12Format("longitud indefinida en un valor primitivo")
        kids = []
        while True:
            if i + 2 > len(buf):
                raise P12Format("archivo truncado")
            if buf[i] == 0 and buf[i + 1] == 0:
                return Node(tag, True, b"", kids), i + 2
            k, i = _read(buf, i, depth + 1)
            kids.append(k)
    if ln & 0x80:
        n = ln & 0x7F
        if n > 4 or i + n > len(buf):
            raise P12Format("longitud invalida")
        ln = int.from_bytes(buf[i : i + n], "big")
        i += n
    end = i + ln
    if end > len(buf):
        raise P12Format("archivo truncado")
    if not cons:
        return Node(tag, False, buf[i:end], []), end
    kids = []
    j = i
    while j < end:
        k, j = _read(buf, j, depth + 1)
        kids.append(k)
    if j != end:
        raise P12Format("longitud inconsistente")
    return Node(tag, True, b"", kids), end


def decode(data: bytes) -> Node:
    node, end = _read(data, 0)
    if end != len(data) and any(data[end:]):  # se toleran ceros de relleno al final
        raise P12Format("bytes de mas al final")
    return node


def _oid_of(n: Node) -> bytes:
    if n.tag != 0x06:
        raise P12Format("se esperaba un OID")
    return n.content


# ---------------------------------------------------------------- RC2 (RFC 2268)
PITABLE = bytes.fromhex(
    "d978f9c419ddb5ed28e9fd794aa0d89dc67e37832b76538e624c6488448bfba2179a59f587b34f1361456d8d09817d32bd8f40eb86b77b0bf09521225c6b4e82"
    "54d66593ce60b21c7356c014a78cf1dc1275ca1f3bbee4d1423dd430a33cb6266fbf0eda4669075727f21d9bbc944303f811c7f690ef3ee706c3d52fc8661ed7"
    "08e8eade8052eef784aa72ac354d6a2a961ad2715a1549744b9fd05e0418a4ecc2e0416e0f51cbcc2491af50a1f47039997c3a8523b8b47afc02365b25559731"
    "2d5dfa98e38a92ae05df2910676cbac9d300e6cfe19ea82c6316013f58e289a90d38341bab33ffb0bb480c5fb9b1cd2ec5f3db47e5a59c770aa62068fe7fc1ad"
)


def _rc2_keys(key: bytes, effective_bits: int) -> list[int]:
    t = len(key)
    if not 1 <= t <= 128:
        raise P12Unsupported("llave RC2 invalida")
    lk = bytearray(128)
    lk[:t] = key
    for i in range(t, 128):
        lk[i] = PITABLE[(lk[i - 1] + lk[i - t]) & 0xFF]
    t8 = (effective_bits + 7) // 8
    tm = 0xFF >> (8 * t8 - effective_bits)
    lk[128 - t8] = PITABLE[lk[128 - t8] & tm]
    for i in range(127 - t8, -1, -1):
        lk[i] = PITABLE[lk[i + 1] ^ lk[i + t8]]
    return [lk[2 * i] | (lk[2 * i + 1] << 8) for i in range(64)]


_S = (1, 2, 3, 5)
_M = 0xFFFF


def _rc2_encrypt_block(k: list[int], blk: bytes) -> bytes:
    r = [blk[0] | blk[1] << 8, blk[2] | blk[3] << 8, blk[4] | blk[5] << 8, blk[6] | blk[7] << 8]
    j = 0

    def mix():
        nonlocal j
        for i in range(4):
            x = (r[i] + k[j] + (r[i - 1] & r[i - 2]) + ((~r[i - 1]) & r[i - 3])) & _M
            j += 1
            r[i] = ((x << _S[i]) | (x >> (16 - _S[i]))) & _M

    def mash():
        for i in range(4):
            r[i] = (r[i] + k[r[i - 1] & 63]) & _M

    for rounds in (5, None, 6, None, 5):
        if rounds is None:
            mash()
        else:
            for _ in range(rounds):
                mix()
    return b"".join(x.to_bytes(2, "little") for x in r)


def _rc2_decrypt_block(k: list[int], blk: bytes) -> bytes:
    r = [blk[0] | blk[1] << 8, blk[2] | blk[3] << 8, blk[4] | blk[5] << 8, blk[6] | blk[7] << 8]
    j = 63

    def rmix():
        nonlocal j
        for i in (3, 2, 1, 0):
            x = r[i]
            x = ((x >> _S[i]) | (x << (16 - _S[i]))) & _M
            r[i] = (x - k[j] - (r[i - 1] & r[i - 2]) - ((~r[i - 1]) & r[i - 3])) & _M
            j -= 1

    def rmash():
        for i in (3, 2, 1, 0):
            r[i] = (r[i] - k[r[i - 1] & 63]) & _M

    for rounds in (5, None, 6, None, 5):
        if rounds is None:
            rmash()
        else:
            for _ in range(rounds):
                rmix()
    return b"".join(x.to_bytes(2, "little") for x in r)


def rc2_cbc(key: bytes, iv: bytes, data: bytes, *, encrypt: bool, effective_bits: int | None = None) -> bytes:
    """RC2-CBC sin relleno (el relleno PKCS#7 lo pone o lo quita quien llama)."""
    if len(data) % 8 or len(iv) != 8:
        raise P12Format("bloque RC2 incompleto")
    k = _rc2_keys(key, effective_bits or len(key) * 8)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 8):
        blk = data[i : i + 8]
        if encrypt:
            c = _rc2_encrypt_block(k, bytes(a ^ b for a, b in zip(blk, prev, strict=True)))
            out += c
            prev = c
        else:
            p = _rc2_decrypt_block(k, blk)
            out += bytes(a ^ b for a, b in zip(p, prev, strict=True))
            prev = blk
    return bytes(out)


# ---------------------------------------------------------------- derivacion de llaves PKCS#12 (RFC 7292, B.2)
def bmp_password(pin: str) -> bytes:
    return pin.encode("utf-16-be") + b"\x00\x00"


def pkcs12_kdf(hash_name: str, password: bytes, salt: bytes, ident: int, iterations: int, n: int) -> bytes:
    h = hashlib.new(hash_name)
    u, v = h.digest_size, h.block_size
    d = bytes([ident]) * v

    def stretch(x: bytes) -> bytes:
        if not x:
            return b""
        size = v * ((len(x) + v - 1) // v)
        return (x * (size // len(x) + 1))[:size]

    i_ = bytearray(stretch(salt) + stretch(password))
    out = b""
    while len(out) < n:
        a = hashlib.new(hash_name, d + bytes(i_)).digest()
        for _ in range(iterations - 1):
            a = hashlib.new(hash_name, a).digest()
        out += a
        if len(out) >= n:
            break
        b = int.from_bytes((a * (v // u + 1))[:v], "big")
        for j in range(0, len(i_), v):
            x = (int.from_bytes(i_[j : j + v], "big") + b + 1) % (1 << (8 * v))
            i_[j : j + v] = x.to_bytes(v, "big")
    return out[:n]


def _unpad(data: bytes, block: int) -> bytes:
    if not data or len(data) % block:
        raise P12Password("relleno invalido")
    p = data[-1]
    if not 1 <= p <= block or data[-p:] != bytes([p]) * p:
        raise P12Password("relleno invalido")
    return data[:-p]


def pad(data: bytes, block: int) -> bytes:
    p = block - len(data) % block
    return data + bytes([p]) * p


def _block_cipher(name: str, key: bytes, iv: bytes, data: bytes, decrypt: bool = True) -> bytes:
    if name == "aes":
        c = Cipher(alg.AES(key), modes.CBC(iv))
    elif name == "3des":
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES

        c = Cipher(TripleDES(key), modes.CBC(iv))
    else:
        raise P12Unsupported(name)
    op = c.decryptor() if decrypt else c.encryptor()
    return op.update(data) + op.finalize()


def pbe_decrypt(alg_node: Node, pin: str, data: bytes) -> bytes:
    """Descifra con el AlgorithmIdentifier dado (PKCS#12 PBE o PBES2)."""
    kids = alg_node.kids(1)
    oid = _oid_of(kids[0])
    if oid in PKCS12_PBE:
        name, klen = PKCS12_PBE[oid]
        params = kids[1].kids(2)
        salt, iters = params[0].octets(), params[1].integer()
        if not 1 <= iters <= 10_000_000:
            raise P12Format("iteraciones invalidas")
        pw = bmp_password(pin)
        key = pkcs12_kdf("sha1", pw, salt, 1, iters, klen)
        if name == "rc4":
            from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4

            dec = Cipher(ARC4(key), mode=None).decryptor()
            return dec.update(data) + dec.finalize()
        iv = pkcs12_kdf("sha1", pw, salt, 2, iters, 8)
        if len(data) % 8:
            raise P12Password("bloque incompleto")
        raw = rc2_cbc(key, iv, data, encrypt=False) if name == "rc2" else _block_cipher("3des", key, iv, data)
        return _unpad(raw, 8)
    if oid == OID_PBES2:
        params = kids[1].kids(2)
        kdf, enc = params[0].kids(2), params[1].kids(2)
        if _oid_of(kdf[0]) != OID_PBKDF2:
            raise P12Unsupported("derivacion de llave desconocida")
        kp = kdf[1].kids(2)
        salt, iters = kp[0].octets(), kp[1].integer()
        if not 1 <= iters <= 10_000_000:
            raise P12Format("iteraciones invalidas")
        prf = "sha1"
        for extra in kp[2:]:
            if extra.tag == 0x30:
                prf = HMAC_PRF.get(_oid_of(extra.kids(1)[0])) or ""
                if not prf:
                    raise P12Unsupported("funcion de derivacion desconocida")
        enc_oid = _oid_of(enc[0])
        if enc_oid not in PBES2_CIPHERS:
            raise P12Unsupported("cifrado desconocido")
        name, klen = PBES2_CIPHERS[enc_oid]
        iv = enc[1].octets()
        key = PBKDF2HMAC(algorithm=getattr(hashes, prf.upper())(), length=klen, salt=salt, iterations=iters).derive(pin.encode("utf-8"))
        block = 16 if name == "aes" else 8
        if len(data) % block:
            raise P12Password("bloque incompleto")
        return _unpad(_block_cipher(name, key, iv, data), block)
    raise P12Unsupported("cifrado desconocido")


# ---------------------------------------------------------------- PFX
class Pfx:
    def __init__(self, auth_safe: bytes, mac: tuple[str, bytes, bytes, int] | None):
        self.auth_safe = auth_safe  # el contenido que firma la MAC
        self.mac = mac  # (hash, digest, sal, iteraciones)


def parse(data: bytes) -> Pfx:
    """Estructura del .p12. P12Format si no es uno (o esta danado)."""
    if not data or data[0] != 0x30:
        raise P12Format("no es un .p12")
    try:
        root = decode(data)
        kids = root.kids(2)
        if kids[0].integer() != 3:
            raise P12Format("version de .p12 desconocida")
        ci = kids[1].kids(2)
        if _oid_of(ci[0]) != OID_DATA:
            raise P12Format("contenido firmado no soportado")
        explicit = ci[1]
        if explicit.tag != 0xA0:
            raise P12Format("contenido invalido")
        auth_safe = explicit.kids(1)[0].octets()
        mac = None
        if len(kids) > 2:
            md = kids[2].kids(2)
            di = md[0].kids(2)
            hname = DIGESTS.get(_oid_of(di[0].kids(1)[0]))
            if not hname:
                raise P12Unsupported("MAC con un algoritmo desconocido")
            iters = md[2].integer() if len(md) > 2 else 1
            if not 1 <= iters <= 10_000_000:
                raise P12Format("iteraciones invalidas")
            mac = (hname, di[1].octets(), md[1].octets(), iters)
        decode(auth_safe)  # la lista de contenidos tiene que leerse
        return Pfx(auth_safe, mac)
    except (IndexError, ValueError) as e:
        raise P12Format("estructura invalida") from e


def mac_ok(pfx: Pfx, pin: str) -> bool | None:
    """True/False si la MAC coincide con el PIN; None si el .p12 no trae MAC."""
    if pfx.mac is None:
        return None
    hname, digest, salt, iters = pfx.mac
    size = hashlib.new(hname).digest_size
    candidatos = [bmp_password(pin)] + ([b""] if pin == "" else [])
    for pw in candidatos:
        key = pkcs12_kdf(hname, pw, salt, 3, iters, size)
        if hmac.compare_digest(hmac.new(key, pfx.auth_safe, hname).digest(), digest):
            return True
    return False


def _bags(contents: bytes, pin: str, keys: list, certs: list, depth: int = 0) -> None:
    if depth > 4:
        raise P12Format("anidamiento excesivo")
    for bag in decode(contents).kids():
        b = bag.kids(2)
        bid = _oid_of(b[0])
        val = b[1].kids(1)[0]  # [0] EXPLICIT
        if bid == OID_CERT_BAG:
            cb = val.kids(2)
            if _oid_of(cb[0]) == OID_X509_CERT:
                certs.append(x509.load_der_x509_certificate(cb[1].kids(1)[0].octets()))
        elif bid == OID_SHROUDED_KEY_BAG:
            epki = val.kids(2)
            der = pbe_decrypt(epki[0], pin, epki[1].octets())
            try:
                keys.append(serialization.load_der_private_key(der, None))
            except ValueError as e:
                raise P12Password("la llave no se pudo descifrar") from e
        elif bid == OID_KEY_BAG:
            keys.append(serialization.load_der_private_key(_reencode(val), None))
        elif bid == OID_SAFE_CONTENTS_BAG:
            _bags(_reencode(val), pin, keys, certs, depth + 1)


def _encode_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _reencode(n: Node) -> bytes:
    """Vuelve a DER un nodo leido (para pasarlo entero a otra lectura)."""
    if not n.cons:
        return bytes([n.tag]) + _encode_len(len(n.content)) + n.content
    body = b"".join(_reencode(k) for k in n.children)
    return bytes([n.tag]) + _encode_len(len(body)) + body


def open_p12(data: bytes, pin: str):
    """(llave, certificado de esa llave, demas certificados). Lanza P12Format, P12Password o P12Unsupported."""
    pfx = parse(data)
    if mac_ok(pfx, pin) is False:
        raise P12Password("la MAC no coincide")
    keys: list = []
    certs: list = []
    try:
        for ci in decode(pfx.auth_safe).kids():
            c = ci.kids(2)
            kind = _oid_of(c[0])
            inner = c[1].kids(1)[0]
            if kind == OID_DATA:
                _bags(inner.octets(), pin, keys, certs)
            elif kind == OID_ENCRYPTED_DATA:
                ed = inner.kids(2)
                eci = ed[1].kids(3)  # contentType, algoritmo, [0] IMPLICIT contenido
                _bags(pbe_decrypt(eci[1], pin, eci[2].octets()), pin, keys, certs)
            else:
                raise P12Unsupported("contenido no soportado")
    except (IndexError, ValueError) as e:
        raise P12Format("contenido invalido") from e
    if not keys or not certs:
        return (keys[0] if keys else None), (certs[0] if certs else None), certs[1:]
    key = keys[0]
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    mine = next((c for c in certs if c.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) == pub), None)
    if mine is None:
        return key, None, certs
    return key, mine, [c for c in certs if c is not mine]
