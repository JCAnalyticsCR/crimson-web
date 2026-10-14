/* Formatos es-CR de la demo. Fechas SIEMPRE relativas a Date.now(): nunca fechas fijas en las maquetas. */

const NBSP = " ";
const crc0 = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 0, maximumFractionDigits: 0 });
const crc2 = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** «₡238 607» */
export const colones = (n: number): string => crc0.format(Number.isFinite(n) ? n : 0);
/** «₡238 607,36» (solo documentos PDF) */
export const colones2 = (n: number): string => crc2.format(Number.isFinite(n) ? n : 0);
/** 0.346 → «34,6 %» */
export const porcentaje = (x: number, dec = 1): string =>
  `${new Intl.NumberFormat("es-CR", { minimumFractionDigits: dec, maximumFractionDigits: dec }).format((Number.isFinite(x) ? x : 0) * 100)}${NBSP}%`;

const aFecha = (d: Date | string): Date => (d instanceof Date ? d : new Date(d));
const sinPunto = (s: string) => s.replace(/\.$/, "").replace(/\.(?=\s|$)/g, "");

/** corta: «13 oct» · larga: «martes, 13 de octubre de 2026» · dia: «mar 13 oct» */
export function fecha(d: Date | string, estilo: "corta" | "larga" | "dia" = "corta"): string {
  const f = aFecha(d);
  if (Number.isNaN(f.getTime())) return "";
  if (estilo === "larga") return f.toLocaleDateString("es-CR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const dia = f.getDate();
  const mes = sinPunto(f.toLocaleDateString("es-CR", { month: "short" }));
  if (estilo === "dia") return `${sinPunto(f.toLocaleDateString("es-CR", { weekday: "short" }))} ${dia} ${mes}`;
  return `${dia} ${mes}`;
}

/** «10:42» */
export const hora = (d: Date | string): string => {
  const f = aFecha(d);
  return Number.isNaN(f.getTime()) ? "" : f.toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", hour12: false });
};

/** Fecha relativa a hoy (dias puede ser negativo). Con hh/mm fija la hora local. */
export function desdeAhora(dias: number, hh?: number, mm?: number): Date {
  const d = new Date(Date.now() + dias * 86_400_000);
  if (typeof hh === "number") d.setHours(hh, typeof mm === "number" ? mm : 0, 0, 0);
  return d;
}
