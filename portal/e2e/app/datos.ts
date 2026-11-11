/* Catalogo de las pruebas de Playwright. TODO ES FICTICIO: marcas, modelos, codigos y textos inventados, precios de
   2 000 colones o menos (con IVA) y fotos dibujadas aqui mismo (SVG en la propia direccion: nada sale a internet).

   Por que no se usa src/demo/snapshot.json: ese archivo es el catalogo real y se regenera desde produccion
   (`npm run demo:snapshot`); las capturas base cambiarian cada vez que alguien lo refresque, y sus fotos viven en el
   sitio del distribuidor. Este catalogo es chico y estable, y trae a proposito un producto de cada estado que la
   tienda tiene que saber mostrar.

   Formato: el mismo de src/demo/snapshot.json (lo lee src/demo/catalog.ts). DISPONIBILIDAD lleva, por producto, la
   respuesta que da el API real (services/availability.py, audiencia publico, cantidad exacta), porque el mock de la
   demo solo conoce tres estados simplificados.

   Los ids 68, 186 y 240 no se pueden quitar: son las lineas del pedido de ejemplo ORD-000128 de src/demo/mockApi.ts. */
import type { RawCat, RawProduct, Snapshot } from "../../src/demo/catalog";

export const FX = 512;

/** Lo que el API manda en `availability` (routers/store_showcase.AVAIL_KEYS). */
export type Dispo = {
  state: "en_bodega" | "bajo_pedido" | "consultar" | "agotado";
  label: string; label_short?: string; max_qty: number | null; ultimas: boolean; cantidad: number | null; disponible: boolean; accion: string | null; plazo: string | null;
};
const PLAZO = "3-5 días hábiles"; // DEFAULTS["backorder_lead"]
const UMBRAL = 3; // DEFAULTS["low_threshold"]
/** n propias en bodega y, si el proveedor tiene mas con dato vigente, `proveedor` unidades bajo pedido. */
const enBodega = (n: number, proveedor = 0): Dispo => ({
  state: "en_bodega", label: `En bodega · ${n} ${n === 1 ? "disponible" : "disponibles"}`, label_short: n <= UMBRAL ? `${n} en bodega` : "En bodega", max_qty: n + proveedor,
  ultimas: n <= UMBRAL, cantidad: n, disponible: true, accion: "comprar", plazo: null,
});
const bajoPedido = (proveedor: number): Dispo => ({
  state: "bajo_pedido", label: `Bajo pedido · entrega en ${PLAZO}`, label_short: "Bajo pedido", max_qty: proveedor, ultimas: false, cantidad: null, disponible: true, accion: "comprar", plazo: PLAZO,
});
/** «Por confirmar»: el proveedor tiene, pero el dato esta vencido. Es el estado de casi toda la tienda real hoy. */
const CONSULTAR: Dispo = { state: "consultar", label: "Disponibilidad por confirmar", label_short: "Por confirmar", max_qty: 0, ultimas: false, cantidad: null, disponible: false, accion: "whatsapp", plazo: null };
const AGOTADO: Dispo = { state: "agotado", label: "Agotado", label_short: "Agotado", max_qty: 0, ultimas: false, cantidad: null, disponible: false, accion: "whatsapp", plazo: null };

