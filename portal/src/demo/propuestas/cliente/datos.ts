/* Datos canonicos de ejemplo del equipo cliente (maquetas B1 y B2). Copia fiel de la seccion 3.0 de la
   especificacion: productos y precios reales del snapshot (usd x 512 x 1,13), pedido ORD-000128 de Maria con los
   totales que da quote() de mockApi, series EJ-..., numeros de documento de ejemplo.
   Reglas: fechas SIEMPRE relativas a Date.now(); correos ficticios en example.com (RFC 2606); nada de esto es un
   dato de Crimson ni de sus clientes. */
import { desdeAhora } from "./comun";

export const MARIA = { nombre: "María Rodríguez", primer: "María", correo: "maria@example.com", iniciales: "MR", lugar: "San Rafael, Alajuela, Alajuela" };

// ---------------------------------------------------------------- fechas
export const DIA = 86400000;
export function sumarMeses(d: Date, n: number): Date {
  const x = new Date(d.getTime());
  const dia = x.getDate();
  x.setDate(1);
  x.setMonth(x.getMonth() + n);
  const ultimo = new Date(x.getFullYear(), x.getMonth() + 1, 0).getDate();
  x.setDate(Math.min(dia, ultimo));
  return x;
}
export const sumarDias = (d: Date, n: number) => new Date(d.getTime() + n * DIA);
const limpia = (s: string) => s.replace(/\./g, "").replace(/,/g, "").replace(/\s+/g, " ").trim();
/** «11 oct 2027» */
export const fechaAno = (d: Date) => limpia(new Intl.DateTimeFormat("es-CR", { day: "numeric", month: "short", year: "numeric" }).format(d));
/** «martes 14 de abril» */
export const fechaDia = (d: Date) => limpia(new Intl.DateTimeFormat("es-CR", { weekday: "long", day: "numeric", month: "long" }).format(d));
/** «14 de abril de 2027» */
export const fechaLargaSinDia = (d: Date) => new Intl.DateTimeFormat("es-CR", { day: "numeric", month: "long", year: "numeric" }).format(d);
const rtf = new Intl.RelativeTimeFormat("es-CR", { numeric: "always" });
/** «hace 5 días», «hace 4 meses» */
export function haceTanto(d: Date, ahora = Date.now()): string {
  const dias = Math.round((d.getTime() - ahora) / DIA);
  if (dias === 0) return "hoy";
  if (Math.abs(dias) < 45) return rtf.format(dias, "day");
  return rtf.format(Math.round(dias / 30.44), "month");
}
export const diasEntre = (a: Date, b: Date) => Math.round((b.getTime() - a.getTime()) / DIA);

export type Momentos = {
  ahora: Date; compra128: Date; entrega128: Date; garantia128: Date; compra097: Date; entrega097: Date; garantia097: Date;
};
/** La escena de B1, alineada con «Mi pedido» de ejemplo en «Entregado» (mockApi): la compra fue hace 70 h 15 min y la
    entrega hace 15 min; la compra anterior, hace 4 meses. Asi el hilo carmesi cuenta la misma historia en cada pantalla. */
export function momentos(): Momentos {
  const ahora = new Date();
  const compra128 = new Date(ahora.getTime() - (70 * 60 + 15) * 60000);
  const entrega128 = new Date(ahora.getTime() - 15 * 60000);
  const compra097 = sumarMeses(desdeAhora(0, 11, 5), -4);
  const entrega097 = sumarDias(compra097, 2);
  entrega097.setHours(14, 30, 0, 0);
  return { ahora, compra128, entrega128, garantia128: sumarMeses(entrega128, 12), compra097, entrega097, garantia097: sumarMeses(entrega097, 12) };
}

