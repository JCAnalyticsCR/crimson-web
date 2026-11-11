/* Tarifas de envio del panel (O1-11a), sin React para poder probarlo en Vitest. Los montos se leen con parseCRC
   («4.700» son cuatro mil setecientos), el nombre se valida igual que el API (2 a 60 caracteres, unico sin
   distinguir mayusculas) y una tarifa activa de ₡0 que no es de retiro pide confirmacion antes de guardarse. */
import { parseCRC } from "../../lib/parseCRC";

export type Rate = { id?: string; name: string; amount: number; active: boolean; per_kg?: number; overhead_pct?: number; zone?: string | null };
/** lo que el panel tiene escrito en una fila (texto tal cual) */
export type RateText = { name: string; amount: string; per_kg: string; overhead_pct: string; active: boolean };
export type RateCheck = { ok: true; rate: Omit<Rate, "id" | "zone"> } | { ok: false; errors: Partial<Record<keyof RateText, string>> };

export function checkRate(t: RateText, others: Pick<Rate, "name">[]): RateCheck {
  const errors: Partial<Record<keyof RateText, string>> = {};
  const name = t.name.trim();
  if (name.length < 2 || name.length > 60) errors.name = "El nombre lleva de 2 a 60 caracteres";
  else if (others.some((o) => o.name.trim().toLocaleLowerCase("es") === name.toLocaleLowerCase("es"))) errors.name = "Ya hay una tarifa con ese nombre";
  const amount = parseCRC(t.amount);
  const perKg = parseCRC(t.per_kg);
  const pct = Number(String(t.overhead_pct || "0").replace(",", "."));
  if (!amount.ok) errors.amount = amount.error;
  if (!perKg.ok) errors.per_kg = perKg.error;
  if (!Number.isFinite(pct) || pct < 0 || pct > 100 || !/^\d{0,3}([.,]\d{1,2})?$/.test(String(t.overhead_pct || "0").trim())) errors.overhead_pct = "El recargo va de 0 a 100 %";
  if (Object.keys(errors).length || !amount.ok || !perKg.ok) return { ok: false, errors };
  return { ok: true, rate: { name, amount: amount.value, per_kg: perKg.value, overhead_pct: pct, active: t.active } };
}

/** activa, sin cobro y no es de retiro: el envio quedaria gratis para todo el pais */
export const freeEverywhere = (r: Pick<Rate, "name" | "amount" | "active" | "per_kg">) =>
  r.active && !Number(r.amount) && !Number(r.per_kg) && !/retiro/i.test(r.name);
export const FREE_CONFIRM = "Esta tarifa quedará gratis para todo el país. ¿Guardar así?";

/** monto sospechoso (posible «4.700» guardado como ₡4,70 antes de parseCRC): se avisa, no se corrige solo */
export const suspiciousAmount = (r: Pick<Rate, "amount">) => Number(r.amount) > 0 && Number(r.amount) < 100;
