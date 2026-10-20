/* DEMOSTRACION ESTATICA de la tienda: logica del catalogo en el navegador.
   Port fiel (simplificado donde no cambia lo que se ve) de api/app/services/store_showcase.py y de los endpoints
   publicos de api/app/routers/store_showcase.py: tipo de equipo, especificaciones deducidas, busqueda tolerante,
   facetas, ficha, "¿Que necesito ademas?" y el configurador "Arma tu sistema" (build_kit).
   Los datos salen de snapshot.json (catalogo real de la tienda propia, ver portal/scripts/demo_snapshot.py).
   Fuente: GET /public/store/crimson/products — precios en CRC sin IVA, disponibilidad real del proveedor.
   Este archivo NO se usa en el portal ni en la tienda real: solo en el build de la demo (vite.demo.config.ts). */

export type RawProduct = {
  id: number; code: string; name: string; brand: string | null; desc: string | null;
  /** CRC sin IVA (fuente principal de precio desde la tienda real). */
  crc: number;
  /** USD sin IVA conservado por compatibilidad: crc / fx. net() usa crc. */
  usd: number;
  rate: number;
  images: string[]; cat: number | null; mod: string;
  /** 'b' bajo pedido · 'a' agotado · 'd' en bodega / disponible */
  avail: "d" | "b" | "a";
};
export type RawCat = { id: number; name: string; parent: number | null };
export type Snapshot = {
  source: string; extracted_at: string; fx: number; legal: { terms?: string; privacy?: string };
  provinces: Record<string, string[]>; outside_gam: { provinces: string[]; cantons: string[] };
  categories: RawCat[]; products: RawProduct[];
};

type Tuple = (number | string | boolean)[];
const cmp = (a: Tuple, b: Tuple): number => {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i], y = b[i];
    if (x === y) continue;
    if (typeof x === "boolean" || typeof y === "boolean") return Number(x) - Number(y);
    if (typeof x === "number" && typeof y === "number") return x - y;
    return String(x) < String(y) ? -1 : 1;
  }
  return 0;
};
const by = <T,>(key: (x: T) => Tuple) => (a: T, b: T) => cmp(key(a), key(b));
const round2 = (n: number) => Math.round(n * 100) / 100;

// ---------------------------------------------------------------- texto
/** minusculas sin tildes (y sin nada fuera de ASCII, como el encode("ascii", "ignore") del API) */
export const fold = (s: unknown) => String(s ?? "").normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase();
export const compact = (s: unknown) => fold(s).replace(/[^a-z0-9]+/g, "");
export function slugify(s: unknown, limit = 70): string {
  let t = fold(s).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (t.length > limit) { const cut = t.slice(0, limit); const i = cut.lastIndexOf("-"); t = (i >= 0 ? cut.slice(0, i) : cut) || cut; }
  return t || "producto";
}
export const productPath = (p: RawProduct) => `/p/${slugify(p.name)}-${p.id}`;
export const idFromRef = (ref: string) => { const m = /(?:^|-)(\d{1,9})$/.exec(ref || ""); return m ? Number(m[1]) : null; };

// ---------------------------------------------------------------- tipo de equipo y especificaciones
const TYPE_RULES: [string, string[], string[]][] = [
  ["Videoportero", ["videoportero", "intercom", "portero"], []],
  ["Grabador NVR", ["nvr"], []],
  ["Grabador DVR", ["dvr"], []],
  ["Grabador NVR", ["grabador", "grabadora"], ["dvr", "analog"]],
  ["Disco duro", ["disco duro", "hdd", "disco "], ["externo"]],
  ["Disco externo", ["disco duro externo", "disco externo"], []],
  ["Memoria microSD", ["micro sd", "microsd", "sdxc", "sdhc"], []],
  ["Cámara PTZ", ["ptz"], []],
  ["Cámara WiFi", ["wifi", "wi-fi", "inalambric"], ["switch", "router", "punto de acceso", "access point", "nvr", "grabador", "teclado", "alarma"]],
  ["Cámara IP", ["camara", "camera", "bullet", "domo", "dome", "turret"], ["dashcam", "burbuja", "soporte", "caja", "brazo", "montaje"]],
  ["Dashcam", ["dashcam"], []],
  ["Switch PoE", ["switch"], []],
  ["Punto de acceso WiFi", ["punto de acceso", "access point", "ubiwifi", "u6", "u7"], []],
  ["Router", ["router", "gateway"], []],
  ["Inyector PoE", ["inyector"], []],
  ["UPS", ["ups", "back-ups"], []],
  ["Control de acceso", ["control de acceso", "lector", "reconocimiento facial", "terminal"], []],
  ["Cerradura", ["cerradura", "electroiman", "chapa"], []],
  ["Alarma", ["alarma", "panico", "detector", "sensor", "sirena", "teclado"], []],
  ["Gabinete", ["gabinete", "rack"], []],
  ["Cable", ["cable", "bobina", "utp"], []],
  ["Conector", ["conector", "rj45", "rj-45"], []],
];
const typeCache = new Map<number, string>();
export function productType(p: RawProduct): string {
  const hit = typeCache.get(p.id);
  if (hit !== undefined) return hit;
  const n = ` ${fold(p.name)} `;
  let out = "";
  for (const [label, must, never] of TYPE_RULES) {
    if (must.some((w) => n.includes(w)) && !never.some((w) => n.includes(w))) {
      out = label === "Switch PoE" && !n.includes("poe") ? "Switch"
        : label === "Cámara IP" && (n.includes("turbo") || n.includes("analog") || n.includes("hd-tvi")) ? "Cámara analógica" : label;
      break;
    }
  }
  if (!out && /^(soporte|caja|brazo|base|tornillo|abrazadera)/.test(fold(p.name))) out = "Accesorio";
  typeCache.set(p.id, out);
  return out;
}

