/* Modelo de rentabilidad de la tienda propia (página «¿Es rentable?» de la vista previa).
   Funciones PURAS: sin React, sin DOM, sin almacenamiento y sin importaciones. Así se pueden verificar en el
   navegador (banco de pruebas), en desarrollo (autoverificación) y con Node:
     cd portal && node src/demo/propuestas/rentabilidad/verificar-modelo.mjs

   Honestidad (sección 6 de la especificación):
   - Todo número de ejemplo es un SUPUESTO editable, no un dato de mercado. Datos reales: margen sobre venta 35 %
     (confirmado por Andrés para los levantamientos: se usa como dato en la instalación; en el equipo de tienda es
     un ejemplo) e IVA 13 % (los montos del modelo van SIN IVA).
   - Es utilidad BRUTA incremental. Las horas van aparte y no se suman salvo que se pida (sumarHoras).
   - Los costos desconocidos (Fygaro, operar la tienda, inversión) entran como null = 0 y la pantalla lo dice.
   Porcentajes como fracción (0,35). Se calcula en flotante y se redondea solo al mostrar. */

export type EscenarioId = "conservador" | "medio" | "optimista";
export const ESCENARIOS: readonly EscenarioId[] = ["conservador", "medio", "optimista"];

/** Supuestos base: iguales en los tres escenarios. Montos sin IVA, por mes. */
export type Base = {
  P0: number; // pedidos en línea por mes, hoy
  T: number; // monto promedio por pedido
  pc: number; // pedidos que llevan cámaras o equipo que se instala
  Hh: number; // horas al mes en tareas manuales de la tienda
  ch: number; // costo de una hora
  K: number; // carritos abandonados por mes con contacto
  Tr: number; // monto promedio de una recompra
  m: number; // margen sobre venta de equipo de tienda (ejemplo: 35 % es el de los levantamientos)
  mi: number; // margen sobre venta de instalación (dato de Crimson)
  mp: number; // margen del plan de mantenimiento
  I: number; // precio promedio de una instalación
  c: number; // cuota mensual del plan
  Fm: number | null; // mensualidad de Fygaro (TU DATO)
  cF: number | null; // comisión de Fygaro sobre ventas (TU DATO)
  Cop: number | null; // costo mensual de operar la tienda propia (TU DATO)
};
export type ClaveBase = keyof Base;

/** Supuestos que cambian por escenario. */
export type ParamEscenario = {
  u: number; // aumento de ventas en línea por tener tienda propia
  ti: number; // de los pedidos con cámaras, cuántos agregan instalación (A1)
  tp: number; // de los pedidos con cámaras, cuántos toman el plan (B2)
  b: number; // planes que se dan de baja cada mes
  rr: number; // carritos con contacto que vuelven con el recordatorio (A2)
  ra: number; // aumento de recompra gracias a B1, por encima de lo que ya vuelve hoy (compras extra por comprador al año)
  he: number; // parte de las horas que hace la plataforma sola
  R: number; // meses hasta que cada etapa rinde completo
};
export type ClaveEscenario = keyof ParamEscenario;

/** Etapas 0…5 (0 = la tienda propia, siempre incluida). */
export type Entradas = {
  base: Base;
  esc: ParamEscenario;
  inicio: readonly number[]; // mes de arranque de cada etapa [E0…E5]
  incluida: readonly boolean[]; // [E0…E5]; E0 se toma siempre como incluida
  inversion: readonly (number | null)[]; // [Inv0…Inv5]
  sumarHoras: boolean;
};

export const MESES = 36;
export const ETAPAS_CON_CIFRA = [0, 1, 2, 3] as const;

export const BASE_EJEMPLO: Base = {
  P0: 20, T: 100_000, pc: 0.5, Hh: 20, ch: 4_000, K: 20, Tr: 50_000,
  m: 0.35, mi: 0.35, mp: 0.35, I: 150_000, c: 15_000,
  Fm: null, cF: null, Cop: null,
};

