/* Tarifa de instalacion de EJEMPLO para la maqueta A1 (y el paquete A5 y la linea de instalacion de A3).
   Crimson define las suyas en Mi Tienda; aqui solo se muestra la cuenta. Montos SIN IVA. Seccion 3.0:

     base  = (25 000 + n x 30 000) x lugar x cableado + traslado
     bajo  = floor(base x 0,90 x 1,13 / 1000) x 1000          (con IVA)
     alto  = ceil (base x 1,20 x 1,13 / 1000) x 1000          (con IVA)
     costo = base x 0,65   ->   precio = costo / (1 - 0,35)   (margen SOBRE VENTA de 35 %, dato de Crimson)

   Control: 4 camaras · Casa · cable Si · GAM -> base 174 000, rango 176 000 - 236 000, costo 113 100.
   Fechas siempre relativas a hoy (Date.now()), nunca fijas. Funciones puras: sin React. */

export const IVA = 0.13;
export const MARGEN = 0.35;
export const VISITA = 25_000;
export const POR_CAMARA = 30_000;
export const TRASLADO = 20_000;
export const CAMARAS_MIN = 1;
export const CAMARAS_MAX = 16;

export type LugarId = "casa" | "negocio" | "condominio" | "bodega";
export type CableId = "si" | "no" | "nose";

export const LUGARES: { id: LugarId; titulo: string; detalle: string; factor: number }[] = [
  { id: "casa", titulo: "Casa", detalle: "Entradas, patio y cochera", factor: 1 },
  { id: "negocio", titulo: "Negocio u oficina", detalle: "Caja, mostrador y bodega", factor: 1.1 },
  { id: "condominio", titulo: "Condominio", detalle: "Accesos, parqueo y perímetro", factor: 1.2 },
  { id: "bodega", titulo: "Bodega o planta", detalle: "Perímetro, andenes y patio", factor: 1.3 },
];
export const CABLES: { id: CableId; titulo: string; corto: string; factor: number }[] = [
  { id: "si", titulo: "Sí, hay que pasar cable", corto: "con cableado nuevo", factor: 1.2 },
  { id: "no", titulo: "No, ya hay cableado de red", corto: "con el cableado de red que ya existe", factor: 1 },
  { id: "nose", titulo: "No sé", corto: "calculado con cableado nuevo", factor: 1.2 },
];
export const NOTA_NO_SE = "Calculamos con cableado; el técnico lo confirma.";

export const PROVINCIAS: { id: string; nombre: string; gam: boolean }[] = [
  { id: "san-jose", nombre: "San José", gam: true },
  { id: "alajuela", nombre: "Alajuela", gam: true },
  { id: "cartago", nombre: "Cartago", gam: true },
  { id: "heredia", nombre: "Heredia", gam: true },
  { id: "guanacaste", nombre: "Guanacaste", gam: false },
  { id: "puntarenas", nombre: "Puntarenas", gam: false },
  { id: "limon", nombre: "Limón", gam: false },
];

export type ConfigA1 = { camaras: number; provincia: string; lugar: LugarId; cable: CableId };
export const CONFIG_INICIAL: ConfigA1 = { camaras: 4, provincia: "heredia", lugar: "casa", cable: "si" };

const c2 = (n: number) => Math.round(n * 100) / 100;
export const lugarDe = (id: string) => LUGARES.find((l) => l.id === id) ?? LUGARES[0];
export const cableDe = (id: string) => CABLES.find((c) => c.id === id) ?? CABLES[0];
export const provinciaDe = (id: string) => PROVINCIAS.find((p) => p.id === id) ?? PROVINCIAS[3];
export const clampCamaras = (n: number) => Math.max(CAMARAS_MIN, Math.min(CAMARAS_MAX, Math.round(Number.isFinite(n) ? n : CAMARAS_MIN)));

export type LineaCosteo = { concepto: string; costo: number; precio: number };
export type Estimado = {
  camaras: number; lugar: (typeof LUGARES)[number]; cable: (typeof CABLES)[number]; provincia: (typeof PROVINCIAS)[number];
  fueraGam: boolean; sinAjuste: number; factor: number; traslado: number; base: number;
  bajo: number; alto: number; costo: number; precio: number; margen: number;
  /** "margen sobre venta, no recargo": lo que pasaria con un recargo de 35 % sobre el costo */
  conRecargo: { precio: number; margen: number };
  lineas: LineaCosteo[];
};

