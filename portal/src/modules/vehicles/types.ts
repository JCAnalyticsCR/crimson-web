/* Tipos y utilidades del modulo de vehiculos y combustible. */
export type Num = string | number | null;
export type Vehicle = {
  id: number; name: string; plate: string | null; kind: string | null; owner: "empresa" | "personal"; driver: string | null; active: boolean;
  excluded_from_analysis: boolean; exclusion_reason: string | null; notes: string | null; loads: number; last_date: string | null; last_odometer: number | null;
};
export type Load = {
  id: number; vehicle_id: number; vehicle: string; plate: string | null; date: string; driver: string | null; liters: Num; amount: Num; currency: string;
  price_per_liter: Num; odometer: number | null; full_tank: boolean; receipt_media_id: number | null; receipt_url: string | null; receipt_name: string | null;
  usage: string | null; project_id: number | null; project: string | null; excluded: boolean; exclusion_reason: string | null;
  excluded_effective: boolean; exclusion_effective: string | null; source: string; notes: string | null;
};
export type Lookups = { vehicles: { id: number; name: string; plate: string | null; driver: string | null; active: boolean; excluded: boolean }[]; projects: { id: number; label: string }[]; config: { monthly_reference: Num } };
export type Month = { month: string; loads: number; liters: Num; amount: Num; cost_per_liter: Num; partial: boolean; partial_reason: string | null; liters_missing: number };
export type ByVehicle = {
  vehicle_id: number; name: string; plate: string | null; owner: string; excluded: boolean; exclusion_reason: string | null; loads: number; loads_excluded: number; amount_excluded: Num;
  liters: Num; amount: Num; share_pct: number | null; cost_per_liter: Num; intervals: number; intervals_valid: number; km_valid: number; km_per_l: Num; cost_per_km: Num;
  other_costs: Record<string, Num>; other_costs_total: Num;
};
export type Interval = { vehicle_id: number; vehicle: string; from: string; to: string; odo_from: number; odo_to: number; km: number; liters: Num; amount: Num; km_per_l: Num; valid: boolean; reason: string | null };
export type Report = {
  from: string; to: string; note: string; reference: { amount: Num; label: string; validated: boolean }; coverage: { first: string | null; last: string | null };
  totals: { loads: number; liters: Num; amount: Num; cost_per_liter: Num; km_valid: number; km_per_l: Num; cost_per_km: Num; full_months: number; excluded_loads: number; excluded_amount: Num };
  months: Month[]; by_vehicle: ByVehicle[]; intervals: Interval[]; excluded: { id: number; date: string; vehicle: string; amount: Num; currency: string; reason: string }[]; warnings: string[];
};
export type Cost = { id: number; vehicle_id: number; vehicle: string; date: string; kind: string; amount: Num; currency: string; description: string | null };

export const n = (v: Num | undefined) => (v === null || v === undefined || v === "" ? null : Number(v));
export const fmtNum = (v: Num | undefined, digits = 0) => { const x = n(v); return x === null ? "—" : new Intl.NumberFormat("es-CR", { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(x); };
export const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
export const monthLabel = (ym: string) => { const [y, m] = ym.split("-"); return `${MONTHS[Number(m) - 1]} ${y.slice(2)}`; };
export const COST_KINDS: Record<string, string> = { mantenimiento: "Mantenimiento", llantas: "Llantas", seguro: "Seguro", depreciacion: "Depreciación", otro: "Otro" };
export const KINDS: Record<string, string> = { sedan: "Sedán", pickup: "Pickup", suv: "SUV", moto: "Moto", camion: "Camión", otro: "Otro" };
export const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });
export const yearStart = () => `${today().slice(0, 4)}-01-01`;
