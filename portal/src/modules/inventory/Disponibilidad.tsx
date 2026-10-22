/* Inventario → Existencias → Disponibilidad: TODOS los productos vendibles con la disponibilidad de la funcion unica
   del API (/stock/availability): propio libre / reservado (solo bodegas de venta: las de garantías no suman),
   proveedor (Eurocomp) con su fecha y el estado. Primero lo propio. Con cero existencia propia muestra lo del
   proveedor en vez de salir vacía. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { Card, Empty, Loading } from "../../ui/components";
import { AvailBadge, qty, type Availability } from "../../ui/Availability";

type Row = Availability & { id: number; code: string; name: string; brand: string | null; show_on_web: boolean; min_stock: number; supplier_old: boolean };
type Summary = {
  productos: number; con_propio: number; solo_proveedor: number; sin_existencia: number; dato_viejo: number; sin_vincular: number;
  propio_libre: string | number; propio_reservado: string | number; proveedor: string | number;
};
const FILTROS: [string, string][] = [["", "Todos"], ["propio", "Con existencia propia"], ["eurocomp", "Solo del proveedor"], ["sin", "Sin existencia"], ["viejo", "Dato del proveedor viejo"], ["sin_vincular", "Proveedor sin vincular"]];

export default function Disponibilidad({ onKardex }: { onKardex?: (p: { id: number; code: string; name: string }) => void }) {
  const [filtro, setFiltro] = useState("");
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ summary: Summary; items: Row[]; total: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    const p = new URLSearchParams();
    if (filtro) p.set("filtro", filtro);
    if (q.trim()) p.set("q", q.trim());
    setData(null);
    api<{ summary: Summary; items: Row[]; total: number }>(`/stock/availability?${p}`).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e instanceof Error ? e.message : "Error"));
  };
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [filtro, q]); // eslint-disable-line react-hooks/exhaustive-deps

  const s = data?.summary;
  const pill = (k: string, label: string, v: number | string) => (
    <div key={k} className={filtro === k ? "hot" : ""} onClick={() => setFiltro(filtro === k ? "" : k)} style={{ cursor: "pointer" }}><b>{v}</b><span>{label}</span></div>
  );
  return (
    <>
      {s && (
        <div className="pending">
          {pill("", "Productos", s.productos)}
          {pill("propio", "Con existencia propia", s.con_propio)}
          {pill("eurocomp", "Solo del proveedor", s.solo_proveedor)}
          {pill("sin", "Sin existencia", s.sin_existencia)}
          {pill("viejo", "Dato del proveedor viejo", s.dato_viejo)}
          {s.sin_vincular > 0 && pill("sin_vincular", "Proveedor sin vincular", s.sin_vincular)}
          <div><b>{qty(s.propio_libre)}</b><span>Unidades propias libres{Number(s.propio_reservado) > 0 ? ` · ${qty(s.propio_reservado)} reservadas` : ""}</span></div>
        </div>
      )}
      <Card
        title="Disponibilidad"
        flush
        extra={
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input className="input" style={{ width: 220 }} placeholder="Buscar código, nombre o modelo" aria-label="Buscar producto" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="select select--sm" aria-label="Filtrar" value={filtro} onChange={(e) => setFiltro(e.target.value)}>{FILTROS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          </div>
        }
      >
        {err ? <Empty title="No se pudo cargar" hint={err} /> : data === null ? <Loading /> : data.items.length === 0 ? <Empty hint={filtro ? "Ningún producto con ese filtro." : "Todavía no hay productos en el catálogo."} /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>Producto</th><th className="num" title="Libre en bodegas de venta (sin garantías)">Propio libre</th><th className="num" title="Reservas confirmadas y vigentes">Reservado</th><th className="num">Proveedor</th><th title="Cuándo se consultó al proveedor y cuándo cambió de verdad su existencia o su costo">Proveedor consultado</th><th>Estado</th><th /></tr></thead>
              <tbody>{data.items.map((r) => (
                <tr key={r.id}>
                  <td style={{ minWidth: 200 }}><b>{r.name}</b>{!r.show_on_web && <span className="badge badge--muted" style={{ marginLeft: 6 }}>No publicado</span>}<div className="meta mono">{r.code}{r.brand ? ` · ${r.brand}` : ""}</div></td>
                  <td className="num mono" style={{ fontWeight: Number(r.own_free ?? 0) > 0 ? 700 : undefined }}>{qty(r.own_free)}</td>
                  <td className="num mono" style={{ color: Number(r.own_reserved ?? 0) > 0 ? "var(--warn)" : "var(--text-3)" }}>{Number(r.own_reserved ?? 0) > 0 ? qty(r.own_reserved) : "—"}</td>
                  <td className="num mono">{r.supplier_linked ? <span title={r.supplier_name || undefined}>{qty(r.supplier_qty)}{Number(r.supplier_qty ?? 0) > Number(r.supplier_available ?? 0) && r.supplier_fresh ? <div className="meta">{qty(r.supplier_available)} vendibles</div> : null}</span> : r.supplier_unlinked_qty ? <Link to="/inventario?tab=proveedores" title="Tiene proveedor pero ningún ítem de su lista vinculado: su existencia no cuenta">vincular</Link> : <span className="muted">—</span>}</td>
                  <td className="muted" style={{ whiteSpace: "nowrap", color: r.supplier_old ? "var(--bad)" : undefined }}>{r.supplier_linked ? <>{r.supplier_name ? `${r.supplier_name} · ` : ""}consultado {r.supplier_age || "sin fecha"}{r.supplier_old ? " · viejo" : ""}{r.supplier_changed_age && <div className="meta" style={{ textTransform: "none" }} title="Último cambio real de existencia o costo">último cambio {r.supplier_changed_age}</div>}</> : "—"}</td>
                  <td><AvailBadge a={r} />{r.label_publico && <div className="meta" style={{ textTransform: "none" }}>Tienda: {r.ocultar ? "oculto (agotado)" : r.label_publico}</div>}</td>
                  <td className="num">{onKardex && <button className="btn btn--ghost btn--sm" onClick={() => onKardex({ id: r.id, code: r.code, name: r.name })}>Movimientos</button>}</td>
                </tr>
              ))}</tbody>
            </table>
            {data.total > data.items.length && <p className="meta" style={{ padding: 12 }}>Mostrando {data.items.length} de {data.total}: buscá para acotar.</p>}
          </div>
        )}
      </Card>
    </>
  );
}