const RX = {
  mp: /(\d{1,2}(?:[.,]\d)?)\s?mp\b/,
  k: /\b([2-8])k\b/,
  p: /\b(1080|720|2160)\s?p\b/,
  lensRange: /(\d{1,2}(?:[.,]\d{1,2})?)\s?(?:mm)?\s?(?:a|-|~|to)\s?(\d{1,2}(?:[.,]\d{1,2})?)\s?mm/,
  lens: /(\d{1,2}[.,]\d{1,2}|\d)\s?mm\b/,
  ir: /\b(?:ir|exir|infrarrojo|vision nocturna)\b[^0-9]{0,18}(\d{2,3})\s?(?:m|mts|metros)\b|(\d{2,3})\s?(?:m|mts|metros)\s?(?:de\s)?(?:ir|infrarrojo)/,
  ip: /\bip\s?(6[5-8])\b/,
  ik: /\bik\s?(0[6-9]|10)\b/,
  ch: /(\d{1,2})\s?(?:-\s?)?(?:ch|canales|canal)\b/,
  tb: /(\d{1,2})\s?tb\b/,
  gb: /(\d{2,4})\s?gb\b/,
  ports: /(\d{1,2})\s?(?:puertos|ptos|port|-port|p\b)/,
  va: /(\d{3,4})\s?va\b/,
  zoom: /(\d{1,2})\s?x\b/,
};
const num = (s: string) => s.replace(",", ".");

export function deriveSpecs(p: RawProduct): Record<string, string> {
  const name = fold(p.name);
  const text = `${name} \n ${fold(p.desc || "").slice(0, 1500)}`;
  const t = productType(p);
  const out: Record<string, string> = {};
  if (t) out["Tipo"] = t;
  if (p.brand) out["Marca"] = p.brand;
  const cam = t.startsWith("Cámara") || t === "Videoportero" || t === "Dashcam";
  const rec = t.startsWith("Grabador");
  let m: RegExpExecArray | null;
  if (cam || rec) {
    if ((m = RX.mp.exec(name) || RX.mp.exec(text))) out["Resolución"] = `${num(m[1])} MP`;
    else if ((m = RX.k.exec(text))) out["Resolución"] = `${m[1]}K`;
    else if ((m = RX.p.exec(text))) out["Resolución"] = ({ "1080": "2 MP (1080p)", "720": "1 MP (720p)", "2160": "8 MP (4K)" } as Record<string, string>)[m[1]];
  }
  if (cam) {
    if ((m = RX.lensRange.exec(text))) out["Lente"] = `${num(m[1])}–${num(m[2])} mm varifocal`;
    else if ((m = RX.lens.exec(text))) out["Lente"] = `${num(m[1])} mm`;
    if ((m = RX.ir.exec(text))) out["Visión nocturna"] = `IR hasta ${m[1] || m[2]} m`;
    else if (text.includes("colorvu") || text.includes("color nocturn") || text.includes("vision nocturna a color")) out["Visión nocturna"] = "A color";
    else if (text.includes("smart hybrid") || text.includes("luz hibrida")) out["Visión nocturna"] = "Luz híbrida inteligente";
    else if (text.includes("vision nocturna") || text.includes(" ir ")) out["Visión nocturna"] = "Sí";
    if (t === "Cámara PTZ" && (m = RX.zoom.exec(text))) out["Zoom óptico"] = `${m[1]}×`;
    if (text.includes("bullet")) out["Formato"] = "Bullet";
    else if (text.includes("turret") || text.includes("eyeball")) out["Formato"] = "Turret";
    else if (text.includes("domo") || text.includes("dome")) out["Formato"] = "Domo";
    if (text.includes("acusense") || text.includes("deteccion humana") || text.includes("deteccion de personas") || text.includes(" ia")) out["Analítica"] = "Detección de personas y vehículos";
    if (text.includes("microfono") || text.includes("audio")) out["Audio"] = "Micrófono integrado";
  }
  if ((m = RX.ip.exec(text))) { const ik = RX.ik.exec(text); out["Protección"] = `IP${m[1]}${ik ? ` / IK${ik[1]}` : ""}`; }
  if (text.includes("poe") && t !== "UPS") out["PoE"] = "Sí";
  else if (cam && (text.includes("12 vdc") || text.includes("12v"))) out["Alimentación"] = "12 VDC";
  if (text.includes("wifi") || text.includes("wi-fi")) out["Conexión"] = "WiFi";
  if (rec && (m = RX.ch.exec(text))) out["Canales"] = m[1];
  if (t === "Disco duro" || t === "Disco externo" || t === "Memoria microSD") {
    if ((m = RX.tb.exec(text))) out["Capacidad"] = `${m[1]} TB`;
    else if ((m = RX.gb.exec(text))) out["Capacidad"] = `${m[1]} GB`;
  }
  if (t.startsWith("Switch") && (m = RX.ports.exec(name))) out["Puertos"] = m[1];
  if (t === "UPS" && (m = RX.va.exec(text))) out["Capacidad"] = `${m[1]} VA`;
  return out;
}
const capacityTb = (p: RawProduct) => { const m = RX.tb.exec(fold(p.name)); return m ? Number(m[1]) : null; };
function channels(p: RawProduct): number | null {
  const m = RX.ch.exec(fold(p.name)) || /\b(4|8|16|32|64)\s?(?:camaras)/.exec(fold(p.name));
  if (m) return Number(m[1]);
  const c = /7(?:1|6)(04|08|16|32)n/.exec(compact(p.code));
  return c ? Number(c[1]) : null;
}
const portsOf = (p: RawProduct) => { const m = RX.ports.exec(fold(p.name)); return m ? Number(m[1]) : null; };

