/* Inventario para el aliado (20:01-20:14): "solo cantidades". Disponible por bodega (ya descontado lo reservado) y si
   se consigue bajo pedido. Ninguna columna de dinero. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Empty } from "../../ui/components";
import { errorText } from "./shared";

type Row = { product_id: number; code: string; name: string; unit: string; disponible_total: string; bodegas: { nombre: string; disponible: string }[]; bajo_pedido: boolean };

export default function AllyInventory() {
  const [q, setQ] = useState("");
  const [todos, setTodos] = useState(false);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => api<{ rows: Row[] }>(`/aliado/inventario?q=${encodeURIComponent(q)}&con_existencia=${!todos}`).then((r) => setRows(r.rows)).catch((e) => setErr(errorText(e))), 250);
    return () => clearTimeout(t);
  }, [q, todos]);
  return (
    <>
      <div className="cp-h"><h1 className="h1">Inventario</h1></div>
      <div className="cp-grid cp-grid--2" style={{ marginBottom: 12 }}>
        <input className="input" placeholder="Buscar producto o código" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="pf-bool"><input type="checkbox" checked={todos} onChange={(e) => setTodos(e.target.checked)} />Mostrar también lo agotado</label>
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="al-table">
          <table>
            <thead><tr><th>Producto</th><th className="num">Disponible</th><th>Por bodega</th><th>Bajo pedido</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.product_id}>
                  <td><b>{r.name}</b><div className="meta">{r.code}</div></td>
                  <td className="num">{Number(r.disponible_total)} {r.unit}</td>
                  <td>{r.bodegas.length ? r.bodegas.map((b) => <div key={b.nombre}>{b.nombre}: {Number(b.disponible)}</div>) : "—"}</td>
                  <td>{r.bajo_pedido ? <span className="badge badge--info">Bajo pedido</span> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <Empty title="Sin existencias con ese filtro" />}
    </>
  );
}
