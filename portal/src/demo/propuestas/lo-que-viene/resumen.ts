/* Texto del resumen «propuestas que me interesan» (plantilla exacta de la especificacion, 4.4). Se copia a mano:
   desde la demo no se envia nada. Lo usan «Lo que viene» y el cierre de «¿Es rentable?» (/propuestas?resumen=1). */
import { CLAVES, ETAPAS, PROPUESTAS, type PropuestaId } from "../registro";
import { almacen } from "../_comun/pedidos";
import { colones } from "../_comun/formato";

export type ResumenRentabilidad = {
  v: 1; escenario: "conservador" | "medio" | "optimista"; ajustado: boolean; etapas: number[];
  utilidadMes12: number; utilidadAcum12: number; utilidadAcum24: number; recurrenteAcum12: number; recurrenteAcum24: number;
  equilibrioMes: number | null; equilibrioTexto: string; inversionTotal: number | null; actualizado: string;
};

export function leerResumenRentabilidad(): ResumenRentabilidad | null {
  try {
    const r = JSON.parse(almacen.local.get(CLAVES.rentabilidadResumen) || "null") as ResumenRentabilidad | null;
    if (!r || r.v !== 1 || !["conservador", "medio", "optimista"].includes(r.escenario)) return null;
    return r;
  } catch { return null; }
}

const dd = (n: number) => String(n).padStart(2, "0");
export const fechaCorta = (d: Date) => `${dd(d.getDate())}/${dd(d.getMonth() + 1)}/${d.getFullYear()}`;

export function construirResumen(ids: PropuestaId[], notas: string, rent: ResumenRentabilidad | null, hoy = new Date()): string {
  const out: string[] = [
    "Tienda Crimson · propuestas que me interesan",
    `Vista previa · ${fechaCorta(hoy)}. No es un pedido ni una aceptación: es para conversar los siguientes pasos.`,
  ];
  const elegidas = new Set(ids);
  for (const e of ETAPAS) {
    const de = e.ids.filter((id) => elegidas.has(id));
    if (!de.length) continue;
    out.push("", `Etapa ${e.n} · ${e.titulo}`);
    for (const id of de) out.push(`- ${id} ${PROPUESTAS.find((p) => p.id === id)?.titulo || ""}`);
  }
  if (rent) {
    out.push(
      "",
      `Números que revisé en «¿Es rentable?» (escenario ${rent.escenario}${rent.ajustado ? ", con mis cambios" : ""}; supuestos de ejemplo editables):`,
      `- Utilidad incremental al mes 12: ${colones(rent.utilidadMes12)} por mes`,
      `- Utilidad acumulada a 24 meses: ${colones(rent.utilidadAcum24)}`,
      `- Ingreso recurrente del plan, acumulado a 24 meses: ${colones(rent.recurrenteAcum24)}`,
      `- Punto de equilibrio: ${rent.equilibrioTexto}`,
      "Son supuestos, no resultados garantizados.",
    );
  }
  const n = notas.trim();
  if (n) out.push("", "Notas:", n);
  return out.join("\n");
}
