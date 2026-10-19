/* Datos de ejemplo del equipo GESTION (Detras de escena, C1 y E1). Copia canonica de la especificacion (3.0, 3.5,
   3.6 y 3.7): productos reales del snapshot (precio sin IVA = usd x 512; con IVA x 1,13), pedidos de ejemplo con
   totales calculados con la misma cuenta que quote() de mockApi, y las reglas reales de estados de
   api/app/services/store_orders.py (STATUS_LABEL, TRANSITIONS, next_states) y de Orders.tsx (ACTION).
   Fechas SIEMPRE relativas a Date.now(). Correos ficticios en @example.com (RFC 2606). */

// ---------------------------------------------------------------- estados reales
export type Estado = "nuevo" | "en_revision" | "pagado" | "preparando" | "listo" | "enviado" | "entregado" | "cancelado" | "reembolsado";
export const ETIQUETA: Record<Estado, string> = {
  nuevo: "Pendiente de pago", en_revision: "Comprobante en revisión", pagado: "Pagado", preparando: "Preparando", listo: "Listo para retiro",
  enviado: "Enviado", entregado: "Entregado", cancelado: "Cancelado", reembolsado: "Reembolsado",
};
export const ES_ESTADO = (s: string): s is Estado => s in ETIQUETA;
/* orden de las pestanas de Orders.tsx */
export const PESTANAS: Estado[] = ["en_revision", "nuevo", "pagado", "preparando", "listo", "enviado", "entregado", "cancelado", "reembolsado"];
const FULFILL: Estado[] = ["preparando", "listo", "enviado", "entregado"];
const TRANSITIONS: Record<Estado, Estado[]> = {
  nuevo: ["en_revision", "pagado", "cancelado", ...FULFILL],
  en_revision: ["pagado", "nuevo", "cancelado"],
  pagado: ["preparando", "listo", "enviado", "entregado", "cancelado", "reembolsado"],
  preparando: ["listo", "enviado", "entregado", "cancelado", "reembolsado"],
  listo: ["enviado", "entregado", "cancelado", "reembolsado"],
  enviado: ["entregado", "reembolsado"],
  entregado: ["reembolsado"],
  cancelado: ["reembolsado"],
  reembolsado: [],
};
/* rotulos de los botones (Orders.tsx -> ACTION) */
export const ACCION: Record<string, string> = {
  nuevo: "Rechazar comprobante", pagado: "Confirmar pago", preparando: "Preparando", listo: "Listo para retiro",
  enviado: "Marcar enviado", entregado: "Entregado", cancelado: "Cancelar pedido", reembolsado: "Registrar reembolso",
};
export const METODO: Record<string, string> = { sinpe: "SINPE Móvil", transferencia: "Transferencia", tarjeta: "Tarjeta", efectivo: "Efectivo" };
export const METODO_LARGO: Record<string, string> = { sinpe: "SINPE Móvil", transferencia: "Transferencia bancaria", tarjeta: "Tarjeta (ONVO)", efectivo: "Efectivo" };
/* reserva sin pago (Inventario web -> Ajustes): SINPE / transferencia en horas, tarjeta en minutos */
export const RESERVA_HORAS = 12;
export const RESERVA_MIN_TARJETA = 30;
export const PAGADOS: Estado[] = ["pagado", "preparando", "listo", "enviado", "entregado"];
export const POR_ATENDER: Estado[] = ["en_revision", "pagado", "preparando"];

/** store_orders.next_states: los saltos que acepta el API, en orden de avance */
export function siguientes(o: { status: Estado; pago: { kind: string }; entrega: { type: string }; paid_at: string | null }): Estado[] {
  const nxt = new Set<Estado>(TRANSITIONS[o.status].filter((s) => s !== "en_revision"));
  if ((o.status === "nuevo" || o.status === "en_revision") && o.pago.kind !== "efectivo") FULFILL.forEach((s) => nxt.delete(s));
  if (o.status === "cancelado" && !o.paid_at) nxt.delete("reembolsado");
  if (o.entrega.type === "envio") nxt.delete("listo");
  else if (o.entrega.type === "retiro") nxt.delete("enviado");
  const orden: Estado[] = ["nuevo", "pagado", "preparando", "listo", "enviado", "entregado", "cancelado", "reembolsado"];
  return orden.filter((s) => nxt.has(s));
}

