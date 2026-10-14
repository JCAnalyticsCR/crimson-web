/* Formato de cifras de «¿Es rentable?» (Intl es-CR) y lectura de lo que se escribe en los campos. */
import { colones } from "./comun";
import type { Unidad } from "./supuestos";

const MENOS = "−";

/** ₡ con signo tipográfico; nunca «−₡0». */
export const crc = (n: number): string => (Math.round(n) < 0 ? MENOS : "") + colones(Math.abs(Math.round(n)));
/** «+₡871 763» / «−₡12 000». */
export const crcSigno = (n: number): string => (Math.round(n) < 0 ? MENOS : "+") + colones(Math.abs(Math.round(n)));

const f1 = new Intl.NumberFormat("es-CR", { maximumFractionDigits: 1 });
const f2 = new Intl.NumberFormat("es-CR", { maximumFractionDigits: 2 });
const f2g = new Intl.NumberFormat("es-CR", { maximumFractionDigits: 2, useGrouping: false });
const f0 = new Intl.NumberFormat("es-CR", { maximumFractionDigits: 0 });

export const numero = (n: number, dec = 2): string => (dec === 0 ? f0 : dec === 1 ? f1 : f2).format(n);

/** Desde ₡1 000 000, «₡1,7 millones» (la cifra exacta va en title y en las tablas). */
export function crcGrande(n: number): string {
  const a = Math.abs(n);
  if (a < 1_000_000) return crc(n);
  const v = Math.round(a / 100_000) / 10;
  const s = Math.round(n) < 0 ? MENOS : "";
  return `${s}₡${f1.format(v)} ${v === 1 ? "millón" : "millones"}`;
}

/** Fracción → «5 %», «1,5 %». */
export const pct = (x: number): string => `${f2.format(Math.round(x * 10_000) / 100)} %`;

/** Rótulos del eje y: «₡2 M», «₡500 mil», «₡0». */
export function ejeColones(n: number): string {
  const a = Math.abs(n);
  const s = n < 0 ? MENOS : "";
  if (a === 0) return "₡0";
  if (a >= 1_000_000) return `${s}₡${f1.format(a / 1_000_000)} M`;
  if (a >= 1_000) return `${s}₡${f0.format(a / 1_000)} mil`;
  return `${s}₡${f0.format(a)}`;
}

/** Texto del campo cuando no tiene foco (unidad de pantalla: los porcentajes ×100). */
export function textoCampo(v: number | null, unidad: Unidad): string {
  if (v === null) return "";
  if (unidad === "porcentaje") return f2g.format(Math.round(v * 1_000_000) / 10_000);
  if (unidad === "colones") return f0.format(Math.round(v));
  return f2g.format(v);
}

export type Lectura = { ok: true; v: number | null } | { ok: false };

/** Lee «100 000», «100.000», «1,5», «3.5 %», «₡25 000». Vacío = null. Devuelve la unidad del MODELO (porcentaje como fracción). */
export function leerCampo(texto: string, unidad: Unidad): Lectura {
  // \s ya cubre el espacio duro y el angosto que pone Intl es-CR entre miles
  let s = texto.replace(/[\s₡%]/g, "").replace(/^mes/i, "").replace(/(h|horas?|mes(es)?)$/i, "");
  if (s === "") return { ok: true, v: null };
  if (/^\d{1,3}([.,]\d{3})+$/.test(s) && unidad !== "porcentaje") s = s.replace(/[.,]/g, "");
  else if (s.includes(",") && s.includes(".")) {
    const i = Math.max(s.lastIndexOf(","), s.lastIndexOf("."));
    s = `${s.slice(0, i).replace(/[.,]/g, "")}.${s.slice(i + 1)}`;
  } else s = s.replace(",", ".");
  if (!/^\d*\.?\d+$/.test(s)) return { ok: false };
  const n = Number(s);
  if (!Number.isFinite(n)) return { ok: false };
  return { ok: true, v: unidad === "porcentaje" ? Math.round((n / 100) * 1_000_000) / 1_000_000 : n };
}

/** Valor de pantalla (para comparar con min/max de la ficha del campo). */
export const enPantalla = (v: number, unidad: Unidad): number => (unidad === "porcentaje" ? v * 100 : v);

export const cerca = (a: number | null, b: number | null): boolean => (a === null || b === null ? a === b : Math.abs(a - b) < 1e-9);