// ---------------------------------------------------------------- pedidos de ejemplo
export type LineaEj = { id: number; nombre: string; cantidad: number; total: number };
export type PedidoEj = {
  numero: "ORD-000128" | "ORD-000097"; factura: string; guia: string | null; transportista: string | null; pago: string; entrega: string;
  lineas: LineaEj[]; envio: { nombre: string; total: number }; subtotal: number; envioSinIva: number; iva: number; total: number;
};
/** ORD-000128: lineas y totales de quote() de mockApi con 2 x 53, 1 x 177, 1 x 11 y «Envio GAM». Total ₡238 607,36. */
export const ORD128: PedidoEj = {
  numero: "ORD-000128", factura: "FE-000231", guia: "EJ012844710CR", transportista: "Correos de Costa Rica", pago: "SINPE Móvil",
  entrega: "Envío GAM · San Rafael, Alajuela, Alajuela",
  lineas: [
    { id: 53, nombre: "Camara IP Hikvision 4MP Acusense Bullet IP67", cantidad: 2, total: 125663.23 },
    { id: 177, nombre: "Grabador IP NVR Hikvision 4CH PoE", cantidad: 1, total: 59533.82 },
    { id: 11, nombre: "Disco duro 2TB Western Digital Sata Purple", cantidad: 1, total: 49455.31 },
  ],
  envio: { nombre: "Envío GAM", total: 3955 }, subtotal: 207656.96, envioSinIva: 3500, iva: 27450.4, total: 238607.36,
};
/** ORD-000097: la compra anterior de Maria, 1 x UPS HIKVISION DS-UPS1000-X con «Envio GAM». Total ₡32 883. */
export const ORD097: PedidoEj = {
  numero: "ORD-000097", factura: "FE-000164", guia: null, transportista: null, pago: "SINPE Móvil",
  entrega: "Envío GAM · San Rafael, Alajuela, Alajuela",
  lineas: [{ id: 36, nombre: "UPS HIKVISION DS-UPS1000-X", cantidad: 1, total: 28928 }],
  envio: { nombre: "Envío GAM", total: 3955 }, subtotal: 25600, envioSinIva: 3500, iva: 3783, total: 32883,
};
export const articulos = (p: PedidoEj) => p.lineas.reduce((a, l) => a + l.cantidad, 0);

// ---------------------------------------------------------------- equipos con serie (se crean al despachar)
export type TipoEquipo = "camara" | "grabador" | "disco" | "ups";
export type Equipo = { serie: string; id: number; nombre: string; tipo: TipoEquipo; pedido: "ORD-000128" | "ORD-000097"; nota?: string };
export const EQUIPOS: Equipo[] = [
  { serie: "EJ-53-0001", id: 53, nombre: "Camara IP Hikvision 4MP Acusense Bullet IP67", tipo: "camara", pedido: "ORD-000128" },
  { serie: "EJ-53-0002", id: 53, nombre: "Camara IP Hikvision 4MP Acusense Bullet IP67", tipo: "camara", pedido: "ORD-000128" },
  { serie: "EJ-177-0001", id: 177, nombre: "Grabador IP NVR Hikvision 4CH PoE", tipo: "grabador", pedido: "ORD-000128" },
  { serie: "EJ-11-0001", id: 11, nombre: "Disco duro 2TB Western Digital Sata Purple", tipo: "disco", pedido: "ORD-000128", nota: "Instalado en el grabador" },
  { serie: "EJ-36-0001", id: 36, nombre: "UPS HIKVISION DS-UPS1000-X", tipo: "ups", pedido: "ORD-000097" },
];
export const EQUIPO_FALLA = "EJ-53-0002";
export const MOTIVOS: Record<TipoEquipo, string[]> = {
  camara: ["No da imagen", "Se ve borrosa de noche", "No conecta al celular", "Otro"],
  grabador: ["No graba", "No se ve desde el celular", "Hace un pitido", "Otro"],
  disco: ["El grabador no lo reconoce", "Se llena muy rápido", "Otro"],
  ups: ["No enciende", "Pita seguido", "La batería dura poco", "Otro"],
};
export const TEXTO_FALLA: Record<TipoEquipo, string> = {
  camara: "Desde ayer la cámara del portón se ve en negro de noche.",
  grabador: "Desde ayer el grabador no muestra las cámaras en el celular.",
  disco: "El grabador dice que el disco no está disponible.",
  ups: "Cuando se va la luz, la UPS pita y se apaga a los pocos minutos.",
};
export const TICKET = "TCK-2026-0542";
export const TIPO_TXT: Record<TipoEquipo, string> = { camara: "Cámara", grabador: "Grabador", disco: "Disco", ups: "UPS" };