// ---------------------------------------------------------------- productos reales del snapshot
export type Producto = { id: number; nombre: string; corto: string; code: string; neto: number; conIva: number; disponible: boolean; path: string };
const r2 = (n: number) => Math.round(n * 100) / 100;
const P = (id: number, nombre: string, corto: string, code: string, neto: number, disponible: boolean, slug: string): Producto =>
  ({ id, nombre, corto, code, neto, conIva: r2(neto * 1.13), disponible, path: `/p/${slug}-${id}` });
export const PRODUCTOS: Record<number, Producto> = Object.fromEntries([
  P(53, "Camara IP Hikvision 4MP Acusense Bullet IP67", "Bullet IP67", "DS2CD2043G2LI2U", 55603.2, false, "camara-ip-hikvision-4mp-acusense-bullet-ip67"),
  P(118, "Camara Tipo Domo Hikvision 4MP Acusense Smart Hybrid Lght", "Domo Acusense", "DS-2CD2143G2-LIS2U(2.8mm)", 61327.36, true, "camara-tipo-domo-hikvision-4mp-acusense-smart-hybrid-lght"),
  P(177, "Grabador IP NVR Hikvision 4CH PoE", "Grabador 4CH", "DS-7104NI-Q1/4P", 52684.8, true, "grabador-ip-nvr-hikvision-4ch-poe"),
  P(93, "Grabador Hikvision NVR 8ch 4MP H.265+ 1 SATA PoE", "Grabador 8CH", "DS-7108NI-Q1/8P", 64000, false, "grabador-hikvision-nvr-8ch-4mp-h-265-1-sata-poe"),
  P(11, "Disco duro 2TB Western Digital Sata Purple", "Disco 2TB", "CRWD23PURZ", 43765.76, false, "disco-duro-2tb-western-digital-sata-purple"),
  P(10, "Disco duro de 4TB Western Digital Purple", "Disco 4TB", "WD44PURZ", 87040, false, "disco-duro-de-4tb-western-digital-purple"),
  P(12, "Disco duro 6TB Western Digital Sata Purple", "Disco duro 6TB", "CRWD64PURZ", 121687.04, false, "disco-duro-6tb-western-digital-sata-purple"),
  P(92, "CABLE U/UTP CAT5E HIKVISION 305 METROS", "Cable UTP 305 m", "DS-1LN5EUEC0", 40673.28, false, "cable-u-utp-cat5e-hikvision-305-metros"),
  P(318, "CAJA CUADRADA EXTERIOR FS TEKLINK-T 3 ENTRADAS 1/2 PULGADA INCLUYE 2 TAPONES DE CIERRE 2G50-3", "Caja exterior", "2G50-3", 3584, true, "caja-cuadrada-exterior-fs-teklink-t-3-entradas-1-2-pulgada"),
  P(108, "UPS APC BACK-UPS 1600VA / 960W 120V AVR", "UPS APC 1600VA", "BX1600M-LM", 97280, false, "ups-apc-back-ups-1600va-960w-120v-avr"),
  P(36, "UPS HIKVISION DS-UPS1000-X", "UPS Hikvision", "DS-UPS1000-X", 25600, false, "ups-hikvision-ds-ups1000-x"),
  P(40, "Camara Bullet Easy Link para exteriores 4MP", "Bullet Easy Link", "DS-2CV1043G2LIDWF28B", 24064, false, "camara-bullet-easy-link-para-exteriores-4mp"),
  P(39, "Camara Turret Easy Link 4MP Interiores", "Turret Easy Link", "DS-2CV1F43G2LIDWF28", 24064, false, "camara-turret-easy-link-4mp-interiores"),
  P(5, "Kit Easylink WiFi de 4 MP, Bullet Exterior - Domo Interiores", "Kit Easylink WiFi", "DS-J142I/NKS424W02H", 55296, true, "kit-easylink-wifi-de-4-mp-bullet-exterior-domo-interiores"),
  P(215, "Cámara Bullet 4 MP ColorVu 3.0", "Bullet ColorVu", "DS-2CD1047G3-LIU", 47104, false, "camara-bullet-4-mp-colorvu-3-0"),
  P(8, "UniFi U7-Pro Punto de Acceso WiFi 7 Ubiquiti", "UniFi U7-Pro", "U7PRO", 145459.2, true, "unifi-u7-pro-punto-de-acceso-wifi-7-ubiquiti"),
].map((p) => [p.id, p]));