// ---------------------------------------------------------------- busqueda tolerante
const SYNONYMS: Record<string, string[]> = {
  grabador: ["nvr", "dvr", "grabador", "grabadora"], grabadora: ["nvr", "dvr", "grabador", "grabadora"],
  camara: ["camara", "camera", "cam"], camaras: ["camara", "camera", "cam"], disco: ["disco", "hdd"], ups: ["ups", "back-ups"],
  wifi: ["wifi", "wi-fi", "inalambric"], acceso: ["acceso", "lector", "facial"],
};
const escapeRx = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export const splitTerms = (q: string) => (q || "").trim().split(/[\s,;/]+/).filter(Boolean).slice(0, 8);

// ---------------------------------------------------------------- el "servidor" de la demo
export const AVAIL = {
  d: { state: "en_bodega", label: "En bodega", max_qty: null },
  b: { state: "bajo_pedido", label: "Bajo pedido · 3 a 5 días hábiles", max_qty: null },
  a: { state: "agotado", label: "Agotado", max_qty: 0 },
} as const;

const DEFAULT_KITS = [
  { id: "casa-4", title: "Casa segura · 4 cámaras", text: "2 adentro, 2 afuera, 15 días de grabación y vista desde el celular.", answers: { place: "casa", indoor: 2, outdoor: 2, days: 15, remote: true } },
  { id: "negocio-8", title: "Negocio · 8 cámaras", text: "Caja, bodega y entradas cubiertas. 30 días de grabación.", answers: { place: "negocio", indoor: 4, outdoor: 4, days: 30, remote: true } },
  { id: "bodega-16", title: "Bodega · 16 cámaras", text: "Perímetro, patio y andenes. Grabación de un mes.", answers: { place: "bodega", indoor: 4, outdoor: 12, days: 30, remote: true } },
];
export const DEFAULT_TRUST = [
  { icon: "shield", title: "Garantía real", text: "Equipo nuevo con garantía de fábrica. Si falla, nosotros hacemos el trámite." },
  { icon: "tool", title: "Soporte técnico local", text: "Técnicos de Crimson en Costa Rica, por WhatsApp, teléfono o en sitio." },
  { icon: "truck", title: "Envío o retiro", text: "Envío a todo el país o retiro coordinado. Te avisamos cuando sale." },
  { icon: "doc", title: "Factura electrónica", text: "Toda compra lleva su comprobante electrónico de Hacienda." },
];
export const DEFAULT_PAGES: Record<string, string> = {
  envios:
    "Envíos\nEnviamos a todo Costa Rica por Correos de Costa Rica o mensajería. El costo se calcula al finalizar el pedido según el método elegido. También podés retirar en nuestras instalaciones coordinando por WhatsApp.\n\n" +
    "Tiempos\nProducto disponible: sale en 1 a 2 días hábiles después de confirmado el pago. Producto bajo pedido: de 3 a 5 días hábiles; te confirmamos la fecha antes de cobrar.\n\n" +
    "Devoluciones\nSi el producto llega dañado o no corresponde a lo pedido, escribinos en los 8 días naturales siguientes a la entrega con fotos del empaque y del equipo. Lo revisamos y, si procede, lo cambiamos o devolvemos el dinero. No se aceptan devoluciones de equipo instalado, configurado o con el empaque dañado por el cliente, ni de productos hechos o pedidos a la medida.",
  garantias:
    "Garantía de fábrica\nTodo el equipo que vendemos es nuevo y lleva la garantía del fabricante (el plazo depende de la marca y el modelo; te lo indicamos en la factura o al consultar).\n\n" +
    "Cómo hacerla válida\nEscribinos por WhatsApp o reportá la falla en crimsoncr.com con el número de factura y una descripción del problema. Nuestro equipo técnico diagnostica el equipo y gestiona el trámite con el fabricante.\n\n" +
    "Qué no cubre\nDaños por golpes, humedad fuera de la especificación del equipo, descargas eléctricas sin protección, manipulación por terceros o instalación que no siga las indicaciones del fabricante.\n\n" +
    "Instalación por Crimson\nSi nosotros instalamos, la mano de obra tiene su propia garantía y el soporte es directo con el técnico que conoce tu sistema.",
};
export const DEFAULT_FAQ = [
  { q: "¿Los precios incluyen IVA?", a: "Sí. Todos los precios de la tienda incluyen el IVA. En la ficha de cada producto ves el desglose." },
  { q: "¿Emiten factura electrónica?", a: "Sí, toda compra lleva factura o tiquete electrónico de Hacienda. Si necesitás factura a nombre de una empresa, indicá la cédula jurídica al comprar." },
  { q: "¿Ustedes instalan?", a: "Sí. Podés comprar solo el equipo o pedir que Crimson lo instale. Usá “Solicitar instalación” en la ficha o el formulario de asesoría y te cotizamos la mano de obra." },
  { q: "¿Qué significa “bajo pedido”?", a: "Lo traemos del distribuidor para vos. Normalmente tarda de 3 a 5 días hábiles; te confirmamos la fecha antes de cobrar." },
  { q: "¿Puedo ver las cámaras desde el celular?", a: "Sí. Los grabadores y cámaras IP que vendemos se ven desde la app del fabricante (por ejemplo Hik-Connect). Solo necesitás internet en el sitio." },
  { q: "¿Cómo pago?", a: "Por transferencia o SINPE Móvil. Al confirmar el pedido te mostramos los datos y despachamos al recibir el comprobante." },
];
export const INFO_KINDS: Record<string, string> = {
  terminos: "Términos y condiciones", privacidad: "Política de privacidad", envios: "Envíos y devoluciones", garantias: "Garantías", "preguntas-frecuentes": "Preguntas frecuentes",
};

