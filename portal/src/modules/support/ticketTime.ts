/* Tiempo de la bandeja de soporte, en el portal. El servidor (api/app/services/sla.py) decide el semaforo y el grupo;
   aqui solo se recalcula el color y el texto del plazo cada 30 s con la MISMA formula (vence_at y ventana_min del
   servidor), para que el reloj baje sin recargar. "Hoy" es el dia de Costa Rica (UTC-6 todo el año). */
import { parseTs } from "../../lib/api";

export const UMBRAL_AMBAR = 0.25; // = services/sla.py UMBRAL_AMBAR
const CR_MS = 6 * 3600_000;
const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
const DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];

export type Color = "rojo" | "ambar" | "verde" | "gris" | "neutro";
export type Semaforo = { color: Color; plazo: "respuesta" | "resolucion" | null; vence_at: string | null; ventana_min: number | null; restante_min: number | null };

/** Semaforo recalculado a esta hora (mismas reglas que el servidor; el rojo nunca vuelve a verde solo). */
export function semaforoVivo(s: Semaforo | null | undefined, status: string, now: number): Semaforo {
  const base: Semaforo = s || { color: "neutro", plazo: null, vence_at: null, ventana_min: null, restante_min: null };
  if (!base.vence_at || base.color === "neutro") return base;
  const restante = Math.floor((parseTs(base.vence_at).getTime() - now) / 60_000);
  const ventana = base.ventana_min ?? 0;
  const color: Color = restante < 0 || base.color === "rojo" ? "rojo" : status === "esperando_cliente" ? "gris" : restante <= ventana * UMBRAL_AMBAR ? "ambar" : "verde";
  return { ...base, color, restante_min: restante };
}

/** 45 min · 3 h 10 min · 2 d 4 h */
export function dur(min: number): string {
  const m = Math.abs(Math.round(min));
  if (m < 60) return `${m} min`;
  if (m < 1440) { const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${r} min` : `${h} h`; }
  const dd = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60);
  return h ? `${dd} d ${h} h` : `${dd} d`;
}

/** Texto del chip de plazo de la tarjeta. El color nunca es la unica señal: el texto dice el estado. */
export function plazoTexto(s: Semaforo): string {
  if (!s.plazo || s.restante_min === null) return "Sin plazo";
  const r = s.restante_min;
  if (s.color === "rojo") return `${s.plazo === "respuesta" ? "Respuesta" : "Resolución"} vencida hace ${dur(-r)}`;
  const cuanto = `${s.plazo === "respuesta" ? "responder" : "resolver"} en ${dur(r)}`;
  if (s.color === "gris") return `Esperando al cliente · ${cuanto}`;
  return s.plazo === "respuesta" ? `Responde en ${dur(r)}` : `Resolver en ${dur(r)}`;
}

/** Texto grande de la franja del detalle. */
export function plazoGrande(s: Semaforo): string {
  if (!s.plazo || s.restante_min === null) return "Sin plazo";
  const r = s.restante_min;
  const que = s.plazo === "respuesta" ? "Primera respuesta" : "Resolución";
  if (s.color === "rojo") return `${que} vencida hace ${dur(-r)}`;
  if (s.color === "gris") return `Esperando al cliente · ${s.plazo === "respuesta" ? "responder" : "resolver"} en ${dur(r)}`;
  return `${que} en ${dur(r)}`;
}

/* ---------- fechas en hora de Costa Rica ---------- */
const cr = (d: Date) => new Date(d.getTime() - CR_MS); // se leen con getUTC*: hora de aqui sin depender de la compu
const hora = (x: Date) => { const h = x.getUTCHours(); return `${h % 12 || 12}:${String(x.getUTCMinutes()).padStart(2, "0")} ${h < 12 ? "a. m." : "p. m."}`; };
const diaN = (x: Date) => Math.floor(x.getTime() / 86_400_000);

/** 15 oct 1:52 p. m. */
export function fechaCR(iso: string | null | undefined): string {
  if (!iso) return "—";
  const x = cr(parseTs(iso));
  return `${x.getUTCDate()} ${MESES[x.getUTCMonth()]} ${hora(x)}`;
}

/** 13 oct */
export function diaCR(iso: string | null | undefined): string {
  if (!iso) return "";
  const x = cr(parseTs(iso));
  return `${x.getUTCDate()} ${MESES[x.getUTCMonth()]}`;
}

/** hoy 4:00 p. m. · mañana 9:00 a. m. · lun 20 oct 9:00 a. m. (para el proximo paso) */
export function cuandoCR(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const x = cr(parseTs(iso)), h = cr(new Date(now));
  const dd = diaN(x) - diaN(h);
  if (dd === 0) return `hoy ${hora(x)}`;
  if (dd === 1) return `mañana ${hora(x)}`;
  if (dd === -1) return `ayer ${hora(x)}`;
  return `${DIAS[x.getUTCDay()]} ${x.getUTCDate()} ${MESES[x.getUTCMonth()]} ${hora(x)}`;
}

/** hace 2 h · ayer 4:10 p. m. · 13 oct 4:10 p. m. */
export function haceCR(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const t = parseTs(iso).getTime();
  const m = Math.floor((now - t) / 60_000);
  if (m < 1) return "hace un momento";
  if (m < 60) return `hace ${m} min`;
  const x = cr(new Date(t)), h = cr(new Date(now));
  if (diaN(x) === diaN(h)) return `hace ${Math.floor(m / 60)} h`;
  if (diaN(h) - diaN(x) === 1) return `ayer ${hora(x)}`;
  return fechaCR(iso);
}

/** ISO (UTC) de un dia de Costa Rica a una hora: crAt(1, 9) = mañana 9:00 a. m. */
export function crAt(diasAdelante: number, h: number, now = Date.now()): string {
  const x = cr(new Date(now));
  return new Date(Date.UTC(x.getUTCFullYear(), x.getUTCMonth(), x.getUTCDate() + diasAdelante, h, 0) + CR_MS).toISOString();
}

/** Atajos de fecha del proximo paso, en hora de aqui. */
export function atajosFecha(now = Date.now()): { label: string; iso: string }[] {
  const x = cr(new Date(now));
  const out: { label: string; iso: string }[] = [];
  if (x.getUTCHours() < 15) out.push({ label: "Hoy 4:00 p. m.", iso: crAt(0, 16, now) });
  out.push({ label: "Mañana 9:00 a. m.", iso: crAt(1, 9, now) });
  out.push({ label: "En 2 días 9:00 a. m.", iso: crAt(2, 9, now) });
  const dow = x.getUTCDay(); // 5 viernes, 6 sabado, 0 domingo
  if (dow === 5 || dow === 6 || dow === 0) out.push({ label: "Lunes 9:00 a. m.", iso: crAt(dow === 0 ? 1 : 8 - dow, 9, now) });
  return out;
}

/** datetime-local escrito en hora de Costa Rica -> ISO (igual que WorkOrders.tsx). */
export const localToISO = (local: string) => (local ? new Date(`${local}:00-06:00`).toISOString() : null);
export const isoToLocal = (iso: string | null | undefined) => (iso ? new Date(parseTs(iso).getTime() - CR_MS).toISOString().slice(0, 16) : "");