export const ESCENARIO_EJEMPLO: Record<EscenarioId, ParamEscenario> = {
  conservador: { u: 0.05, ti: 0.05, tp: 0.03, b: 0.03, rr: 0.03, ra: 0.02, he: 0.4, R: 6 },
  medio: { u: 0.1, ti: 0.1, tp: 0.06, b: 0.02, rr: 0.05, ra: 0.04, he: 0.6, R: 4 },
  optimista: { u: 0.2, ti: 0.15, tp: 0.1, b: 0.015, rr: 0.08, ra: 0.06, he: 0.75, R: 3 },
};

/** Calendario de ejemplo: mes de arranque de las etapas 0…5. */
export const INICIO_EJEMPLO: readonly number[] = [1, 2, 4, 6, 9, 12];
export const INCLUIDA_EJEMPLO: readonly boolean[] = [true, true, true, true, true, true];
export const INVERSION_VACIA: readonly (number | null)[] = [null, null, null, null, null, null];

export type Mes = {
  k: number;
  r: [number, number, number, number]; // rampa de las etapas 0…3 en este mes
  N: number; // pedidos del mes con tienda propia
  S1: number; S2: number; S3: number; S4: number; S5: number; S6: number; S7: number;
  horas: number; // horas liberadas en el mes
  A: number; // planes activos al cierre del mes
  MRR: number; // ingreso recurrente del plan en el mes (sin IVA, no utilidad)
  B: number; // compradores con cuenta que compraron en los últimos 12 meses, al cierre del mes
  costos: number; // costo de operar la tienda propia
  U: number; // utilidad incremental del mes
  acum: number; // utilidad acumulada hasta este mes
  recAcum: number; // ingreso recurrente acumulado hasta este mes
};

export type Equilibrio = { tipo: "mes"; mes: number } | { tipo: "mas36" } | { tipo: "sin-inversion" };

/** Ventana de compradores con cuenta (B1): solo cuentan los que compraron en los últimos 12 meses. */
export const VENTANA_B = 12;

export type Resultado = {
  meses: Mes[]; // meses[0] = mes 1 … meses[35] = mes 36
  V0: number;
  inversion: number | null; // suma de las etapas incluidas; null si no hay inversión escrita (> 0)
  equilibrio: Equilibrio;
  /** Solo la tienda propia (Etapa 0) contra su propia inversión (Inv0). */
  equilibrioTienda: Equilibrio;
  /** Etapas incluidas que suman utilidad (1…3) pero no tienen inversión escrita: con una inversión total escrita,
      el equilibrio sale más corto de lo real porque su costo cuenta como ₡0. */
  sinInversion: number[];
  aportes: [number, number, number, number]; // aporte a la utilidad acumulada de 24 meses, etapas 0…3
};

/** Rampa: al arrancar una etapa su efecto sube parejo hasta el 100 % en R meses. */
export function rampa(k: number, inicio: number, R: number): number {
  if (k < inicio) return 0;
  return Math.min(1, (k - inicio + 1) / Math.max(1, R));
}

const num = (x: number | null | undefined) => (typeof x === "number" && Number.isFinite(x) ? x : 0);