// ---------------------------------------------------------------- pedidos
export type Linea = { name: string; quantity: number; unit_price: number; total: number; product_id?: number; envio?: boolean };
export type Hist = { status: Estado; at: string; by: string | null; note?: string };
export type Correo = { status: "enviado" | "simulado"; subject: string; to: string; at: string };
export type Bitacora = { at: string; quien: string; texto: string };
export type PedidoGes = {
  numero: string; token?: string; demo: boolean;
  cliente: { name: string; email: string; company?: string; id_number?: string };
  entrega: { type: "envio" | "retiro"; method: string; province?: string; canton?: string; district?: string };
  pago: { kind: string; label: string };
  lineas: Linea[]; subtotal: number; envio: number; iva: number; total: number;
  created_at: string; status: Estado; paid_at: string | null; expires_at: string | null;
  tracking_number: string | null; carrier: string | null; cancel_reason: string | null; payment_ref: string | null; refund: number | null;
  history: Hist[]; proofs: { filename: string; at: string }[]; correos: Correo[]; bitacora: Bitacora[];
  factura: string | null; facturaAuto?: boolean; requiereCompra: string | null; margen: number | null;
};
const ENVIOS: Record<string, number> = { "Retiro en Alajuela": 0, "Envío GAM": 3500, "Envío fuera de la GAM": 5500 };
function armar(items: [number, number][], envio: string) {
  const lineas: Linea[] = [];
  let subtotal = 0, iva = 0;
  for (const [id, q] of items) {
    const p = PRODUCTOS[id];
    const net = r2(p.neto * q), tax = r2(net * 0.13);
    subtotal += net; iva += tax;
    lineas.push({ name: p.nombre, quantity: q, unit_price: p.neto, total: r2(net + tax), product_id: id });
  }
  const sh = ENVIOS[envio] || 0, shTax = r2(sh * 0.13);
  iva += shTax;
  if (sh) lineas.push({ name: envio, quantity: 1, unit_price: sh, total: r2(sh + shTax), envio: true });
  subtotal = r2(subtotal); iva = r2(iva);
  return { lineas, subtotal, envio: sh, iva, total: r2(subtotal + sh + iva) };
}
const MIN = 60_000;
const iso = (ms: number) => new Date(ms).toISOString();
const PLANTILLA: Partial<Record<Estado, string>> = {
  nuevo: "rechazó el comprobante de {n}", en_revision: "subió el comprobante de {n}", pagado: "confirmó el pago de {n}",
  preparando: "pasó {n} a «Preparando»", listo: "marcó {n} como «Listo para retiro»", enviado: "marcó {n} como enviado",
  entregado: "marcó {n} como entregado", cancelado: "canceló {n}", reembolsado: "registró el reembolso de {n}",
};
export const textoBitacora = (s: Estado, n: string) => (PLANTILLA[s] || `cambió {n} a «${ETIQUETA[s]}»`).replace("{n}", n);

type Paso = { s: Estado; min: number; by: string | null; note?: string };
type Def = {
  numero: string; cliente: PedidoGes["cliente"]; entrega: PedidoGes["entrega"]; pago: string; items: [number, number][];
  haceMin: number; flujo: Paso[]; factura?: string; tracking?: string; cancel?: string; requiereCompra?: string; margen: number | null;
};
const GAM_ALAJUELA = { type: "envio" as const, method: "Envío GAM", province: "Alajuela", canton: "Alajuela", district: "San Rafael" };
const RETIRO = { type: "retiro" as const, method: "Retiro en Alajuela" };
const MARIA = { name: "María Rodríguez", email: "maria@example.com" };
const correoCliente = (n: string, s: Estado) => `${s === "nuevo" ? "Recibimos tu pedido" : ETIQUETA[s]} · ${n}`;