// ---------- fotos: un dibujo por producto, sin texto (el texto cambiaria con las fuentes de cada sistema) ----------
type Forma = "camara" | "domo" | "caja" | "disco" | "red" | "energia" | "audio" | "cable";
const DIBUJO: Record<Forma, string> = {
  camara: '<rect x="90" y="150" width="190" height="100" rx="18"/><circle cx="300" cy="200" r="46"/><rect x="150" y="250" width="26" height="70"/>',
  domo: '<path d="M90 190a110 110 0 0 1 220 0z"/><rect x="70" y="190" width="260" height="34" rx="10"/><circle cx="200" cy="270" r="40"/>',
  caja: '<rect x="70" y="150" width="260" height="110" rx="12"/><rect x="100" y="190" width="120" height="14" rx="7" fill="#fff"/><circle cx="290" cy="205" r="14" fill="#fff"/>',
  disco: '<rect x="110" y="80" width="180" height="240" rx="14"/><circle cx="200" cy="170" r="56" fill="#fff"/><circle cx="200" cy="170" r="12"/>',
  red: '<rect x="50" y="170" width="300" height="70" rx="10"/><g fill="#fff"><rect x="76" y="192" width="26" height="26"/><rect x="118" y="192" width="26" height="26"/><rect x="160" y="192" width="26" height="26"/><rect x="202" y="192" width="26" height="26"/></g>',
  energia: '<rect x="120" y="70" width="160" height="260" rx="14"/><path d="M212 120l-44 84h34l-12 76 48-92h-36z" fill="#fff"/>',
  audio: '<rect x="130" y="60" width="140" height="280" rx="40"/><circle cx="200" cy="140" r="34" fill="#fff"/><circle cx="200" cy="250" r="50" fill="#fff"/>',
  cable: '<circle cx="200" cy="200" r="110" fill="none" stroke-width="34"/><circle cx="200" cy="200" r="40"/>',
};
/** SVG en la propia direccion (data:). `n` cambia el tono para que las miniaturas de una galeria se distingan. */
export function foto(forma: Forma, id: number, n = 0): string {
  const tono = (id * 47 + n * 90) % 360;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" width="400" height="400"><rect width="400" height="400" fill="#fff"/><g fill="hsl(${tono} 28% 38%)" stroke="hsl(${tono} 28% 38%)" stroke-width="0">${DIBUJO[forma]}</g></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

// ---------- categorias (dos niveles, como la tienda real) ----------
export const CATEGORIAS: RawCat[] = [
  { id: 1, name: "Seguridad electrónica", parent: null },
  { id: 11, name: "Cámaras IP", parent: 1 },
  { id: 12, name: "Grabadores", parent: 1 },
  { id: 13, name: "Control de acceso", parent: 1 },
  { id: 2, name: "Redes y conectividad", parent: null },
  { id: 21, name: "Switches", parent: 2 },
  { id: 22, name: "WiFi", parent: 2 },
  { id: 3, name: "Respaldo eléctrico", parent: null },
  { id: 4, name: "Audio", parent: null },
  { id: 5, name: "Almacenamiento", parent: null },
  { id: 6, name: "Cables y accesorios de instalación", parent: null },
];

type Fila = {
  id: number; code: string; name: string; brand: string | null; cat: number; forma: Forma | null;
  /** colones SIN IVA; con el 13 % ninguno pasa de 2 000. 0 = «Precio a consultar». */
  crc: number; dispo: Dispo; desc?: string; fotos?: number;
};

const DESC_CAMARA =
  "- Resolución: 4 MP (2688 x 1520) a 25 cuadros por segundo\n- Lente fijo de 2.8 mm, ángulo de visión de 100°\n- Visión nocturna: IR hasta 30 m y luz blanca híbrida\n" +
  "- Detección de personas y vehículos\n- Micrófono integrado y ranura microSD hasta 256 GB\n- Alimentación PoE (802.3af) o 12 VDC\n- Protección IP67 para exterior\n" +
  "Producto de demostración: los datos de esta ficha son inventados para las pruebas automáticas de la tienda.";

/* Los nombres imitan los dos estilos que conviven en el catalogo real: el del distribuidor (mayusculas, largo, con el
   codigo metido en el nombre) y el corto. Asi las pruebas de scroll ven textos tan incomodos como los de verdad. */
const FILAS: Fila[] = [
  // --- Seguridad electronica · Camaras IP
  { id: 68, code: "DEMO-0068", brand: "Vigía", cat: 11, forma: "camara", crc: 1769, dispo: enBodega(3, 5), fotos: 3, desc: DESC_CAMARA,
    name: "CAMARA BULLET IP 4MP LENTE 2.8MM POE DETECCION DE PERSONAS VG-B4028-LI2U(2.8MM)(O-STD) VISION NOCTURNA HIBRIDA IP67" },
  { id: 69, code: "DEMO-0069", brand: "Vigía", cat: 11, forma: "domo", crc: 1593, dispo: enBodega(1), name: "Cámara domo IP 4 MP PoE con micrófono VG-D4028-MIC",
    desc: "- Resolución 4 MP\n- Lente 2.8 mm\n- IR hasta 30 m\n- PoE\n- Protección IP67 / IK10" },
  { id: 70, code: "DEMO-0070", brand: "Vigía", cat: 11, forma: "camara", crc: 1151, dispo: enBodega(9), name: "Cámara bullet IP 2 MP PoE exterior VG-B2028",
    desc: "- Resolución 2 MP\n- Lente 2.8 mm\n- IR hasta 30 m\n- PoE\n- Protección IP67" },
  { id: 71, code: "DEMO-0071", brand: "Vigía", cat: 11, forma: "domo", crc: 973, dispo: bajoPedido(12), name: "Cámara WiFi interior 2 MP con visión nocturna VG-W2-MINI",
    desc: "- Resolución 2 MP\n- Conexión WiFi de 2.4 GHz\n- Audio de dos vías\n- Graba en microSD" },
  { id: 72, code: "DEMO-0072", brand: "Vigía", cat: 11, forma: "domo", crc: 1760, dispo: CONSULTAR, name: "Cámara PTZ IP 4 MP zoom 25x PoE VG-PTZ425",
    desc: "- Resolución 4 MP\n- Zoom óptico 25x\n- IR hasta 100 m\n- PoE+\n- Protección IP66" },
  // --- Seguridad electronica · Grabadores
  { id: 186, code: "DEMO-0186", brand: "Vigía", cat: 12, forma: "caja", crc: 1700, dispo: enBodega(2, 6), name: "Grabador NVR 8 canales 4K con 8 puertos PoE VG-NVR8/8P",
    desc: "- 8 canales IP hasta 8 MP\n- 8 puertos PoE integrados\n- 1 bahía para disco de hasta 10 TB (no incluido)\n- Salida HDMI 4K" },
  { id: 187, code: "DEMO-0187", brand: "Vigía", cat: 12, forma: "caja", crc: 1765, dispo: bajoPedido(4), name: "Grabador NVR 16 canales VG-NVR16",
    desc: "- 16 canales IP hasta 8 MP\n- 2 bahías para disco (no incluidos)\n- Salida HDMI 4K" },
  // --- Seguridad electronica · Control de acceso
  { id: 130, code: "DEMO-0130", brand: "Vigía", cat: 13, forma: "caja", crc: 1590, dispo: bajoPedido(7), name: "Control de acceso con lector de huella y tarjeta VG-K1T" },
  { id: 131, code: "DEMO-0131", brand: "Vigía", cat: 13, forma: "caja", crc: 1100, dispo: enBodega(3), name: "Cerradura electromagnética 280 kg VG-MAG280" },
  { id: 132, code: "DEMO-0132", brand: "Vigía", cat: 13, forma: "caja", crc: 1755, dispo: CONSULTAR, name: "Videoportero IP con pantalla de 7 pulgadas VG-VP7" },
  // --- Redes y conectividad
  { id: 300, code: "DEMO-0300", brand: "Nodo Norte", cat: 21, forma: "red", crc: 1300, dispo: enBodega(4), name: "Switch PoE 8 puertos gigabit NN-SW8P",
    desc: "- 8 puertos gigabit PoE+ (802.3at)\n- Presupuesto PoE de 96 W\n- Carcasa metálica" },
  { id: 301, code: "DEMO-0301", brand: "Nodo Norte", cat: 21, forma: "red", crc: 1768, dispo: CONSULTAR, name: "Switch PoE 16 puertos administrable NN-SW16P" },
  { id: 302, code: "DEMO-0302", brand: "Nodo Norte", cat: 21, forma: "red", crc: 398, dispo: AGOTADO, name: "Switch 5 puertos gigabit de escritorio NN-SW5" },
  { id: 310, code: "DEMO-0310", brand: "Nodo Norte", cat: 22, forma: "domo", crc: 1420, dispo: bajoPedido(10), name: "Punto de acceso WiFi 6 de techo NN-AP6" },
  { id: 311, code: "DEMO-0311", brand: "Nodo Norte", cat: 22, forma: "red", crc: 1239, dispo: enBodega(6), name: "Router WiFi 6 doble banda AX1800 NN-R1800" },
  // --- Respaldo electrico
  { id: 400, code: "DEMO-0400", brand: "Voltaria", cat: 3, forma: "energia", crc: 1680, dispo: enBodega(2), name: "UPS interactiva 750 VA con regulador VT-750" },
  { id: 401, code: "DEMO-0401", brand: "Voltaria", cat: 3, forma: "energia", crc: 0, dispo: bajoPedido(3), name: "UPS interactiva 1500 VA VT-1500" }, // sin precio
  // --- Audio (en la tienda real casi todo esta «por confirmar»)
  { id: 500, code: "DEMO-0500", brand: "Resonar", cat: 4, forma: "audio", crc: 1740, dispo: CONSULTAR,
    name: "PARLANTE BLUETOOTH PORTATIL RS-P5 4.8W IP68 COLOR CAMUFLADO (RSP5CAMUAM)",
    desc: "- Potencia 4.8 W RMS\n- Bluetooth 5.3\n- Hasta 7 horas de batería\n- Resistente al agua y al polvo (IP68)\n- Carga por USB-C" },
  { id: 501, code: "DEMO-0501", brand: "Resonar", cat: 4, forma: "audio", crc: 1769, dispo: CONSULTAR, name: "Parlante de fiesta 280 W con luces RS-FIESTA280" },
  { id: 502, code: "DEMO-0502", brand: "Resonar", cat: 4, forma: "caja", crc: 1649, dispo: enBodega(1), name: "Barra de sonido 2.1 con subwoofer inalámbrico RS-BAR21" },
  { id: 503, code: "DEMO-0503", brand: "Resonar", cat: 4, forma: null, crc: 885, dispo: bajoPedido(20), name: "Audífonos Bluetooth con cancelación de ruido RS-TUNE" }, // sin foto
  // --- Almacenamiento
  { id: 240, code: "DEMO-0240", brand: "Baúl", cat: 5, forma: "disco", crc: 1500, dispo: enBodega(8), name: "Disco duro de videovigilancia 2 TB BL-VX2000" },
  { id: 241, code: "DEMO-0241", brand: "Baúl", cat: 5, forma: "disco", crc: 1751, dispo: bajoPedido(5), name: "Disco duro de videovigilancia 4 TB BL-VX4000" },
  { id: 242, code: "DEMO-0242", brand: "Baúl", cat: 5, forma: "disco", crc: 440, dispo: enBodega(12), name: "Memoria microSD 128 GB para cámaras BL-SD128" },
  // --- Cables y accesorios de instalacion
  { id: 600, code: "DEMO-0600", brand: "Trenza", cat: 6, forma: "cable", crc: 100, dispo: enBodega(40), name: "Conector RJ45 Cat6 (bolsa de 100) TZ-RJ45-100" },
  { id: 601, code: "DEMO-0601", brand: "Trenza", cat: 6, forma: "cable", crc: 1769, dispo: enBodega(5), name: "Bobina de cable UTP Cat6 305 m exterior TZ-UTP305" },
  { id: 602, code: "DEMO-0602", brand: "Trenza", cat: 6, forma: "caja", crc: 265, dispo: enBodega(25), name: "Caja de paso exterior para cámara TZ-CAJA1" },
  { id: 603, code: "DEMO-0603", brand: null, cat: 6, forma: "caja", crc: 177, dispo: AGOTADO, name: "Soporte de pared para cámara domo TZ-SOP2" },
];

/** El mock de la demo solo distingue en bodega / bajo pedido / agotado; lo que no se puede comprar va como agotado. */
const letra = (d: Dispo): RawProduct["avail"] => (d.state === "en_bodega" ? "d" : d.state === "bajo_pedido" ? "b" : "a");

export const PRODUCTOS: RawProduct[] = FILAS.map((f, i) => ({
  id: f.id, code: f.code, name: f.name, brand: f.brand, desc: f.desc ?? null,
  crc: f.crc, usd: Math.round((f.crc / FX) * 100) / 100, rate: 13,
  images: f.forma ? Array.from({ length: f.fotos ?? 1 }, (_, n) => foto(f.forma!, f.id, n)) : [],
  cat: f.cat, mod: String(i + 1).padStart(6, "0"), avail: letra(f.dispo),
}));

export const DISPONIBILIDAD: Record<number, Dispo> = Object.fromEntries(FILAS.map((f) => [f.id, f.dispo]));

const TERMINOS =
  "Texto de prueba\nEstos términos son un texto inventado para las pruebas automáticas de la tienda. No son los términos reales de ninguna empresa.\n\n" +
  "Pedidos\nUn pedido de prueba no genera cobro, factura ni envío.\n\nCambios\nEste texto puede cambiar en cualquier momento sin afectar a nadie.";
const PRIVACIDAD =
  "Texto de prueba\nEsta política es un texto inventado para las pruebas automáticas. Aquí no se recoge ni se guarda ningún dato personal.";

/** Catalogo en el formato de src/demo/snapshot.json. */
export const CATALOGO: Snapshot = {
  source: "portal/e2e/app/datos.ts (ficticio)", extracted_at: "2026-11-10", fx: FX,
  legal: { terms: TERMINOS, privacy: PRIVACIDAD },
  provinces: {
    "San José": ["Central", "Escazú", "Desamparados", "Pérez Zeledón"],
    Alajuela: ["Central", "San Ramón", "San Carlos"],
    Heredia: ["Central", "Barva", "Santo Domingo"],
    Guanacaste: ["Liberia", "Nicoya", "Santa Cruz"],
  },
  outside_gam: { provinces: ["Guanacaste"], cantons: ["Pérez Zeledón", "San Carlos"] },
  categories: CATEGORIAS,
  products: PRODUCTOS,
};

/** Productos que las pruebas abren por nombre (un estado cada uno). */
export const REF = { comprable: 68, ultimaUnidad: 69, bajoPedido: 71, porConfirmar: 500, agotado: 302, sinPrecio: 401, sinFoto: 503, barraDeSonido: 502 } as const;
