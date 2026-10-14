/* Texto del resumen «propuestas que me interesan» (plantilla exacta de la especificacion, 4.4). Se copia a mano:
   desde la demo no se envia nada. Lo usan «Lo que viene» y el cierre de «¿Es rentable?» (/propuestas?resumen=1). */
import { CLAVES, ETAPAS, PROPUESTAS, type PropuestaId } from "../registro";
import { almacen } from "../_comun/pedidos";
import { colones } from "../_comun/formato";

export type ResumenRentabilidad = {
  v: 1; escenario: "conservador" | "medio" | "optimista"; ajustado: boolean; etapas: number[];
  utilidadMes12: number; utilidadAcum12: number; utilidadAcum24: number; recurrenteAcum12: number; recurrenteAcum24: number;
  equilibrioMes: number | null; equilibrioTexto: string; inversionTotal: number | null; actualizado: string;
  inversionPorEtapa?: (number | null)[]; sumarHoras?: boolean; sinInversion?: number[]; equilibrioTiendaTexto?: string;
};

/** «Me interesa esta etapa» de la Etapa 0 (la tienda propia, dejar Fygaro): la marca el cierre de «¿Es rentable?». */
export const leerEtapa0 = (): boolean => almacen.local.get(CLAVES.etapa0) === "1";

const lista = (n: number[]) => (n.length > 1 ? `${n.slice(0, -1).join(", ")} y ${n[n.length - 1]}` : String(n[0] ?? ""));

export function leerResumenRentabilidad(): ResumenRentabilidad | null {
  try {
    const r = JSON.parse(almacen.local.get(CLAVES.rentabilidadResumen) || "null") as ResumenRentabilidad | null;
    if (!r || r.v !== 1 || !["conservador", "medio", "optimista"].includes(r.escenario)) return null;
    return r;
  } catch { return null; }
}

const dd = (n: number) => String(n).padStart(2, "0");
export const fechaCorta = (d: Date) => `${dd(d.getDate())}/${dd(d.getMonth() + 1)}/${d.getFullYear()}`;

export function construirResumen(ids: PropuestaId[], notas: string, rent: ResumenRentabilidad | null, hoy = new Date(), etapa0 = false): string {
  const out: string[] = [
    "Tienda Crimson · propuestas que me interesan",
    `Vista previa · ${fechaCorta(hoy)}. No es un pedido ni una aceptación: es para conversar los siguientes pasos.`,
  ];
  if (etapa0) out.push("", "Etapa 0 · La tienda propia (dejar Fygaro)");
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
      `- Etapas que suman en los números: ${lista(rent.etapas.filter((n) => n <= 3))}`,
      `- Utilidad incremental al mes 12: ${colones(rent.utilidadMes12)} por mes`,
      `- Utilidad acumulada a 24 meses: ${colones(rent.utilidadAcum24)}`,
      `- Ingreso recurrente del plan, acumulado a 24 meses: ${colones(rent.recurrenteAcum24)} (ingreso sin IVA, no utilidad)`,
    );
    const inv = (rent.inversionPorEtapa || []).map((x, i) => [i, x] as const).filter((p): p is readonly [number, number] => typeof p[1] === "number" && p[1] > 0);
    const detalle = inv.length === 1 ? ` (solo la etapa ${inv[0][0]})` : inv.length > 1 ? ` (${inv.map(([i, x]) => `etapa ${i}: ${colones(x)}`).join(" · ")})` : "";
    out.push(rent.inversionTotal ? `- Inversión usada: ${colones(rent.inversionTotal)}${detalle}` : "- Inversión usada: ninguna escrita");
    out.push(`- Punto de equilibrio: ${rent.equilibrioTexto}`);
    if (rent.inversionTotal && rent.equilibrioTiendaTexto && (rent.inversionPorEtapa?.[0] ?? 0) > 0) out.push(`- Punto de equilibrio solo de la tienda propia (etapa 0): ${rent.equilibrioTiendaTexto}`);
    if (rent.sinInversion && rent.sinInversion.length) out.push(`  Ojo: ${rent.sinInversion.length > 1 ? `las etapas ${lista(rent.sinInversion)} suman` : `la etapa ${rent.sinInversion[0]} suma`} utilidad sin inversión escrita: el equilibrio sale más corto de lo real.`);
    out.push(`- Horas liberadas: ${rent.sumarHoras ? "sumadas al resultado" : "no sumadas"}`);
    out.push("Son supuestos, no resultados garantizados.");
  }
  const n = notas.trim();
  if (n) out.push("", "Notas:", n);
  return out.join("\n");
}