const ACCESSORY_RULES: Record<string, [string, string[], string][]> = {
  "Cámara IP": [["Grabador", ["Grabador NVR"], "Para grabar y ver las cámaras desde el celular."], ["Disco para el grabador", ["Disco duro"], "El grabador no trae disco: elegí la capacidad según los días de grabación."], ["Switch PoE", ["Switch PoE"], "Alimenta la cámara por el mismo cable de red."]],
  "Cámara PTZ": [["Grabador", ["Grabador NVR"], "Para grabar y controlar la PTZ."], ["Disco para el grabador", ["Disco duro"], "Capacidad según días de grabación."]],
  "Cámara analógica": [["Grabador DVR", ["Grabador DVR"], "Las cámaras analógicas graban en un DVR."], ["Disco para el grabador", ["Disco duro"], "El DVR no trae disco."]],
  "Cámara WiFi": [["Memoria microSD", ["Memoria microSD"], "Graba en la propia cámara sin grabador."]],
  "Grabador NVR": [["Disco duro de videovigilancia", ["Disco duro"], "El grabador no trae disco."], ["Cámaras IP", ["Cámara IP"], "Compatibles con el grabador."], ["Respaldo eléctrico", ["UPS"], "Sigue grabando si se va la luz."]],
  "Grabador DVR": [["Disco duro de videovigilancia", ["Disco duro"], "El grabador no trae disco."], ["Respaldo eléctrico", ["UPS"], "Sigue grabando si se va la luz."]],
  "Disco duro": [["Grabador", ["Grabador NVR", "Grabador DVR"], "Donde va instalado el disco."]],
  "Switch PoE": [["Respaldo eléctrico", ["UPS"], "Mantiene la red y las cámaras encendidas sin luz."], ["Conectores y cable", ["Conector", "Cable"], "Para armar el cableado."]],
  "Punto de acceso WiFi": [["Switch PoE", ["Switch PoE", "Inyector PoE"], "Alimenta el punto de acceso por el cable de red."]],
  "Control de acceso": [["Cerradura", ["Cerradura"], "Lo que abre la puerta."], ["Respaldo eléctrico", ["UPS"], "La puerta sigue funcionando sin luz."]],
  "Videoportero": [["Cerradura", ["Cerradura"], "Para abrir desde la pantalla o la app."]],
};

const PLACES: Record<string, { label: string; meters: number }> = {
  casa: { label: "Casa", meters: 20 }, negocio: { label: "Negocio u oficina", meters: 30 },
  condominio: { label: "Condominio", meters: 40 }, bodega: { label: "Bodega o planta", meters: 45 },
};
const GB_PER_CAMERA_DAY = 21;
const RJ45_NO = ["tester", "ponchadora", "crimp", "pinza", "balun", "pareja", "acoplador", "punto de acceso", "router", "onu"];
const FEATURED_TYPES = ["Cámara IP", "Grabador NVR", "Cámara WiFi", "Switch PoE", "Punto de acceso WiFi", "Control de acceso", "Videoportero", "UPS", "Disco duro", "Cámara PTZ"];

export class Catalog {
  readonly fx: number;
  readonly prods: RawProduct[];
  readonly byId = new Map<number, RawProduct>();
  readonly cats: RawCat[];
  readonly catName = new Map<number, string>();
  readonly catSlug = new Map<number, string>();
  private hay = new Map<number, [string, string]>();

  constructor(readonly snap: Snapshot) {
    this.fx = snap.fx;
    this.prods = [...snap.products].sort(by((p) => [p.name])); // el API los entrega ordenados por nombre
    this.prods.forEach((p) => this.byId.set(p.id, p));
    this.cats = snap.categories;
    this.cats.forEach((c) => this.catName.set(c.id, c.name));
    const seen = new Set<string>();
    [...this.cats].sort((a, b) => a.id - b.id).forEach((c) => { let s = slugify(c.name, 60); if (seen.has(s)) s = `${s}-${c.id}`; seen.add(s); this.catSlug.set(c.id, s); });
  }

  // ---------- precios: CRC sin IVA -> colones con IVA ----------
  /** Precio neto en CRC sin IVA. Usa `crc` si el producto lo trae; si no, usd × fx (catálogo legacy). */
  net(p: RawProduct) { return "crc" in p && p.crc > 0 ? p.crc : round2(p.usd * this.fx); }
  gross(p: RawProduct) { return round2(this.net(p) * (1 + p.rate / 100)); }

  card(p: RawProduct, full = false) {
    const desc = p.desc && !full && p.desc.length > 180 ? `${p.desc.slice(0, 177).trimEnd()}…` : p.desc;
    const netPrice = this.net(p);
    const grossPrice = this.gross(p);
    // price y currency: CRC cuando el snapshot trae `crc`; USD si es catálogo legacy
    const hasCrc = "crc" in p && p.crc > 0;
    return {
      id: p.id, code: p.code, name: p.name, price: hasCrc ? netPrice : p.usd, currency: hasCrc ? "CRC" : "USD",
      featured: false, description: desc,
      image: p.images[0] || null, images: full ? p.images : p.images.slice(0, 2), category_id: p.cat, category: p.cat ? this.catName.get(p.cat) ?? null : null,
      brand: p.brand, tax_rate: p.rate, item_type: "producto", variants: [], availability: { ...AVAIL[p.avail] },
      display_currency: "CRC", display_price: grossPrice, path: productPath(p), model: null, type: productType(p),
      compare_display_price: null, price_on_request: grossPrice <= 0,
      category_path: p.cat && this.catSlug.has(p.cat) ? `/c/${this.catSlug.get(p.cat)}` : null,
    };
  }