// ---------------------------------------------------------------- B2: plan de mantenimiento
export type CobroPlan = "tarjeta" | "factura";
export type PlanB2 = { v: 1; nombre: string; precioSinIva: number; visitasAno: number; incluye: { texto: string; on: boolean }[]; cobro: CobroPlan[] };
export const PLAN_EJEMPLO: PlanB2 = {
  v: 1, nombre: "Plan de mantenimiento", precioSinIva: 15000, visitasAno: 2,
  incluye: [
    { texto: "Visita preventiva: limpieza de lentes, ajuste de ángulos y revisión de cableado", on: true },
    { texto: "Revisión del disco del grabador y de los días de grabación", on: true },
    { texto: "Actualización de firmware de cámaras y grabador", on: true },
    { texto: "Prioridad en soporte técnico", on: true },
    { texto: "Descuento en repuestos (10 %)", on: false },
    { texto: "Revisión remota mensual", on: false },
  ],
  cobro: ["tarjeta", "factura"],
};
export const COBRO_TXT: Record<CobroPlan, string> = { tarjeta: "Tarjeta con ONVO", factura: "Factura mensual (SINPE Móvil o transferencia)" };
export const VISITAS_OPC = [1, 2, 3, 4] as const;
export const MAX_INCLUYE = 8;
/** Lee y valida lo que haya en crimson-demo:b2:plan; ante cualquier duda, el ejemplo. */
export function leerPlan(raw: string | null): PlanB2 {
  if (!raw) return PLAN_EJEMPLO;
  try {
    const p = JSON.parse(raw) as Partial<PlanB2>;
    if (p?.v !== 1) return PLAN_EJEMPLO;
    const nombre = typeof p.nombre === "string" && p.nombre.trim() ? p.nombre.slice(0, 60) : PLAN_EJEMPLO.nombre;
    const precio = Number(p.precioSinIva);
    const visitas = Number(p.visitasAno);
    const incluye = Array.isArray(p.incluye)
      ? p.incluye.filter((x) => x && typeof x.texto === "string").slice(0, MAX_INCLUYE).map((x) => ({ texto: x.texto.slice(0, 90), on: !!x.on }))
      : PLAN_EJEMPLO.incluye;
    const cobro = Array.isArray(p.cobro) ? p.cobro.filter((c): c is CobroPlan => c === "tarjeta" || c === "factura") : [];
    return {
      v: 1, nombre,
      precioSinIva: Number.isFinite(precio) && precio > 0 && precio < 10000000 ? Math.round(precio) : PLAN_EJEMPLO.precioSinIva,
      visitasAno: (VISITAS_OPC as readonly number[]).includes(visitas) ? visitas : PLAN_EJEMPLO.visitasAno,
      incluye, cobro: cobro.length ? [...new Set(cobro)] : PLAN_EJEMPLO.cobro,
    };
  } catch {
    return PLAN_EJEMPLO;
  }
}
export const conIva = (n: number) => Math.round(n * 1.13 * 100) / 100;
export const CONTRATO = "CTR-2026-0077";
export const FACTURA_PLAN_N = 233; // FE-000233 y siguientes, de ejemplo
export const facturaPlan = (k: number) => `FE-${String(FACTURA_PLAN_N + k).padStart(6, "0")}`;
export const TECNICO = "Luis";
export const NOTAS_VISITA = [
  "Limpieza de 4 cámaras; disco del grabador al 62 %; sin fallas.",
  "Limpieza de 4 cámaras; disco del grabador al 71 %; se ajustó el ángulo de la cámara del portón.",
  "Limpieza de 4 cámaras; firmware al día en grabador y cámaras; sin fallas.",
  "Limpieza de 4 cámaras; revisión del cableado exterior; sin fallas.",
];
/** Otros contratos de ejemplo en Mantenimientos (proxima visita en dias desde hoy). */
export const CONTRATOS_OTROS = [
  { numero: "CTR-2026-0071", cliente: "Ana Solano", plan: "Hogar", cuota: 15000, cadaMeses: 6, proxima: 9, cobro: "Tarjeta (ONVO)" },
  { numero: "CTR-2026-0064", cliente: "Jorge Vargas", plan: "Hogar", cuota: 15000, cadaMeses: 6, proxima: 38, cobro: "Factura mensual" },
  { numero: "CTR-2026-0058", cliente: "Sofía Mora", plan: "Hogar", cuota: 15000, cadaMeses: 6, proxima: 67, cobro: "Tarjeta (ONVO)" },
  { numero: "CTR-2026-0049", cliente: "Comercial Ejemplo S.A.", plan: "Negocio", cuota: 35000, cadaMeses: 3, proxima: 16, cobro: "Factura mensual" },
];