function construir(d: Def, ahora: number): PedidoGes {
  const created = ahora - d.haceMin * MIN;
  const t = armar(d.items, d.entrega.method);
  const ultimo = d.flujo[d.flujo.length - 1];
  const status = ultimo.s;
  const history: Hist[] = d.flujo.map((f) => ({ status: f.s, at: iso(created + f.min * MIN), by: f.by, ...(f.note ? { note: f.note } : {}) }));
  const pago = d.flujo.find((f) => f.s === "pagado");
  const paid_at = pago ? iso(created + pago.min * MIN) : null;
  const revision = d.flujo.find((f) => f.s === "en_revision");
  const correos: Correo[] = [
    { status: "enviado", subject: correoCliente(d.numero, "nuevo"), to: d.cliente.email, at: iso(created) },
    { status: "enviado", subject: `Pedido web nuevo · ${d.numero}`, to: "equipo de ventas de Crimson", at: iso(created) },
    ...d.flujo.filter((f) => f.s !== "nuevo" && f.s !== "en_revision").map((f) => ({ status: "enviado" as const, subject: correoCliente(d.numero, f.s), to: d.cliente.email, at: iso(created + f.min * MIN) })),
  ];
  const bitacora: Bitacora[] = d.flujo.filter((f) => f.by).map((f) => ({ at: iso(created + f.min * MIN), quien: f.by!, texto: textoBitacora(f.s, d.numero) }));
  if (pago && d.factura) bitacora.push({ at: iso(created + pago.min * MIN + MIN), quien: "Sistema", texto: `emitió la factura electrónica ${d.factura} (ejemplo) de ${d.numero}` });
  bitacora.sort((a, b) => (a.at < b.at ? -1 : 1));
  return {
    numero: d.numero, demo: false, cliente: d.cliente, entrega: d.entrega, pago: { kind: d.pago, label: METODO_LARGO[d.pago] },
    ...t, created_at: iso(created), status, paid_at,
    expires_at: status === "nuevo" ? iso(created + (d.pago === "tarjeta" ? RESERVA_MIN_TARJETA : RESERVA_HORAS * 60) * MIN) : null,
    tracking_number: d.flujo.some((f) => f.s === "enviado") ? d.tracking || null : null,
    carrier: d.flujo.some((f) => f.s === "enviado") ? "Correos de Costa Rica" : null,
    cancel_reason: status === "cancelado" ? d.cancel || null : null, payment_ref: null, refund: null,
    history, proofs: revision ? [{ filename: d.numero === "ORD-000128" ? "comprobante-sinpe-ejemplo.jpg" : `comprobante-${d.numero.toLowerCase()}.jpg`, at: iso(created + revision.min * MIN) }] : [],
    correos, bitacora, factura: pago ? d.factura || null : null, requiereCompra: d.requiereCompra || null, margen: d.margen,
  };
}

/* ORD-000128 de Maria: los mismos desfases que el pedido de ejemplo de mockApi ("Mi pedido"), para que los dos
   lados cuenten la misma historia. created_at = ahora - (desfase del estado + 15 min). */
export const CAMINO_128: Estado[] = ["nuevo", "en_revision", "pagado", "preparando", "enviado", "entregado"];
const OFF_128: Record<string, number> = { nuevo: 0, en_revision: 25, pagado: 120, preparando: 22 * 60, enviado: 46 * 60, entregado: 70 * 60 };
const POR_128: Record<string, string | null> = { nuevo: null, en_revision: "María (cliente)", pagado: "Andrés", preparando: "Andrés", enviado: "Andrés", entregado: "Andrés" };
export function pedido128(k: Estado, ahora = Date.now()): PedidoGes {
  const hasta = CAMINO_128.includes(k) ? CAMINO_128.indexOf(k) : 1;
  const flujo: Paso[] = CAMINO_128.slice(0, hasta + 1).map((s) => ({ s, min: OFF_128[s], by: POR_128[s] }));
  const ult = CAMINO_128[hasta];
  return construir({
    numero: "ORD-000128", cliente: MARIA, entrega: GAM_ALAJUELA, pago: "sinpe", items: [[53, 2], [177, 1], [11, 1]],
    haceMin: OFF_128[ult] + 15, flujo, factura: "FE-000231", tracking: "EJ012844710CR",
    requiereCompra: "2 × Bullet IP67, 1 × Disco 2TB", margen: 0.36,
  }, ahora);
}