function simular(e: Entradas, inc: readonly boolean[]): Mes[] {
  const b0 = e.base;
  const s = e.esc;
  const V0 = b0.P0 * b0.T;
  const fygaro = num(b0.Fm) + num(b0.cF) * V0;
  const cop = num(b0.Cop);
  const i1 = !!inc[1], i2 = !!inc[2], i3 = !!inc[3];
  const meses: Mes[] = [];
  let A = 0; // planes activos (A_0 = 0)
  let B = 0; // compradores con cuenta en la ventana de 12 meses (B_0 = 0)
  const altas: number[] = []; // compradores que entran cada mes (para sacarlos de B al cumplir 12 meses)
  let acum = 0, recAcum = 0;
  for (let k = 1; k <= MESES; k++) {
    const r: Mes["r"] = [rampa(k, e.inicio[0], s.R), rampa(k, e.inicio[1], s.R), rampa(k, e.inicio[2], s.R), rampa(k, e.inicio[3], s.R)];
    const N = b0.P0 * (1 + s.u * r[0]);
    const S1 = V0 * s.u * r[0] * b0.m;
    const S2 = i1 ? N * b0.pc * s.ti * r[1] * b0.I * b0.mi : 0;
    A = A * (1 - s.b) + (i2 ? N * b0.pc * s.tp * r[2] : 0);
    const MRR = A * b0.c;
    const S3 = MRR * b0.mp;
    const S4 = i3 ? b0.K * s.rr * r[3] * b0.T * b0.m : 0;
    const S5 = i1 ? ((B * s.ra) / 12) * b0.Tr * b0.m : 0; // B todavía es B_{k−1}
    const alta = i1 ? N * r[1] : 0;
    altas.push(alta);
    B = B + alta - (altas.length > VENTANA_B ? altas[altas.length - 1 - VENTANA_B] : 0);
    const horas = b0.Hh * s.he * r[0];
    const S6 = horas * b0.ch;
    // lo que se deja de pagar a Fygaro y el costo de operar corren desde que arranca la tienda propia (Etapa 0)
    const propia = k >= e.inicio[0];
    const S7 = propia ? fygaro : 0;
    const costo = propia ? cop : 0;
    const U = S1 + S2 + S3 + S4 + S5 + S7 - costo + (e.sumarHoras ? S6 : 0);
    acum += U;
    recAcum += MRR;
    meses.push({ k, r, N, S1, S2, S3, S4, S5, S6, S7, horas, A, MRR, B, costos: costo, U, acum, recAcum });
  }
  return meses;
}

/** Inversión total de las etapas incluidas (Inv0 siempre). null si no hay inversión escrita mayor que 0. */
export function inversionTotal(e: Entradas): number | null {
  let t = 0;
  for (let s = 0; s < 6; s++) if (s === 0 || e.incluida[s]) t += Math.max(0, num(e.inversion[s]));
  return t > 0 ? t : null;
}

export function equilibrioDe(meses: Mes[], inversion: number | null): Equilibrio {
  if (inversion === null) return { tipo: "sin-inversion" };
  const m = meses.find((x) => x.acum >= inversion);
  return m ? { tipo: "mes", mes: m.k } : { tipo: "mas36" };
}

/** El cálculo completo de un escenario. */
export function calcular(e: Entradas): Resultado {
  const inc = e.incluida.map((v, i) => i === 0 || !!v);
  const meses = simular(e, inc);
  const inversion = inversionTotal(e);
  const inv0 = Math.max(0, num(e.inversion[0]));
  const soloTienda = simular(e, [true, false, false, false, false, false]);
  const sinInversion = inversion === null ? [] : [1, 2, 3].filter((s) => inc[s] && !(num(e.inversion[s]) > 0));
  // Aporte(s) = Acum_24(etapas 0..s) − Acum_24(etapas 0..s−1), con las etapas forzadas (no dependen de los interruptores).
  const acum24 = (hasta: number) => (hasta < 0 ? 0 : simular(e, [0, 1, 2, 3, 4, 5].map((i) => i <= hasta))[23].acum);
  const a = [acum24(0), acum24(1), acum24(2), acum24(3)];
  return {
    meses,
    V0: e.base.P0 * e.base.T,
    inversion,
    equilibrio: equilibrioDe(meses, inversion),
    equilibrioTienda: equilibrioDe(soloTienda, inv0 > 0 ? inv0 : null),
    sinInversion,
    aportes: [a[0], a[1] - a[0], a[2] - a[1], a[3] - a[2]],
  };
}

/** Atajo para leer el mes k (1…36). */
export const mes = (r: Resultado, k: number): Mes => r.meses[Math.min(MESES, Math.max(1, Math.round(k))) - 1];