  // ---------- arbol de categorias ----------
  private kids(): Map<number | null, RawCat[]> {
    const k = new Map<number | null, RawCat[]>();
    for (const c of [...this.cats].sort(by((c) => [c.name]))) {
      const parent = c.parent && this.catName.has(c.parent) ? c.parent : null;
      if (!k.has(parent)) k.set(parent, []);
      k.get(parent)!.push(c);
    }
    return k;
  }
  counts(prods = this.prods) { const m = new Map<number, number>(); prods.forEach((p) => { if (p.cat) m.set(p.cat, (m.get(p.cat) || 0) + 1); }); return m; }
  tree(counts: Map<number, number>, images: Map<number, string> = new Map()) {
    const kids = this.kids();
    const total = (c: RawCat): number => (counts.get(c.id) || 0) + (kids.get(c.id) || []).reduce((a, k) => a + total(k), 0);
    type Node = { id: number; name: string; slug: string; path: string; count: number; image: string | null; children: Node[] };
    const node = (c: RawCat): Node => ({
      id: c.id, name: c.name, slug: this.catSlug.get(c.id)!, path: `/c/${this.catSlug.get(c.id)}`, count: total(c),
      image: images.get(c.id) || (kids.get(c.id) || []).map((k) => images.get(k.id)).find(Boolean) || null,
      children: (kids.get(c.id) || []).filter((k) => total(k)).map(node),
    });
    return (kids.get(null) || []).filter((c) => total(c)).map(node).sort((a, b) => b.count - a.count);
  }
  descendants(cid: number): Set<number> {
    const out = new Set([cid]); const todo = [cid];
    while (todo.length) { const cur = todo.pop()!; for (const c of this.cats) if (c.parent === cur && !out.has(c.id)) { out.add(c.id); todo.push(c.id); } }
    return out;
  }

  // ---------- busqueda ----------
  private haystack(p: RawProduct): [string, string] {
    let h = this.hay.get(p.id);
    if (!h) {
      const raw = [p.name, p.code, p.brand, p.cat ? this.catName.get(p.cat) : "", productType(p)].filter(Boolean).join(" ");
      h = [` ${fold(raw)} `, compact(raw)];
      this.hay.set(p.id, h);
    }
    return h;
  }
  score(p: RawProduct, terms: string[]): number {
    const [, comp] = this.haystack(p);
    const name = fold(p.name);
    const codes = [compact(p.code)];
    let score = 1;
    for (const t of terms) {
      const c = compact(t);
      if (!c) continue;
      const alts = SYNONYMS[c] || [c];
      const variants = new Set(alts.map(compact));
      if (c.length > 4 && c.endsWith("s")) variants.add(c.slice(0, -1));
      if (c.length > 5 && c.endsWith("es")) variants.add(c.slice(0, -2));
      if (![...variants].some((v) => v && comp.includes(v))) return 0;
      if (codes.some((cd) => cd && cd.startsWith(c))) score += 40;
      else if (codes.some((cd) => cd && cd.includes(c))) score += 20;
      if ([...variants].some((v) => compact(name).includes(v))) score += 6;
      if (new RegExp(`\\b${escapeRx(fold(t))}\\b`).test(name)) score += 4;
    }
    return score;
  }

  rankDefault = (p: RawProduct): Tuple => [!p.images.length, !(p.usd > 0), p.name.toLowerCase()];

  // ---------- portada ----------
  defaultFeatured(limit = 8): RawProduct[] {
    const out: RawProduct[] = [];
    for (const t of FEATURED_TYPES) {
      const c = this.prods.filter((p) => this.net(p) > 0 && p.images.length && p.avail !== "a" && productType(p) === t).sort((a, b) => this.net(a) - this.net(b));
      if (c.length) out.push(c[Math.floor(c.length / 2)]);
      if (out.length >= limit) break;
    }
    return out;
  }
  showcase() {
    const counts = this.counts();
    const images = new Map<number, string>();
    const brands = new Map<string, number>();
    for (const p of this.prods) {
      if (p.cat && !images.has(p.cat) && p.images.length && p.usd > 0) images.set(p.cat, p.images[0]);
      if (p.brand) brands.set(p.brand, (brands.get(p.brand) || 0) + 1);
    }
    const featured = this.defaultFeatured();
    const fresh = [...this.prods].sort(by((p) => [p.mod, p.id])).reverse().filter((p) => p.images.length && !featured.includes(p)).slice(0, 8);
    const kits = DEFAULT_KITS.map((k) => {
      const kit = this.buildKit(k.answers);
      const lines = kit.lines.filter((l) => l.product.usd > 0);
      const total = lines.reduce((a, l) => a + this.gross(l.product) * l.quantity, 0);
      const cam = lines.find((l) => l.role.startsWith("Cámaras"))?.product;
      return { id: k.id, title: k.title, text: k.text, answers: kit.answers, items: lines.reduce((a, l) => a + l.quantity, 0), total: Math.round(total), image: cam?.images[0] || null, cameras: kit.cameras };
    });
    return {
      headline: "", subline: "", banners: [], trust: DEFAULT_TRUST,
      categories: this.tree(counts, images),
      brands: [...brands.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14).map(([name, count]) => ({ name, count })),
      featured: featured.slice(0, 8).map((p) => this.card(p)),
      fresh: fresh.map((p) => this.card(p)),
      kits, product_count: this.prods.length, currency: "CRC",
      info: Object.entries(INFO_KINDS).map(([kind, title]) => ({ kind, title, path: `/info/${kind}` })),
    };
  }