export function estimar(cfg: ConfigA1): Estimado {
  const camaras = clampCamaras(cfg.camaras);
  const lugar = lugarDe(cfg.lugar);
  const cable = cableDe(cfg.cable);
  const provincia = provinciaDe(cfg.provincia);
  const fueraGam = !provincia.gam;
  const sinAjuste = VISITA + camaras * POR_CAMARA;
  const factor = c2(lugar.factor * cable.factor);
  const traslado = fueraGam ? TRASLADO : 0;
  const base = c2(sinAjuste * factor + traslado);
  const bajo = Math.floor(c2(base * 0.9 * (1 + IVA)) / 1000) * 1000;
  const alto = Math.ceil(c2(base * 1.2 * (1 + IVA)) / 1000) * 1000;
  const costoDe = (precio: number) => c2(precio * (1 - MARGEN));
  const lineas: LineaCosteo[] = [
    { concepto: "Visita y configuración", costo: costoDe(VISITA), precio: VISITA },
    { concepto: `Instalación de cámara × ${camaras}`, costo: costoDe(camaras * POR_CAMARA), precio: camaras * POR_CAMARA },
  ];
  if (factor !== 1) {
    const ajuste = c2(sinAjuste * (factor - 1));
    lineas.push({ concepto: `Ajuste por lugar y cableado (×${factor.toLocaleString("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })})`, costo: costoDe(ajuste), precio: ajuste });
  }
  if (traslado) lineas.push({ concepto: "Traslado fuera de la GAM", costo: costoDe(traslado), precio: traslado });
  const costo = c2(lineas.reduce((t, l) => t + l.costo, 0));
  const precio = c2(costo / (1 - MARGEN));
  const recargoPrecio = c2(costo * (1 + MARGEN));
  return {
    camaras, lugar, cable, provincia, fueraGam, sinAjuste, factor, traslado, base, bajo, alto, costo, precio,
    margen: precio ? (precio - costo) / precio : 0,
    conRecargo: { precio: recargoPrecio, margen: recargoPrecio ? (recargoPrecio - costo) / recargoPrecio : 0 },
    lineas,
  };
}

// ---------- fecha tentativa: 10 dias habiles (lunes a sabado) desde pasado manana ----------
export type Turno = "m" | "t";
export type Dia = { iso: string; fecha: Date; sabado: boolean; lleno: Record<Turno, boolean> };

const isoLocal = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
/** disponibilidad DE EJEMPLO: una franja sale "Lleno" si (dia del mes + (tarde ? 1 : 0)) % 4 === 0 */
export const estaLleno = (d: Date, turno: Turno) => (d.getDate() + (turno === "t" ? 1 : 0)) % 4 === 0;

export function diasHabiles(ahora: number = Date.now()): Dia[] {
  const hoy = new Date(ahora);
  const d = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() + 2);
  const out: Dia[] = [];
  while (out.length < 10) {
    if (d.getDay() !== 0) {
      const f = new Date(d);
      const sabado = f.getDay() === 6;
      out.push({ iso: isoLocal(f), fecha: f, sabado, lleno: { m: estaLleno(f, "m"), t: sabado ? true : estaLleno(f, "t") } });
    }
    d.setDate(d.getDate() + 1);
  }
  return out;
}
export type Franja = { iso: string; turno: Turno };
/** la franja guardada sigue sirviendo solo si su dia esta en la lista de hoy y no esta llena */
export function franjaValida(f: Franja | null, dias: Dia[]): { dia: Dia; turno: Turno } | null {
  if (!f) return null;
  const dia = dias.find((x) => x.iso === f.iso);
  if (!dia || dia.lleno[f.turno] || (dia.sabado && f.turno === "t")) return null;
  return { dia, turno: f.turno };
}
export const primeraLibre = (dias: Dia[]): { dia: Dia; turno: Turno } | null => {
  for (const dia of dias) { if (!dia.lleno.m) return { dia, turno: "m" }; if (!dia.sabado && !dia.lleno.t) return { dia, turno: "t" }; }
  return null;
};
export const turnoTexto = (t: Turno) => (t === "m" ? "en la mañana" : "en la tarde");
/** "viernes, 23 de octubre" (sin año) */
export const diaLargo = (d: Date) => new Intl.DateTimeFormat("es-CR", { weekday: "long", day: "numeric", month: "long" }).format(d);
