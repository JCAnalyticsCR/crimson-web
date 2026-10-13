/* Candado de la vista previa privada.

   OJO: esto es un candado DISUASIVO del navegador, no seguridad real. Todo el codigo y los datos de la demo
   viajan al navegador de quien abra la URL; cualquiera con las herramientas de desarrollo puede saltarse esta
   pantalla. Sirve para que un enlace reenviado no muestre la demo "en frio" y para que no se indexe por
   accidente, nada mas. Por eso el codigo nunca aparece en texto plano: solo guardamos SHA-256(sal + ":" + codigo).
   Para cambiarlo: node -e "const c=require('crypto');const s=c.randomBytes(16).toString('hex');
   console.log(s, c.createHash('sha256').update(s+':'+process.argv[1]).digest('hex'))" <CODIGO>
   y reemplazar SALT y DIGEST. */

const SALT = "08be5d270fc72a5f0e2088887e38834f";
const DIGEST = "adcd05f660514bc8fd9c66e989f9e87508af8e0fa547c20a9877f7a9d9386138";

export const PIN_LENGTH = 4;
export const MAX_FAILS = 3;
export const LOCK_MS = 30_000;

/** sessionStorage con try/catch: en modo privado o con datos bloqueados puede tirar; la demo sigue igual. */
export const ss = {
  get(k: string): string | null { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { sessionStorage.setItem(k, v); } catch { /* sin almacenamiento: no se recuerda */ } },
  del(k: string) { try { sessionStorage.removeItem(k); } catch { /* idem */ } },
};

export const KEY = {
  ok: "crimson-vip:acceso",
  welcomed: "crimson-vip:bienvenida",
  fails: "crimson-vip:fallos",
  lockUntil: "crimson-vip:espera",
  tour: "crimson-vip:guia-paso",
  guideOff: "crimson-vip:guia-off",
} as const;

const hex = (buf: ArrayBuffer) => Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");

async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  // Web Crypto solo existe en contexto seguro (https o localhost). Si alguien abre la demo por http en la LAN,
  // usamos la implementacion de respaldo de abajo para que el candado no se quede trabado.
  if (globalThis.crypto?.subtle) return hex(await crypto.subtle.digest("SHA-256", data));
  return sha256Fallback(data);
}

/** Comparacion en tiempo constante (por prolijidad; aqui no hay un atacante remoto midiendo tiempos). */
function same(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export async function checkPin(pin: string): Promise<boolean> {
  if (!new RegExp(`^\\d{${PIN_LENGTH}}$`).test(pin)) return false;
  return same(await sha256Hex(`${SALT}:${pin}`), DIGEST);
}

// ---------- SHA-256 de respaldo (FIPS 180-4), ~1 KB; solo corre sin Web Crypto ----------
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
  0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
  0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function sha256Fallback(msg: Uint8Array): string {
  const len = msg.length;
  const total = Math.ceil((len + 9) / 64) * 64;
  const buf = new Uint8Array(total);
  buf.set(msg);
  buf[len] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(total - 8, Math.floor((len * 8) / 2 ** 32));
  view.setUint32(total - 4, (len * 8) >>> 0);
  const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const W = new Uint32Array(64);
  const rotr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) W[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(W[i - 15], 7) ^ rotr(W[i - 15], 18) ^ (W[i - 15] >>> 3);
      const s1 = rotr(W[i - 2], 17) ^ rotr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) >>> 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
  }
  return Array.from(H, (x) => x.toString(16).padStart(8, "0")).join("");
}

/** Solo para pruebas: verifica la implementacion de respaldo contra el vector conocido de "abc". */
export const _selfTestFallback = () =>
  sha256Fallback(new TextEncoder().encode("abc")) === "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