/* ============================================================================================================
   Vectores de prueba (sección 6.7, recalculados al corregir las recompras de B1: compradores con cuenta en una
   ventana de 12 meses y «ra» como aumento por encima de lo que ya vuelve hoy). Configuración: valores por defecto, todas las etapas incluidas, arranques
   1/2/4/6/9/12, TU DATO vacíos y sumarHoras apagado. Tolerancia ±₡1 (planes activos: ±0,005).
   Las inversiones de prueba son SOLO para QA: nunca se muestran como valor por defecto.
   ============================================================================================================ */

export function entradasEjemplo(esc: EscenarioId, cambios?: { base?: Partial<Base>; inversion?: (number | null)[]; sumarHoras?: boolean }): Entradas {
  return {
    base: { ...BASE_EJEMPLO, ...(cambios?.base || {}) },
    esc: { ...ESCENARIO_EJEMPLO[esc] },
    inicio: [...INICIO_EJEMPLO],
    incluida: [...INCLUIDA_EJEMPLO],
    inversion: cambios?.inversion ? [...cambios.inversion] : [...INVERSION_VACIA],
    sumarHoras: !!cambios?.sumarHoras,
  };
}

type Salida = (r: Resultado) => number | string;
type Caso = { nombre: string; cambios?: Parameters<typeof entradasEjemplo>[1]; salida: Salida; esperado: Record<EscenarioId, number | string>; tolerancia?: number };

const eqDe = (q: Equilibrio) => (q.tipo === "mes" ? `mes ${q.mes}` : q.tipo === "mas36" ? "más de 36" : "sin inversión");
const eqTexto = (r: Resultado) => eqDe(r.equilibrio);
const eqTienda = (r: Resultado) => eqDe(r.equilibrioTienda);
const m_ = (k: number, campo: keyof Omit<Mes, "r">): Salida => (r) => mes(r, k)[campo] as number;

