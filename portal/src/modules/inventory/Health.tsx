/* Salud del inventario: compara cada documento contra el kardex y lista discrepancias corregibles. Tambien lista
   los productos con proveedor de catalogo (Eurocomp) sin item vinculado: su existencia del proveedor no cuenta.
   Existencias negativas: se corrigen con un AJUSTE documentado (motivo obligatorio, queda en el kardex); si hay una
   compra del producto sin recibir, el API pide confirmar (recibirla es la correccion). Pedidos con salida adelantada
   (la factura del pago saco lo bajo pedido antes de que llegara): corregir devuelve esa salida. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon, Loading } from "../../ui/components";

type Issue = {
  kind: "negativo" | "factura" | "proyecto" | "pedido" | "pedido_adelantado" | "pedido_sin_decidir" | "garantia" | "garantia_vieja" | "sin_vincular";
  ref_id: number; doc: string; product: string; detail: string; fixable: boolean; action?: string; code?: string | null;
  warehouse_id?: number; qty?: string;
};
type HealthData = { checked_at: string; days: number; total: number; counts: Record<string, number>; issues: Issue[] };

const KIND_LABEL: Record<Issue["kind"], string> = {
  negativo: "Existencias negativas", factura: "Facturas", proyecto: "Proyectos", pedido: "Pedidos web", pedido_adelantado: "Salidas adelantadas", pedido_sin_decidir: "Devoluciones sin decidir",
  garantia: "Garantías", garantia_vieja: "Garantías de más de 30 días", sin_vincular: "Proveedor sin vincular",
};
const KIND_ORDER: Issue["kind"][] = ["pedido_adelantado", "negativo", "factura", "proyecto", "pedido", "pedido_sin_decidir", "garantia", "garantia_vieja", "sin_vincular"];

export default function Health() {
  const { toast, allows } = useSession();
  const [data, setData] = useState<HealthData | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  /* toast cambia de identidad cada vez que se muestra un aviso: como dependencia, un error al cargar volveria a
     disparar la carga sin fin (mismo cuidado que SupplierCatalog). */
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const load = useCallback(() => {
    setData(null);
    api<HealthData>("/stock/health").then(setData).catch((e) => toastRef.current(e instanceof Error ? e.message : "Error", "bad"));
  }, []);

  useEffect(() => { load(); }, [load]);

  const fix = async (issue: Issue) => {
    let extra: Record<string, unknown> = {};
    if (issue.kind === "negativo") {
      const motivo = (window.prompt(`Ajuste de +${issue.qty ?? ""} para dejar ${issue.product} en cero en ${issue.doc}.
¿Por qué? (queda en el kardex: conteo físico, venta sin entrada registrada…)`) || "").trim();
      if (!motivo) return;
      extra = { warehouse_id: issue.warehouse_id, motivo };
    }
    setBusy(issue.ref_id);
    const send = (force: boolean) => api<{ movimientos: number }>("/stock/health/fix", { method: "POST", json: { kind: issue.kind, ref_id: issue.ref_id, ...extra, ...(force ? { force: true } : {}) } });
    try {
      let r: { movimientos: number };
      try { r = await send(false); }
      catch (e) {
        const msg = e instanceof Error ? e.message : "Error";
        if (issue.kind !== "negativo" || !/sin recibir/.test(msg) || !window.confirm(`${msg}

¿Ajustar igual?`)) throw e;
        r = await send(true);
      }
      toast(`Corregido: ${r.movimientos} movimiento${r.movimientos !== 1 ? "s" : ""} en el kardex`);
      setBusy(null);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); setBusy(null); }
  };

  const fmtChecked = (s: string) => new Date(s).toLocaleString("es-CR", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

  if (data === null) return <Loading />;

  const sorted = [...data.issues].sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));

  return (
    <Card flush>
      <div style={{ padding: "16px 20px 4px", display: "flex", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
        <span className="muted" style={{ fontSize: 13 }}>Revisado {fmtChecked(data.checked_at)} · últimos {data.days} días</span>
        <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} />Revisar de nuevo</button>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: "0 20px 8px" }}>Compara lo que cada documento debió mover con lo que registró el kardex; corregir escribe solo la diferencia, con documento y usuario.</p>
      {data.total === 0
        ? <Empty title="Todo cuadra" hint="Cada factura, proyecto y pedido web coincide con el kardex y no hay existencias negativas." />
        : <>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "0 20px 12px" }}>
              {KIND_ORDER.filter((k) => (data.counts[k] ?? 0) > 0).map((k) => <span key={k} className="badge badge--warn">{KIND_LABEL[k]} · {data.counts[k]}</span>)}
            </div>
            <div style={{ overflowX: "auto" }}>
              <table className="table">
                <thead><tr><th>Documento</th><th>Producto</th><th>Qué pasa</th><th /></tr></thead>
                <tbody>{sorted.map((iss, i) => (
                  <tr key={i}>
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>{iss.doc}</td>
                    <td>{iss.product}</td>
                    <td>{iss.detail}</td>
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      {iss.action === "vincular"
                        ? <Link className="btn btn--soft btn--sm" to="/inventario?tab=proveedores" title={iss.code ? `Buscá ${iss.code} en el catálogo del proveedor y vinculalo` : undefined}>Vincular</Link>
                        : iss.fixable && allows("inventory.editar")
                        ? <button className="btn btn--soft btn--sm" disabled={busy === iss.ref_id} onClick={() => fix(iss)} title={iss.kind === "negativo" ? "Ajuste documentado que deja la bodega en cero (pide el motivo)" : undefined}>{busy === iss.ref_id ? "Corrigiendo…" : iss.kind === "negativo" ? "Ajustar a cero" : iss.kind === "pedido_adelantado" ? "Devolver salida" : "Corregir"}</button>
                        : <span className="muted" style={{ fontSize: 12.5 }}>{iss.kind === "negativo" ? "Revisá con un conteo o un ajuste" : iss.kind === "pedido_sin_decidir" ? "Decidí en el pedido si volvió" : iss.kind === "garantia_vieja" ? "Dale seguimiento en Garantías" : null}</span>}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </>
      }
    </Card>
  );
}
