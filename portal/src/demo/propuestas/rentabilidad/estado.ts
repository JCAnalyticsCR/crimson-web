/* Estado de «¿Es rentable?»: lo que Andrés cambia, cómo se guarda (localStorage con try/catch) y el resumen
   que lee «Lo que viene». Si el navegador no deja guardar, todo sigue funcionando en memoria. */
import { almacen, CLAVES } from "./comun";
import { cerca, enPantalla } from "./cifras";
import {
  BASE_EJEMPLO, ESCENARIO_EJEMPLO, ESCENARIOS, INCLUIDA_EJEMPLO, INICIO_EJEMPLO, INVERSION_VACIA,
  type Base, type ClaveBase, type ClaveEscenario, type Entradas, type EscenarioId, type ParamEscenario, type Resultado,
} from "./modelo";
import { CAMPOS_BASE, CAMPO_ESCENARIO } from "./supuestos";

export type Estado = {
  v: 1;
  escenario: EscenarioId;
  base: Base;
  ajustes: Partial<Record<EscenarioId, Partial<ParamEscenario>>>;
  incluida: boolean[]; // etapas 0…5 (la 0 siempre true)
  inicio: number[]; // mes de arranque de las etapas 0…5
  inversion: (number | null)[]; // etapas 0…5
  sumarHoras: boolean;
};

export const TU_DATO: readonly ClaveBase[] = ["Fm", "cF", "Cop"];

export const estadoInicial = (escenario: EscenarioId = "conservador"): Estado => ({
  v: 1, escenario, base: { ...BASE_EJEMPLO }, ajustes: {}, incluida: [...INCLUIDA_EJEMPLO], inicio: [...INICIO_EJEMPLO],
  inversion: [...INVERSION_VACIA], sumarHoras: false,
});

export type Accion =
  | { t: "escenario"; e: EscenarioId }
  | { t: "base"; k: ClaveBase; v: number | null }
  | { t: "ajuste"; e: EscenarioId; k: ClaveEscenario; v: number }
  | { t: "inicio"; s: number; v: number }
  | { t: "incluida"; s: number; v: boolean }
  | { t: "inversion"; s: number; v: number | null }
  | { t: "sumarHoras"; v: boolean }
  | { t: "volverEjemplos" };

export function reducir(s: Estado, a: Accion): Estado {
  switch (a.t) {
    case "escenario": return s.escenario === a.e ? s : { ...s, escenario: a.e };
    case "base": {
      if (a.v === null && !TU_DATO.includes(a.k)) return s;
      return { ...s, base: { ...s.base, [a.k]: a.v } };
    }
    case "ajuste": {
      const prev = { ...(s.ajustes[a.e] || {}) };
      if (cerca(a.v, ESCENARIO_EJEMPLO[a.e][a.k])) delete prev[a.k];
      else prev[a.k] = a.v;
      return { ...s, ajustes: { ...s.ajustes, [a.e]: prev } };
    }
    case "inicio": return { ...s, inicio: s.inicio.map((x, i) => (i === a.s ? a.v : x)) };
    case "incluida": return a.s === 0 ? s : { ...s, incluida: s.incluida.map((x, i) => (i === a.s ? a.v : x)) };
    case "inversion": return { ...s, inversion: s.inversion.map((x, i) => (i === a.s ? a.v : x)) };
    case "sumarHoras": return { ...s, sumarHoras: a.v };
    case "volverEjemplos": {
      // borra los ajustes del escenario actual, la base editada y el calendario; NO borra los TU DATO ni la inversión
      const ajustes = { ...s.ajustes };
      delete ajustes[s.escenario];
      const base = { ...BASE_EJEMPLO };
      for (const k of TU_DATO) (base as Record<ClaveBase, number | null>)[k] = s.base[k];
      return { ...s, ajustes, base, inicio: [...INICIO_EJEMPLO] };
    }
  }
}

export const paramDe = (s: Estado, e: EscenarioId): ParamEscenario => ({ ...ESCENARIO_EJEMPLO[e], ...(s.ajustes[e] || {}) });

export const entradasDe = (s: Estado, e: EscenarioId): Entradas => ({
  base: s.base, esc: paramDe(s, e), inicio: s.inicio, incluida: s.incluida, inversion: s.inversion, sumarHoras: s.sumarHoras,
});

export const baseEditada = (s: Estado): boolean =>
  (Object.keys(BASE_EJEMPLO) as ClaveBase[]).some((k) => !TU_DATO.includes(k) && !cerca(s.base[k], BASE_EJEMPLO[k]));
export const calendarioEditado = (s: Estado): boolean => s.inicio.some((x, i) => x !== INICIO_EJEMPLO[i]);
/** «(con tus cambios)»: el escenario tiene ajustes propios, o se cambió la base de ejemplo o el calendario. */
export const ajustado = (s: Estado, e: EscenarioId): boolean =>
  Object.keys(s.ajustes[e] || {}).length > 0 || baseEditada(s) || calendarioEditado(s);