  // ---------- catalogo con filtros ----------
  catalog(sp: URLSearchParams): { body: unknown; total: number } | { error: number; detail: string } {
    const idsQ = sp.get("ids");
    if (idsQ) {
      const rows = idsQ.split(",").map((x) => Number(x.trim())).filter(Boolean).slice(0, 12).map((i) => this.byId.get(i)).filter((p): p is RawProduct => !!p);
      return { body: { items: rows.map((p) => ({ ...this.card(p, true), specs: deriveSpecs(p) })), total: rows.length }, total: rows.length };
    }
    const nav = this.tree(this.counts()).map((c) => ({ name: c.name, path: c.path, count: c.count }));
    let prods = this.prods;
    let cat: RawCat | null = null;
    const category = sp.get("category");
    if (category) {
      const cid = [...this.catSlug.entries()].find(([, s]) => s === category)?.[0] ?? (/^\d+$/.test(category) && this.catName.has(Number(category)) ? Number(category) : null);
      if (!cid) return { error: 404, detail: "Categoría no encontrada" };
      cat = this.cats.find((c) => c.id === cid)!;
      const allowed = this.descendants(cid);
      prods = prods.filter((p) => p.cat !== null && allowed.has(p.cat));
    }
    const terms = splitTerms(sp.get("q") || "");
    const scores = new Map<number, number>();
    if (terms.length) {
      for (const p of prods) { const s = this.score(p, terms); if (s) scores.set(p.id, s); }
      prods = prods.filter((p) => scores.has(p.id));
    }
    const brandsSel = new Set(sp.getAll("brand").filter(Boolean).map((b) => b.toLowerCase()));
    const type = sp.get("type") || "";
    const minP = sp.get("min_price") ? Number(sp.get("min_price")) : null;
    const maxP = sp.get("max_price") ? Number(sp.get("max_price")) : null;
    const avail = sp.get("avail") || "";
    const st = (p: RawProduct) => AVAIL[p.avail].state;
    const keep = (p: RawProduct, skip = "") => {
      if (skip !== "brand" && brandsSel.size && !brandsSel.has((p.brand || "").toLowerCase())) return false;
      if (skip !== "type" && type && productType(p) !== type) return false;
      if (skip !== "price") {
        const g = this.gross(p);
        if (minP !== null && g < minP) return false;
        if (maxP !== null && (g > maxP || g <= 0)) return false;
      }
      if (skip !== "avail" && avail) {
        if ((avail === "disponible" || avail === "inmediata" || avail === "en_bodega") && st(p) !== "en_bodega") return false;
        if (avail === "bajo_pedido" && !["en_bodega", "bajo_pedido"].includes(st(p))) return false;
      }
      return true;
    };
    let rows = prods.filter((p) => keep(p));
    const fb = new Map<string, number>(), ft = new Map<string, number>();
    const fav = { disponible: 0, bajo_pedido: 0, agotado: 0 };
    const priced: number[] = [];
    for (const p of prods) {
      if (p.brand && keep(p, "brand")) fb.set(p.brand, (fb.get(p.brand) || 0) + 1);
      const t = productType(p);
      if (t && keep(p, "type")) ft.set(t, (ft.get(t) || 0) + 1);
      if (keep(p, "avail")) {
        const s = st(p);
        if (s === "en_bodega") fav.disponible++;
        if (s !== "agotado") fav.bajo_pedido++;
        if (s === "agotado") fav.agotado++;
      }
      if (keep(p, "price") && this.gross(p) > 0) priced.push(this.gross(p));
    }
    const sort = sp.get("sort") || "relevance";
    if (sort === "relevance") rows = [...rows].sort(by((p) => [-(scores.get(p.id) || 0), ...this.rankDefault(p)]));
    else if (sort === "name") rows = [...rows].sort(by((p) => [p.name.toLowerCase()]));
    else if (sort === "price_asc" || sort === "price_desc") {
      const con = rows.filter((p) => this.gross(p) > 0).sort((a, b) => (sort === "price_desc" ? -1 : 1) * (this.gross(a) - this.gross(b)));
      rows = [...con, ...rows.filter((p) => this.gross(p) <= 0)];
    } else rows = [...rows].sort(by((p) => [p.mod, p.id])).reverse();
    const limit = Math.max(1, Math.min(60, Number(sp.get("limit")) || 24));
    const offset = Math.max(0, Number(sp.get("offset")) || 0);
    const total = rows.length;
    const kidsOf = this.cats.filter((c) => cat && c.parent === cat.id);
    return {
      total,
      body: {
        items: rows.slice(offset, offset + limit).map((p) => this.card(p)), total, offset,
        category: cat ? {
          id: cat.id, name: cat.name, slug: this.catSlug.get(cat.id), description: null,
          parent: cat.parent && this.catName.has(cat.parent) ? { name: this.catName.get(cat.parent), path: `/c/${this.catSlug.get(cat.parent)}` } : null,
          children: kidsOf.filter((c) => this.prods.some((p) => p.cat === c.id)).map((c) => ({ name: c.name, path: `/c/${this.catSlug.get(c.id)}` })),
        } : null,
        facets: {
          brands: [...fb.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).slice(0, 30).map(([name, count]) => ({ name, count })),
          types: [...ft.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([name, count]) => ({ name, count })),
          availability: fav,
          price: { min: priced.length ? Math.min(...priced) : 0, max: priced.length ? Math.max(...priced) : 0 },
        },
        currency: "CRC", nav,
      },
    };
  }