export function pedidosEjemplo(ahora = Date.now()): PedidoGes[] {
  const H = 60;
  return [
    pedido128("en_revision", ahora),
    construir({ numero: "ORD-000127", cliente: { name: "Jorge Vargas", email: "jorge@example.com" }, entrega: RETIRO, pago: "tarjeta", items: [[118, 1]],
      haceMin: 3 * H, flujo: [{ s: "nuevo", min: 0, by: null }, { s: "pagado", min: 2, by: "ONVO (pago en línea; ejemplo para cuando se contrate)" }], factura: "FE-000230", margen: 0.30 }, ahora),
    construir({ numero: "ORD-000126", cliente: { name: "Laura Méndez", company: "Comercial Ejemplo S.A.", id_number: "3-101-000000 (ejemplo)", email: "compras@example.com" },
      entrega: { type: "envio", method: "Envío GAM", province: "San José", canton: "San José", district: "Catedral" }, pago: "transferencia",
      items: [[53, 4], [118, 4], [93, 1], [12, 1], [92, 1], [318, 4]], haceMin: 5 * H, flujo: [{ s: "nuevo", min: 0, by: null }],
      factura: "FE-000232", requiereCompra: "4 productos", margen: 0.35 }, ahora),
    construir({ numero: "ORD-000125", cliente: { name: "Ana Solano", email: "ana@example.com" }, entrega: RETIRO, pago: "efectivo", items: [[5, 1]],
      haceMin: 26 * H, flujo: [{ s: "nuevo", min: 0, by: null }, { s: "preparando", min: 3 * H, by: "Andrés" }, { s: "listo", min: 20 * H, by: "Andrés" }], margen: 0.22 }, ahora),
    construir({ numero: "ORD-000124", cliente: { name: "Carlos Jiménez", email: "carlos@example.com" },
      entrega: { type: "envio", method: "Envío fuera de la GAM", province: "Guanacaste", canton: "Liberia", district: "Liberia" }, pago: "sinpe", items: [[8, 1]],
      haceMin: 52 * H, flujo: [{ s: "nuevo", min: 0, by: null }, { s: "en_revision", min: 30, by: "Carlos (cliente)" }, { s: "pagado", min: 2 * H, by: "Andrés" },
        { s: "preparando", min: 20 * H, by: "Andrés" }, { s: "enviado", min: 46 * H, by: "Andrés" }], factura: "FE-000227", tracking: "EJ012790115CR", margen: 0.37 }, ahora),
    construir({ numero: "ORD-000123", cliente: { name: "Sofía Mora", email: "sofia@example.com" },
      entrega: { type: "envio", method: "Envío GAM", province: "Heredia", canton: "Heredia", district: "Mercedes" }, pago: "tarjeta", items: [[177, 1]],
      haceMin: 96 * H, flujo: [{ s: "nuevo", min: 0, by: null }, { s: "pagado", min: 2, by: "ONVO (pago en línea; ejemplo para cuando se contrate)" }, { s: "preparando", min: 18 * H, by: "Andrés" },
        { s: "enviado", min: 26 * H, by: "Andrés" }, { s: "entregado", min: 50 * H, by: "Andrés" }], factura: "FE-000226", tracking: "EJ012733904CR", margen: 0.34 }, ahora),
    construir({ numero: "ORD-000122", cliente: { name: "Diego Castro", email: "diego@example.com" },
      entrega: { type: "envio", method: "Envío GAM", province: "San José", canton: "Desamparados", district: "San Miguel" }, pago: "sinpe", items: [[39, 2]],
      haceMin: 102 * H, flujo: [{ s: "nuevo", min: 0, by: null }, { s: "cancelado", min: RESERVA_HORAS * H, by: "Sistema", note: "No se recibió el pago a tiempo; la reserva venció sola." }],
      cancel: "No se recibió el pago a tiempo; la reserva venció sola.", margen: null }, ahora),
    construir({ numero: "ORD-000121", cliente: { name: "Valeria Rojas", email: "valeria@example.com" },
      entrega: { type: "envio", method: "Envío GAM", province: "Cartago", canton: "Cartago", district: "Oriental" }, pago: "transferencia", items: [[12, 1]],
      haceMin: 116 * H, flujo: [{ s: "nuevo", min: 0, by: null }, { s: "en_revision", min: 60, by: "Valeria (cliente)" }, { s: "pagado", min: 3 * H, by: "Andrés" }],
      factura: "FE-000229", requiereCompra: "1 × Disco duro 6TB", margen: 0.35 }, ahora),
  ];
}
/* numero de factura que sale al confirmar el pago (los de ejemplo traen el suyo; los de prueba toman uno libre) */
export const FACTURA_DE: Record<string, string> = { "ORD-000128": "FE-000231", "ORD-000126": "FE-000232" };

