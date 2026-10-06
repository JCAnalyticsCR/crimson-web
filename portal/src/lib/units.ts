/* Unidades y tratamiento de lineas. Espejo de api/app/services/units.py: la clave es lo que se guarda;
   al emitir a Hacienda se traduce a la unidad oficial (entre parentesis). */
export type Unit = { value: string; label: string; short: string; service?: boolean };
export const UNITS: Unit[] = [
  { value: "Unid", label: "Unidad (Unid)", short: "Unid" },
  { value: "m", label: "Metro (m)", short: "m" },
  { value: "m²", label: "Metro cuadrado (m²)", short: "m²" },
  { value: "servicio", label: "Servicio (Os)", short: "servicio", service: true },
  { value: "Sp", label: "Servicio profesional (Sp)", short: "serv. prof.", service: true },
  { value: "dia", label: "Día (d)", short: "día", service: true },
  { value: "jornada", label: "Jornada (d)", short: "jornada", service: true },
  { value: "hora", label: "Hora (h)", short: "hora", service: true },
  { value: "mes", label: "Mes (Os)", short: "mes", service: true },
  { value: "Os", label: "Otro servicio (Os)", short: "otro serv.", service: true },
];
/* Opciones del selector: si la linea trae una unidad vieja que no esta en la lista, se conserva. */
export const unitOptions = (current?: string | null): Unit[] =>
  current && !UNITS.some((u) => u.value === current) ? [...UNITS, { value: current, label: current, short: current }] : UNITS;

export type Treatment = "normal" | "pendiente" | "aportado" | "cortesia" | "excluido";
export const TREATMENTS: { value: Treatment; label: string; hint: string }[] = [
  { value: "normal", label: "Normal", hint: "Suma al total" },
  { value: "pendiente", label: "Pendiente", hint: "Falta costo o precio del proveedor: se puede guardar, no enviar ni convertir" },
  { value: "aportado", label: "Aportado", hint: "Equipo del cliente o de un aliado: sale con leyenda y no suma" },
  { value: "cortesia", label: "Cortesía", hint: "Sale con leyenda «Cortesía» y no suma" },
  { value: "excluido", label: "Excluido", hint: "Sale en la sección de exclusiones y no suma" },
];
export const NO_SUMA: Treatment[] = ["aportado", "cortesia", "excluido"];