  // ---------- ficha ----------
  item(ref: string) {
    const pid = idFromRef(ref);
    const p = pid ? this.byId.get(pid) : undefined;
    if (!p) return null;
    const ptype = productType(p);
    let related = this.prods.filter((r) => r.id !== p.id && r.cat === p.cat && productType(r) === ptype && r.images.length);
    related = related.concat(this.prods.filter((r) => r.id !== p.id && !related.includes(r) && r.cat === p.cat && r.images.length));
    related = related.concat(this.prods.filter((r) => r.id !== p.id && !related.includes(r) && !!p.brand && r.brand === p.brand && r.images.length));
    related = related.slice(0, 24).sort(by((r) => [r.brand !== p.brand, ...this.rankDefault(r)])).slice(0, 8);
    const groups = (ACCESSORY_RULES[ptype] || []).map(([title, kinds, reason]) => {
      const cands = this.prods.filter((r) => r.id !== p.id && kinds.includes(productType(r)) && r.usd > 0).sort(by((r) => [r.brand !== p.brand, !r.images.length, r.usd]));
      return { title, reason, items: cands.slice(0, 3) };
    }).filter((g) => g.items.length);
    const out = this.card(p, true);
    const gross = out.display_price;
    const net = round2(gross / (1 + p.rate / 100));
    const crumbs: { name: string; path: string }[] = [{ name: "Tienda", path: "/" }];
    const chain: RawCat[] = [];
    let cur = p.cat ? this.cats.find((c) => c.id === p.cat) : undefined;
    while (cur && chain.length < 4) { chain.unshift(cur); cur = cur.parent ? this.cats.find((c) => c.id === cur!.parent) : undefined; }
    chain.forEach((c) => crumbs.push({ name: c.name, path: `/c/${this.catSlug.get(c.id)}` }));
    return {
      ...out, description: p.desc, specs: deriveSpecs(p), datasheet_url: null, net_price: net, tax_amount: round2(gross - net),
      breadcrumbs: crumbs, related: related.map((r) => this.card(r)),
      accessories: groups.map((g) => ({ title: g.title, reason: g.reason, items: g.items.map((r) => this.card(r)) })),
    };
  }

  // ---------- "Arma tu sistema" ----------
  private priced() { return this.prods.filter((p) => this.net(p) > 0 && p.avail !== "a"); }
  private pickCamera(outdoor: boolean) {
    const cams = this.priced().filter((p) => {
      const n = fold(p.name);
      const poe = fold(`${p.name} ${p.desc || ""}`).includes("poe") || n.includes("acusense");
      if (productType(p) !== "Cámara IP" || !poe) return false;
      return !["ptz", "wifi", "panoram", "fisheye", "lpr", "anpr", "termic", "kit"].some((w) => n.includes(w));
    });
    const rank = (p: RawProduct): Tuple => {
      const n = fold(p.name);
      const fmtOk = outdoor ? n.includes("bullet") : ["domo", "dome", "turret", "eyeball"].some((w) => n.includes(w));
      const mp = RX.mp.exec(n);
      const v = mp ? Number(num(mp[1])) : 0;
      return [!fmtOk, v ? Math.abs(v - 4) : 9, p.usd];
    };
    return cams.sort(by(rank))[0] || null;
  }
  private pickRecorder(n: number): [RawProduct | null, boolean] {
    const best: [number, boolean, number, RawProduct, boolean][] = [];
    for (const p of this.priced()) {
      const fn = fold(p.name);
      if (productType(p) !== "Grabador NVR" || fn.includes("wifi") || fn.includes("inalambric")) continue;
      const ch = channels(p);
      if (!ch || ch < n) continue;
      const poe = /\/\d{1,2}p\b/.test(fold(`${p.code} ${p.name}`)) || (fn.includes(" poe") && fn.includes("switch poe"));
      best.push([ch, !poe, p.usd, p, poe]);
    }
    if (!best.length) return [null, false];
    const b = best.sort(by((x) => [x[0], x[1], x[2]]))[0];
    return [b[3], b[4]];
  }
  private pickDisk(needTb: number): [RawProduct | null, number] {
    const disks = this.priced().filter((p) => productType(p) === "Disco duro" && capacityTb(p)).map((p) => [capacityTb(p)!, p] as [number, RawProduct]);
    if (!disks.length) return [null, 0];
    const surv0 = disks.filter(([, p]) => ["purple", "skyhawk", "vigilancia", "vx"].some((w) => fold(p.name).includes(w)));
    const surv = surv0.length ? surv0 : disks;
    const fits = surv.filter(([c]) => c >= needTb).sort(by(([c, p]) => [c, p.usd]));
    if (fits.length) return [fits[0][1], 1];
    const [c, p] = surv.reduce((a, b) => (b[0] > a[0] ? b : a));
    return [p, Math.max(1, Math.ceil(needTb / c))];
  }
  private pickSwitch(need: number) {
    const sw = this.priced().filter((p) => productType(p) === "Switch PoE").map((p) => [portsOf(p) || 0, p] as [number, RawProduct]).filter(([n]) => n >= need && n <= 24);
    return sw.sort(by(([n, p]) => [n, p.usd]))[0]?.[1] || null;
  }
  private pickByWords(words: string[], never: string[] = []) {
    return this.priced().filter((p) => { const n = fold(p.name); return words.every((w) => n.includes(w)) && !never.some((w) => n.includes(w)); }).sort((a, b) => a.usd - b.usd)[0] || null;
  }