// ---------------------------------------------------------------- inventario web (WebInventory.tsx)
export type FilaInv = { id: number; bodega: number; proveedor: string | null };
/* "En bodegas web" con los pedidos de ejemplo en su estado inicial. Lo reservado y lo que "requiere compra" se
   calcula de los pedidos (Pedidos y Detras de escena cuentan la misma historia). */
export const INVENTARIO: FilaInv[] = [
  { id: 118, bodega: 9, proveedor: null },
  { id: 177, bodega: 2, proveedor: null },
  { id: 5, bodega: 3, proveedor: null },
  { id: 8, bodega: 2, proveedor: null },
  { id: 318, bodega: 40, proveedor: null },
  { id: 53, bodega: 0, proveedor: "Eurocomp · 120 u. · hace 2 días (cargado a mano)" },
  { id: 11, bodega: 0, proveedor: "Eurocomp · 35 u. · hace 2 días (cargado a mano)" },
  { id: 12, bodega: 0, proveedor: "Eurocomp · 8 u. · hace 6 días (cargado a mano)" },
  { id: 108, bodega: 0, proveedor: "Eurocomp · 6 u. · hace 3 días (cargado a mano)" },
];
export const RESUMEN_INV = { publicados: 429, disponible: 152, bajo_pedido: 277, consultar: 0, agotado: 0, alertas: 0 };
export const AJUSTES_INV: { label: string; valor: string; tipo: "check" | "campo" }[] = [
  { label: "Reservar inventario al recibir pedidos web", valor: "sí", tipo: "check" },
  { label: "Bloquear ventas sin existencia (nunca sobrevender)", valor: "no", tipo: "check" },
  { label: "Cantidad en la tienda", valor: "«Últimas unidades» bajo el umbral", tipo: "campo" },
  { label: "Umbral de últimas unidades", valor: "2", tipo: "campo" },
  { label: "Plazo bajo pedido", valor: "3 a 5 días hábiles", tipo: "campo" },
  { label: "Dato del proveedor vigente (días)", valor: "7", tipo: "campo" },
  { label: "Reserva sin pago: SINPE / transferencia (horas)", valor: String(RESERVA_HORAS), tipo: "campo" },
  { label: "Reserva sin pago: tarjeta (minutos)", valor: String(RESERVA_MIN_TARJETA), tipo: "campo" },
  { label: "Cancelar el pedido cuando vence la reserva sin pago", valor: "sí", tipo: "check" },
  { label: "Vender bajo pedido lo que tiene el proveedor (genera solicitud de compra)", valor: "sí", tipo: "check" },
];

// ---------------------------------------------------------------- Mi Tienda
export const TIENDA = {
  name: "Crimson", tagline: "Seguridad electrónica, redes y soporte técnico en Costa Rica.", primary: "#e2233a", secondary: "#15131a",
  kind: "tienda", font: "Arial", currency: "CRC", domain: "tienda.crimsoncr.com",
};
export const DESTACADOS = [
  "KIT EASYLINK NVS IP HIKVISION DS-J142(STD)/NKS422W02H BULLET 2MP",
  "NVR Hikvision AcuSense de 8 Canales IP 8MP con AcuSense y Switch PoE",
  "Cámara wifi exterior 3K, EZVIZ, Deteción de humanos y vehículos,",
  "Switch Hikvision 16 Puertos Gigabit PoE+ / Administrable / Hasta 300 metros",
  "Punto de acceso WiFi 7 Versión Lite-, para montaje en cielorrazo,",
  "Terminal de huellas dactilares y control de acceso K1T807MBFWX-E1",
  "VIDEO INTERCOM 4-WIRE ANALOG KIT / DS-KIS302-P",
  "UPS CDP 1000VA/500W 6 OUTLET R-SMART1010",
];
export type Kit = { id: string; title: string; text: string; place: string; indoor: number; outdoor: number; days: number };
export const KITS: Kit[] = [
  { id: "casa-4", title: "Casa segura · 4 cámaras", text: "2 adentro, 2 afuera, 15 días de grabación y vista desde el celular.", place: "casa", indoor: 2, outdoor: 2, days: 15 },
  { id: "negocio-8", title: "Negocio · 8 cámaras", text: "Caja, bodega y entradas cubiertas. 30 días de grabación.", place: "negocio", indoor: 4, outdoor: 4, days: 30 },
  { id: "bodega-16", title: "Bodega · 16 cámaras", text: "Perímetro, patio y andenes. Grabación de un mes.", place: "bodega", indoor: 4, outdoor: 12, days: 30 },
];
export const LUGARES: Record<string, string> = { casa: "Casa", negocio: "Negocio u oficina", condominio: "Condominio", bodega: "Bodega o planta" };
export const TARIFAS = [
  { name: "Retiro en Alajuela", amount: 0, zone: "Retiro en sitio" },
  { name: "Envío GAM", amount: 3500, zone: "Solo GAM" },
  { name: "Envío fuera de la GAM", amount: 5500, zone: "Fuera de GAM" },
];
export const BLOQUES: Record<string, string> = {
  hero: "Portada (hero)", text: "Texto", image_text: "Imagen + texto", cta: "Llamado a la acción", columns: "Columnas", products: "Productos", gallery: "Galería", form: "Formulario",
};