function construirCasos(): Caso[] {
  return [
    { nombre: "U mes 1", salida: m_(1, "U"), esperado: { conservador: 5_833, medio: 17_500, optimista: 46_667 } },
    { nombre: "U mes 12", salida: m_(12, "U"), esperado: { conservador: 97_973, medio: 197_950, optimista: 357_165 } },
    { nombre: "U mes 24", salida: m_(24, "U"), esperado: { conservador: 114_612, medio: 234_533, optimista: 425_239 } },
    { nombre: "Acumulado 12 m", salida: m_(12, "acum"), esperado: { conservador: 720_910, medio: 1_634_803, optimista: 3_134_960 } },
    { nombre: "Acumulado 24 m", salida: m_(24, "acum"), esperado: { conservador: 2_019_239, medio: 4_274_078, optimista: 7_902_318 } },
    { nombre: "Planes activos m12", salida: m_(12, "A"), esperado: { conservador: 1.87, medio: 4.63, optimista: 9.11 }, tolerancia: 0.005 },
    { nombre: "MRR m12", salida: m_(12, "MRR"), esperado: { conservador: 28_095, medio: 69_488, optimista: 136_582 } },
    { nombre: "Planes activos m24", salida: m_(24, "A"), esperado: { conservador: 4.51, medio: 10.74, optimista: 20.86 }, tolerancia: 0.005 },
    { nombre: "MRR m24", salida: m_(24, "MRR"), esperado: { conservador: 67_713, medio: 161_094, optimista: 312_969 } },
    { nombre: "Recurrente acumulado 12 m", salida: m_(12, "recAcum"), esperado: { conservador: 114_511, medio: 307_575, optimista: 631_139 } },
    { nombre: "Recurrente acumulado 24 m", salida: m_(24, "recAcum"), esperado: { conservador: 723_519, medio: 1_758_903, optimista: 3_448_386 } },
    { nombre: "S1 m12 (ventas)", salida: m_(12, "S1"), esperado: { conservador: 35_000, medio: 70_000, optimista: 140_000 } },
    { nombre: "S2 m12 (instalaciones)", salida: m_(12, "S2"), esperado: { conservador: 27_563, medio: 57_750, optimista: 94_500 } },
    { nombre: "S3 m12 (plan)", salida: m_(12, "S3"), esperado: { conservador: 9_833, medio: 24_321, optimista: 47_804 } },
    { nombre: "S4 m12 (carritos)", salida: m_(12, "S4"), esperado: { conservador: 21_000, medio: 35_000, optimista: 56_000 } },
    { nombre: "S5 m12 (recompras)", salida: m_(12, "S5"), esperado: { conservador: 4_578, medio: 10_879, optimista: 18_861 } },
    { nombre: "S7 m12 (Fygaro)", salida: m_(12, "S7"), esperado: { conservador: 0, medio: 0, optimista: 0 } },
    { nombre: "Horas m12", salida: m_(12, "horas"), esperado: { conservador: 8, medio: 12, optimista: 15 }, tolerancia: 0.005 },
    { nombre: "Valor de las horas m12", salida: m_(12, "S6"), esperado: { conservador: 32_000, medio: 48_000, optimista: 60_000 } },
    { nombre: "Con horas: U mes 12", cambios: { sumarHoras: true }, salida: m_(12, "U"), esperado: { conservador: 129_973, medio: 245_950, optimista: 417_165 } },
    { nombre: "Con horas: acumulado 24 m", cambios: { sumarHoras: true }, salida: m_(24, "acum"), esperado: { conservador: 2_707_239, medio: 5_354_078, optimista: 9_282_318 } },
    { nombre: "Aporte 24 m · Etapa 0", salida: (r) => r.aportes[0], esperado: { conservador: 752_500, medio: 1_575_000, optimista: 3_220_000 } },
    { nombre: "Aporte 24 m · Etapa 1", salida: (r) => r.aportes[1], esperado: { conservador: 667_008, medio: 1_470_963, optimista: 2_467_383 } },
    { nombre: "Aporte 24 m · Etapa 2", salida: (r) => r.aportes[2], esperado: { conservador: 253_232, medio: 615_616, optimista: 1_206_935 } },
    { nombre: "Aporte 24 m · Etapa 3", salida: (r) => r.aportes[3], esperado: { conservador: 346_500, medio: 612_500, optimista: 1_008_000 } },
    ...([3, 6, 9, 15, 18, 21] as const).map((k, i): Caso => ({
      nombre: `Acumulado mes ${k}`,
      salida: m_(k, "acum"),
      esperado: {
        conservador: [48_516, 198_872, 436_573, 1_026_397, 1_346_760, 1_678_349][i],
        medio: [147_306, 539_453, 1_053_983, 2_253_682, 2_903_646, 3_577_684][i],
        optimista: [373_411, 1_128_904, 2_086_856, 4_251_557, 5_423_258, 6_640_857][i],
      },
    })),
    { nombre: "Equilibrio con Inv0 = ₡3 000 000", cambios: { inversion: [3_000_000, null, null, null, null, null] }, salida: eqTexto, esperado: { conservador: "mes 33", medio: "mes 19", optimista: "mes 12" } },
    { nombre: "Equilibrio solo tienda (Etapa 0) con Inv0 = ₡3 000 000", cambios: { inversion: [3_000_000, null, null, null, null, null] }, salida: eqTienda, esperado: { conservador: "más de 36", medio: "más de 36", optimista: "mes 23" } },
    { nombre: "Etapas sin inversión con Inv0 = ₡3 000 000", cambios: { inversion: [3_000_000, null, null, null, null, null] }, salida: (r) => r.sinInversion.join(","), esperado: { conservador: "1,2,3", medio: "1,2,3", optimista: "1,2,3" } },
    { nombre: "Equilibrio con Inv0 = ₡6 000 000", cambios: { inversion: [6_000_000, null, null, null, null, null] }, salida: eqTexto, esperado: { conservador: "más de 36", medio: "mes 32", optimista: "mes 20" } },
    ...((): Caso[] => {
      const cambios = { base: { Fm: 30_000, cF: 0.03, Cop: 50_000 }, inversion: [3_000_000, 1_500_000, 1_500_000, 1_000_000, null, null] };
      return [
        { nombre: "Combinada: U mes 12", cambios, salida: m_(12, "U"), esperado: { conservador: 137_973, medio: 237_950, optimista: 397_165 } },
        { nombre: "Combinada: acumulado 24 m", cambios, salida: m_(24, "acum"), esperado: { conservador: 2_979_239, medio: 5_234_078, optimista: 8_862_318 } },
        { nombre: "Combinada: inversión total", cambios, salida: (r) => r.inversion ?? 0, esperado: { conservador: 7_000_000, medio: 7_000_000, optimista: 7_000_000 } },
        { nombre: "Combinada: equilibrio", cambios, salida: eqTexto, esperado: { conservador: "más de 36", medio: "mes 31", optimista: "mes 20" } },
        { nombre: "Combinada: etapas sin inversión", cambios, salida: (r) => r.sinInversion.join(",") || "ninguna", esperado: { conservador: "ninguna", medio: "ninguna", optimista: "ninguna" } },
      ];
    })(),
  ];
}

