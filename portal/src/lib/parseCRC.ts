/* Montos en colones escritos como se escriben en Costa Rica (O1-11a). Antes el panel usaba Number(): «4.700» se
   guardaba como ₡4,70. Reglas:
   - Miles con punto o espacio: «4.700», «4 700», «1.250.000». Sin separador: «4700».
   - Decimales con coma: «4.700,50», «4700,5», «0,75».
   - Ambiguo se rechaza (mejor preguntar que cobrar mal): «4.70» (¿cuatro con setenta o un error?), «4,700» (coma de
     miles al estilo ingles), grupos de miles que no son de tres cifras, letras o signos.
   - Vacio = 0. Negativos no.
   Devuelve { ok: true, value } o { ok: false, error } con el texto para el panel (trato de usted). */
export type ParseCRC = { ok: true; value: number } | { ok: false; error: string };

export const CRC_ERROR = "Escriba el monto en colones, por ejemplo 4.700";

export function parseCRC(raw: string | number | null | undefined): ParseCRC {
  if (typeof raw === "number") return Number.isFinite(raw) && raw >= 0 ? { ok: true, value: raw } : { ok: false, error: CRC_ERROR };
  // espacios normales, duros y finos (los que pone Intl al formatear) cuentan como separador de miles
  const s = String(raw ?? "").trim().replace(/^₡\s*/, "").replace(/[  ]/g, " ");
  if (!s) return { ok: true, value: 0 };
  const m = s.match(/^(\d{1,3}(?:[. ]\d{3})+|\d+)(?:,(\d{1,2}))?$/);
  if (!m) return { ok: false, error: CRC_ERROR };
  const entero = m[1];
  // un separador mezclado («4.700 000») tampoco se acepta
  if (/\./.test(entero) && / /.test(entero)) return { ok: false, error: CRC_ERROR };
  const value = Number(entero.replace(/[. ]/g, "") + (m[2] ? `.${m[2]}` : ""));
  return Number.isFinite(value) ? { ok: true, value } : { ok: false, error: CRC_ERROR };
}

/** Monto en la moneda del producto (O1-11b: precio anterior y precio web). En colones, la regla de parseCRC; en
    dolares, punto o coma decimal con hasta dos cifras («12.50», «12,5»), sin separador de miles. */
export function parseMonto(raw: string | number | null | undefined, currency: string): ParseCRC {
  if (currency === "CRC") return parseCRC(raw);
  if (typeof raw === "number") return parseCRC(raw);
  const s = String(raw ?? "").trim().replace(/^\$\s*/, "");
  if (!s) return { ok: true, value: 0 };
  return /^\d+(?:[.,]\d{1,2})?$/.test(s) ? { ok: true, value: Number(s.replace(",", ".")) } : { ok: false, error: `Escriba el monto en ${currency === "USD" ? "dólares" : currency}, por ejemplo 12.50` };
}

/** monto para mostrar en un campo del panel: 4700 -> «4.700», 4700.5 -> «4.700,50» */
export function formatCRCInput(v: number | null | undefined): string {
  const n = Number(v) || 0;
  const [ent, dec] = n.toFixed(2).split(".");
  const miles = ent.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return dec === "00" ? miles : `${miles},${dec}`;
}