// ---------------------------------------------------------------- C1 · tablero (datos ficticios)
export type Periodo = "7" | "30" | "90";
export const PERIODOS: Periodo[] = ["7", "30", "90"];
export const KPI: Record<Periodo, { visitas: number; pedidos: number; ventas: number; abandonados: number; conContacto: number; margen: number }> = {
  "7": { visitas: 760, pedidos: 4, ventas: 468_000, abandonados: 7, conContacto: 5, margen: 0.341 },
  "30": { visitas: 3150, pedidos: 18, ventas: 2_196_000, abandonados: 28, conContacto: 20, margen: 0.346 },
  "90": { visitas: 8940, pedidos: 49, ventas: 5_978_000, abandonados: 79, conContacto: 55, margen: 0.344 },
};
export const EMBUDO: Record<Periodo, [string, number][]> = {
  "7": [["Visitas", 760], ["Vieron un producto", 357], ["Agregaron al carrito", 27], ["Iniciaron el pago", 11], ["Pedidos", 4]],
  "30": [["Visitas", 3150], ["Vieron un producto", 1480], ["Agregaron al carrito", 112], ["Iniciaron el pago", 46], ["Pedidos", 18]],
  "90": [["Visitas", 8940], ["Vieron un producto", 4200], ["Agregaron al carrito", 318], ["Iniciaron el pago", 128], ["Pedidos", 49]],
};
/* [producto, vistas, al carrito, vendidas] */
export const MAS_VISTO: Record<Periodo, [number, number, number, number][]> = {
  "7": [[40, 99, 2, 1], [5, 77, 2, 1], [53, 69, 2, 1], [177, 58, 1, 1], [215, 54, 1, 0], [8, 48, 0, 0]],
  "30": [[40, 412, 9, 5], [5, 318, 6, 3], [53, 287, 7, 4], [177, 241, 4, 3], [215, 226, 5, 2], [8, 198, 2, 1]],
  "90": [[40, 1170, 26, 13], [5, 902, 17, 8], [53, 815, 20, 11], [177, 684, 11, 8], [215, 641, 14, 5], [8, 562, 6, 3]],
};
export const CANALES: Record<Periodo, [string, number][]> = {
  "7": [["Tienda en línea", 468_000], ["Cotizaciones y proyectos", 1_620_000], ["Punto de venta", 190_000]],
  "30": [["Tienda en línea", 2_196_000], ["Cotizaciones y proyectos", 6_940_000], ["Punto de venta", 812_500]],
  "90": [["Tienda en línea", 5_978_000], ["Cotizaciones y proyectos", 21_300_000], ["Punto de venta", 2_410_000]],
};
/* tendencia de ventas de la tienda, ultimas 12 semanas, en miles de colones sin IVA */
export const TENDENCIA = [410, 380, 520, 470, 455, 610, 540, 495, 580, 520, 640, 560];
/* margen real de los pedidos web por dia, ultimos 7 dias (el 4o dia baja por ORD-000125) */
export const MARGEN_DIA = [0.348, 0.352, 0.339, 0.29, 0.361, 0.354, 0.346];
export const META = 0.35;

// ---------------------------------------------------------------- E1 · indicadores de ejemplo
export const E1_KPI = [
  { label: "Conversaciones", valor: 46 }, { label: "Resueltas por el asistente", valor: 31 },
  { label: "Pasaron a una persona", valor: 15 }, { label: "Sin respuesta en el catálogo", valor: 4 },
];