/** U del escenario Medio, meses 1 a 12 (sección 6.7). */
const U_MEDIO_1_12 = [17_500, 48_781, 81_025, 115_112, 132_227, 144_808, 158_203, 171_532, 184_795, 189_243, 193_627, 197_950];

export type FilaPrueba = { nombre: string; escenario: EscenarioId; esperado: number | string; obtenido: number | string; pasa: boolean };

export type VectorPrueba = { nombre: string; escenario: EscenarioId; cambios?: Caso["cambios"]; salida: Salida; esperado: number | string; tolerancia: number };

function construirVectores(): readonly VectorPrueba[] {
  return [
    ...construirCasos().flatMap((c) => ESCENARIOS.map((esc) => ({ nombre: c.nombre, escenario: esc, cambios: c.cambios, salida: c.salida, esperado: c.esperado[esc], tolerancia: c.tolerancia ?? 1 }))),
    ...U_MEDIO_1_12.map((v, i) => ({ nombre: `Medio · U mes ${i + 1}`, escenario: "medio" as const, salida: m_(i + 1, "U"), esperado: v, tolerancia: 1 })),
  ];
}

/** Todos los vectores de la sección 6.7, uno por escenario y salida (se arman solo si alguien los usa). */
export const VECTORES_PRUEBA: readonly VectorPrueba[] = /* @__PURE__ */ construirVectores();

/** Corre todos los vectores. Devuelve las filas «pasa / no pasa» y el total. */
export function verificarModelo(): { ok: boolean; pasan: number; total: number; filas: FilaPrueba[] } {
  const cache = new Map<string, Resultado>();
  const filas = VECTORES_PRUEBA.map((v): FilaPrueba => {
    const llave = `${v.escenario}|${JSON.stringify(v.cambios ?? null)}`;
    let r = cache.get(llave);
    if (!r) { r = calcular(entradasEjemplo(v.escenario, v.cambios)); cache.set(llave, r); }
    const obtenido = v.salida(r);
    const pasa = typeof v.esperado === "number" && typeof obtenido === "number" ? Math.abs(obtenido - v.esperado) <= v.tolerancia + 1e-9 : obtenido === v.esperado;
    return { nombre: v.nombre, escenario: v.escenario, esperado: v.esperado, obtenido: typeof obtenido === "number" ? Math.round(obtenido * 100) / 100 : obtenido, pasa };
  });
  const pasan = filas.filter((f) => f.pasa).length;
  return { ok: pasan === filas.length, pasan, total: filas.length, filas };
}
