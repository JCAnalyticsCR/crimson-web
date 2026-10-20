/* Disponibilidad interna de un producto, tal como la calcula el API (services/availability, audiencia "interno"):
   propio libre en bodegas de venta (las de garantías nunca) y lo del proveedor con su fecha. Una sola forma de
   mostrarla en Productos, Inventario, cotizaciones/facturas, punto de venta y recomendación de equipos. */

export type Availability = {
  state: "en_bodega" | "bajo_pedido" | "consultar" | "agotado" | "servicio" | string;
  label: string;
  label_publico?: string | null;
  own_on_hand?: number | string | null;
  own_free?: number | string | null;
  own_reserved?: number | string | null;
  supplier_linked?: boolean;
  supplier_qty?: number | string | null;
  supplier_available?: number | string | null;
  supplier_name?: string | null;
  supplier_source?: string | null;
  supplier_fresh?: boolean;
  supplier_age?: string | null;
  supplier_updated_at?: string | null;
  supplier_unlinked_qty?: number | null;
  max_qty?: number | string | null;
};

export const STATE_UI: Record<string, { label: string; tone: string }> = {
  en_bodega: { label: "En bodega", tone: "ok" },
  bajo_pedido: { label: "Bajo pedido", tone: "warn" },
  consultar: { label: "Consultar", tone: "warn" },
  agotado: { label: "Sin existencia", tone: "bad" },
  servicio: { label: "Servicio", tone: "muted" },
};

const num = (v: number | string | null | undefined) => Number(v ?? 0) || 0;
export const qty = (v: number | string | null | undefined) => num(v).toLocaleString("es-CR", { maximumFractionDigits: 3 });

/** se vende sin respaldo: nadie lo tiene (o el dato del proveedor esta viejo) */
export const sinExistencia = (a?: Availability | null) => !!a && (a.state === "agotado" || a.state === "consultar");

/** "Propio: 2 · Eurocomp: 12 · hace 3 h" en una linea corta */
export function availText(a?: Availability | null): string {
  if (!a) return "—";
  if (a.state === "servicio") return "Servicio";
  const parts = [`Propio: ${qty(a.own_free)}${num(a.own_reserved) > 0 ? ` (${qty(a.own_reserved)} reservado)` : ""}`];
  if (a.supplier_linked) parts.push(`${a.supplier_name || "Proveedor"}: ${qty(a.supplier_qty)}${a.supplier_age ? ` · ${a.supplier_age}` : ""}${a.supplier_fresh ? "" : " (dato viejo)"}`);
  else if (a.supplier_unlinked_qty) parts.push("proveedor sin vincular");
  return parts.join(" · ");
}

export function AvailBadge({ a }: { a?: Availability | null }) {
  if (!a) return <span className="muted">—</span>;
  const st = STATE_UI[a.state] || { label: a.state, tone: "muted" };
  return <span className={`badge badge--${st.tone}`}>{st.label}</span>;
}

/** Celda completa: estado + propio / proveedor / actualizado */
export function AvailCell({ a, compact = false }: { a?: Availability | null; compact?: boolean }) {
  if (!a) return <span className="muted">—</span>;
  return (
    <span style={{ display: "inline-flex", flexDirection: "column", gap: 2 }}>
      <AvailBadge a={a} />
      {!compact && a.state !== "servicio" && <span className="meta" style={{ whiteSpace: "normal" }}>{availText(a)}</span>}
    </span>
  );
}