// ---------- guardar y leer
const dentro = (v: unknown, min: number, max: number): v is number => typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;

export function leerGuardado(): Estado | null {
  const raw = almacen.local.get(CLAVES.rentabilidad);
  if (!raw) return null;
  let d: Record<string, unknown>;
  try { d = JSON.parse(raw); } catch { return null; }
  if (!d || typeof d !== "object" || d.v !== 1) return null;
  const s = estadoInicial();
  if (typeof d.escenario === "string" && (ESCENARIOS as readonly string[]).includes(d.escenario)) s.escenario = d.escenario as EscenarioId;
  const base = (d.base && typeof d.base === "object" ? d.base : {}) as Record<string, unknown>;
  for (const k of Object.keys(BASE_EJEMPLO) as ClaveBase[]) {
    const c = CAMPOS_BASE[k];
    const v = base[k];
    if (v === null && TU_DATO.includes(k)) (s.base as Record<ClaveBase, number | null>)[k] = null;
    else if (typeof v === "number" && dentro(enPantalla(v, c.unidad), c.min, c.max)) (s.base as Record<ClaveBase, number | null>)[k] = v;
  }
  const ajustes = (d.ajustes && typeof d.ajustes === "object" ? d.ajustes : {}) as Record<string, unknown>;
  for (const e of ESCENARIOS) {
    const a = ajustes[e];
    if (!a || typeof a !== "object") continue;
    const limpio: Partial<ParamEscenario> = {};
    for (const [k, v] of Object.entries(a as Record<string, unknown>)) {
      const c = CAMPO_ESCENARIO[k as ClaveEscenario];
      if (c && typeof v === "number" && dentro(enPantalla(v, c.unidad), c.min, c.max)) limpio[k as ClaveEscenario] = v;
    }
    if (Object.keys(limpio).length) s.ajustes[e] = limpio;
  }
  if (Array.isArray(d.incluida) && d.incluida.length === 6) s.incluida = d.incluida.map((x, i) => i === 0 || x !== false);
  if (Array.isArray(d.inicio) && d.inicio.length === 6) s.inicio = d.inicio.map((x, i) => (dentro(x, 1, 36) ? Math.round(x) : INICIO_EJEMPLO[i]));
  if (Array.isArray(d.inversion) && d.inversion.length === 6) s.inversion = d.inversion.map((x) => (dentro(x, 0, 1e11) ? x : null));
  s.sumarHoras = d.sumarHoras === true;
  return s;
}

export const guardar = (s: Estado): boolean => almacen.local.set(CLAVES.rentabilidad, JSON.stringify(s));

// ---------- resumen para «Lo que viene» (sección 6.4, punto 8)
export type ResumenRentabilidad = {
  v: 1; escenario: EscenarioId; ajustado: boolean; etapas: number[];
  utilidadMes12: number; utilidadAcum12: number; utilidadAcum24: number; recurrenteAcum12: number; recurrenteAcum24: number;
  equilibrioMes: number | null; equilibrioTexto: string; inversionTotal: number | null; actualizado: string;
};

export function textoEquilibrio(r: Resultado): string {
  if (r.equilibrio.tipo === "mes") return `mes ${r.equilibrio.mes}`;
  if (r.equilibrio.tipo === "mas36") return "más de 36 meses con estos supuestos";
  return "sin calcular (falta escribir la inversión)";
}

export function escribirResumen(s: Estado, r: Resultado): boolean {
  const m12 = r.meses[11], m24 = r.meses[23];
  const resumen: ResumenRentabilidad = {
    v: 1, escenario: s.escenario, ajustado: ajustado(s, s.escenario),
    etapas: s.incluida.map((x, i) => (i === 0 || x ? i : -1)).filter((i) => i >= 0),
    utilidadMes12: Math.round(m12.U), utilidadAcum12: Math.round(m12.acum), utilidadAcum24: Math.round(m24.acum),
    recurrenteAcum12: Math.round(m12.recAcum), recurrenteAcum24: Math.round(m24.recAcum),
    equilibrioMes: r.equilibrio.tipo === "mes" ? r.equilibrio.mes : null, equilibrioTexto: textoEquilibrio(r),
    inversionTotal: r.inversion, actualizado: new Date().toISOString(),
  };
  return almacen.local.set(CLAVES.rentabilidadResumen, JSON.stringify(resumen));
}

/** Precio sin IVA del plan que Andrés diseñó en la maqueta B2 (si existe). Nunca se aplica solo. */
export function precioPlanB2(): number | null {
  const raw = almacen.local.get(CLAVES.planB2);
  if (!raw) return null;
  try {
    const p = JSON.parse(raw) as { v?: unknown; precioSinIva?: unknown };
    return p && p.v === 1 && dentro(p.precioSinIva, 1, 100_000_000) ? Math.round(p.precioSinIva) : null;
  } catch { return null; }
}