  buildKit(answers: Record<string, unknown>) {
    const place = typeof answers.place === "string" && PLACES[answers.place] ? answers.place : "casa";
    let indoor = Math.max(0, Math.min(32, Math.trunc(Number(answers.indoor) || 0)));
    const outdoor = Math.max(0, Math.min(32, Math.trunc(Number(answers.outdoor) || 0)));
    if (indoor + outdoor === 0) indoor = 1;
    const n = indoor + outdoor;
    const days = Math.max(1, Math.min(90, Math.trunc(Number(answers.days) || 15)));
    const remote = answers.remote !== false;
    const meters = PLACES[place].meters;
    const lines: { product: RawProduct; quantity: number; role: string; reason: string }[] = [];
    const missing: { name: string; quantity: number; reason: string; role: string }[] = [];
    const add = (p: RawProduct | null, qty: number, role: string, reason: string, fallback: string) => {
      if (!p) missing.push({ name: fallback, quantity: qty, reason, role });
      else lines.push({ product: p, quantity: Math.ceil(qty), role, reason });
    };
    // mismas cantidades que survey_specs.suggest_materials para puntos de CCTV PoE
    const nvrCh = [4, 8, 16, 32, 64].find((c) => c >= n) || 64;
    const swPorts = [8, 16, 24].find((c) => c > n) || 24;
    const codes = (k: number) => { const all = Array.from({ length: k }, (_, i) => `C${i + 1}`); return all.length > 6 ? `${all.slice(0, 6).join(", ")} y ${all.length - 6} más` : all.join(", "); };

    if (outdoor) add(this.pickCamera(true), outdoor, "Cámaras exteriores", `${outdoor} punto(s) afuera: formato bullet, resistente al agua`, "Cámara IP exterior PoE 4 MP");
    if (indoor) add(this.pickCamera(false), indoor, "Cámaras interiores", `${indoor} punto(s) adentro: formato domo, discreto`, "Cámara IP interior PoE 4 MP");
    const [nvr, nvrPoe] = this.pickRecorder(n);
    const nvrLabel = `NVR ${nvrCh} canales`;
    add(nvr, 1, "Grabador", `${n} cámara(s): ${nvrLabel.toLowerCase()}${nvrPoe ? " con PoE integrado (no hace falta switch)" : ""}`, nvrLabel);
    const needTb = Math.round(n * days * GB_PER_CAMERA_DAY / 1000 * 1.1 * 10) / 10;
    const [disk, diskQty] = this.pickDisk(needTb);
    add(disk, diskQty || 1, "Almacenamiento", `${days} días × ${n} cámara(s) ≈ ${needTb.toFixed(1)} TB en grabación continua`, `Disco de videovigilancia ${Math.ceil(needTb)} TB`);
    if (!nvrPoe) {
      const swName = `Switch PoE ${swPorts} puertos`;
      add(this.pickSwitch(Math.min(swPorts, n)), 1, "Red", `alimenta las ${n} cámaras por el mismo cable (PoE)`, swName);
    }
    const rj = this.pickByWords(["conector", "rj45"], RJ45_NO) || this.pickByWords(["rj45"], [...RJ45_NO, "switch", "puerto", "modulo", "ficha"]);
    add(rj && rj.usd <= 2 ? rj : null, 2 * n, "Instalación", `2 por cámara (${n} cámaras)`, "Conectores RJ45");
    const cableM = Math.round(n * meters * 1.15);
    const bobina = this.pickByWords(["bobina"], ["coaxial"]) || this.pickByWords(["cable", "305"]);
    if (bobina) add(bobina, Math.max(1, Math.ceil(cableM / 305)), "Instalación", `${cableM} m de cable UTP (${meters} m por cámara + 15 %)`, "Cable UTP");
    else add(this.pickByWords(["cable", "utp"], ["patch", "hdmi", "coaxial"]), cableM, "Instalación", `${cableM} m de cable UTP (${meters} m por cámara + 15 %)`, `Cable UTP Cat6 (${cableM} m)`);
    if (outdoor) add(this.pickByWords(["caja", "exterior"], ["tapa", "fuerte", "carton"]), outdoor, "Instalación", `cámaras en exterior: ${codes(outdoor)}`, "Caja de paso exterior");
    const notes = [
      "Kit sugerido: un técnico de Crimson lo valida antes de cobrar o instalar (distancias, alturas y red del sitio).",
      `Almacenamiento estimado con grabación continua a 4 MP en H.265+ (~${GB_PER_CAMERA_DAY} GB por cámara al día). Con grabación por movimiento rinde 2 a 3 veces más.`,
    ];
    if (remote) notes.push("Vista desde el celular: los grabadores sugeridos usan la app gratuita del fabricante; solo necesitás internet en el sitio.");
    return { answers: { place, indoor, outdoor, days, remote }, place_label: PLACES[place].label, cameras: n, storage_tb: needTb.toFixed(1), lines, missing, notes };
  }

  kitResponse(answers: Record<string, unknown>) {
    const k = this.buildKit(answers);
    let total = 0;
    const lines = k.lines.map((l) => {
      const c = this.card(l.product);
      const sub = round2(c.display_price * l.quantity);
      total += sub;
      return { role: l.role, reason: l.reason, quantity: l.quantity, subtotal: sub, product: c };
    });
    return { ...k, lines, total: round2(total), currency: "CRC" };
  }
}
