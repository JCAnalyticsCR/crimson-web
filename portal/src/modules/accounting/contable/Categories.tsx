/* Categorias de gasto: renombrar, activar/desactivar (no se borra), el tipo de gasto especifico del D-151/D-270 y en
   que grupo del estado de resultados cae (costo de ventas u operacion; vacio = por su nombre). */
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, I, Icon, Loading } from "../../../ui/components";

type Cat = { id: number; name: string; active: boolean; d151_kind: string | null; pnl_group: string | null; pnl_group_effective?: string; pnl_group_auto?: boolean };
const GRUPO: Record<string, string> = { costo_ventas: "Costo de ventas", operativo: "Gastos de operación" };
const D151 = [["", "Ninguno"], ["alquiler", "Alquiler"], ["servicios_profesionales", "Servicios profesionales"], ["comisiones", "Comisiones"], ["intereses", "Intereses"]];

export default function Categories() {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Cat[] | null>(null);
  const [nuevo, setNuevo] = useState("");
  const canEdit = allows("accounting.editar");
  const load = () => api<Cat[]>("/expense-categories").then(setRows).catch((e) => toast(e.message, "bad"));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (c: Cat) => {
    try { await api(`/expense-categories/${c.id}`, { method: "PUT", json: { name: c.name, active: c.active, d151_kind: c.d151_kind || null, pnl_group: c.pnl_group || null } }); toast("Categoría guardada"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const add = async () => {
    if (!nuevo.trim()) return;
    try { await api("/expense-categories", { method: "POST", json: { name: nuevo.trim() } }); setNuevo(""); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const set = (id: number, patch: Partial<Cat>) => setRows((r) => (r || []).map((x) => (x.id === id ? { ...x, ...patch } : x)));
  return (
    <Card flush title="Categorías de gasto" extra={allows("accounting.crear") && <div style={{ display: "flex", gap: 6 }}><input className="input" style={{ height: 32 }} placeholder="Nueva categoría" value={nuevo} onChange={(e) => setNuevo(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} /><button className="btn btn--soft btn--sm" onClick={add}><Icon d={I.plus} />Agregar</button></div>}>
      {rows === null ? <Loading /> : rows.length === 0 ? <Empty /> : (
        <div style={{ overflowX: "auto" }}>
          <table className="table"><thead><tr><th>Nombre</th><th>Tipo para el D-151</th><th>Estado de resultados</th><th>Activa</th><th /></tr></thead>
            <tbody>{rows.map((c) => <tr key={c.id} style={{ opacity: c.active ? 1 : 0.6 }}>
              <td><input className="input" style={{ height: 32 }} disabled={!canEdit} value={c.name} onChange={(e) => set(c.id, { name: e.target.value })} /></td>
              <td><select className="select" style={{ height: 32 }} disabled={!canEdit} value={c.d151_kind || ""} onChange={(e) => set(c.id, { d151_kind: e.target.value || null })}>{D151.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
              <td><select className="select" style={{ height: 32 }} disabled={!canEdit} value={c.pnl_group || ""} onChange={(e) => set(c.id, { pnl_group: e.target.value || null })} aria-label="Grupo en el estado de resultados">
                <option value="">{`Por su nombre${c.pnl_group_effective && !c.pnl_group ? ` (${GRUPO[c.pnl_group_effective] || c.pnl_group_effective})` : ""}`}</option>
                {Object.entries(GRUPO).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select></td>
              <td><input type="checkbox" disabled={!canEdit} checked={c.active} onChange={(e) => set(c.id, { active: e.target.checked })} aria-label="Activa" /></td>
              <td className="num">{canEdit && <button className="btn btn--ghost btn--sm" onClick={() => save(c)}>Guardar</button>}</td>
            </tr>)}</tbody></table>
        </div>
      )}
      <p className="muted" style={{ padding: "10px 18px", fontSize: 12, margin: 0 }}>Desactivar no borra: la categoría deja de salir en los selectores y sus gastos la conservan. «Costo de ventas» resta antes de la utilidad bruta; «Gastos de operación», después.</p>
    </Card>
  );
}
