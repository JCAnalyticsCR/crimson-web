/* Inventarios por ubicacion: existencias (calculadas del ledger), movimientos, transferencias y alertas de stock minimo. */
import { useEffect, useState } from "react";
import { api, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";

type Wh = { id: number; name: string; location: string | null; is_default: boolean; active: boolean };
type Level = { product_id: number; warehouse_id: number; quantity: string; code: string; name: string; min_stock: number; warehouse: string };
type Low = { product_id: number; code: string; name: string; quantity: string; min_stock: number };

export default function Inventory() {
  const { toast } = useSession();
  const [whs, setWhs] = useState<Wh[]>([]);
  const [levels, setLevels] = useState<Level[]>([]);
  const [low, setLow] = useState<Low[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [filter, setFilter] = useState<number | "">("");
  const [modal, setModal] = useState<"move" | "transfer" | "wh" | null>(null);
  const [mv, setMv] = useState({ product_id: "", warehouse_id: "", kind: "entrada", quantity: "1", reference: "", note: "" });
  const [tr, setTr] = useState({ product_id: "", from_id: "", to_id: "", quantity: "1" });
  const [wh, setWh] = useState({ name: "", location: "", is_default: false });

  const load = () => {
    api<Wh[]>("/warehouses").then(setWhs);
    api<{ levels: Level[]; low: Low[] }>(`/stock${filter ? `?warehouse_id=${filter}` : ""}`).then((r) => { setLevels(r.levels); setLow(r.low); });
    api<{ items: Product[] }>("/products?limit=100").then((r) => setProducts(r.items.filter((p) => p.item_type === "producto")));
  };
  useEffect(load, [filter]);

  const submit = async () => {
    try {
      if (modal === "move") await api("/stock/movements", { method: "POST", json: { ...mv, product_id: Number(mv.product_id), warehouse_id: Number(mv.warehouse_id) } });
      if (modal === "transfer") await api("/stock/transfer", { method: "POST", json: { ...tr, product_id: Number(tr.product_id), from_id: Number(tr.from_id), to_id: Number(tr.to_id) } });
      if (modal === "wh") await api("/warehouses", { method: "POST", json: wh });
      toast("Guardado"); setModal(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const total = levels.reduce((a, l) => a + Number(l.quantity), 0);

  return (
    <>
      <div className="page-head">
        <div><div className="meta">06 · Inventarios</div><h1 className="h1">Inventarios</h1></div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={() => setModal("wh")}><Icon d={I.plus} />Ubicación</button>
          <button className="btn btn--soft btn--sm" onClick={() => setModal("transfer")}><Icon d={I.arrow} />Transferir</button>
          <button className="btn btn--crimson" onClick={() => setModal("move")}><Icon d={I.plus} />Movimiento</button>
        </div>
      </div>

      <div className="pending">
        {whs.map((w) => <div key={w.id} className={filter === w.id ? "hot" : ""} onClick={() => setFilter(filter === w.id ? "" : w.id)} style={{ cursor: "pointer" }}><b>{levels.filter((l) => l.warehouse_id === w.id).reduce((a, l) => a + Number(l.quantity), 0)}</b><span>{w.name}{w.is_default ? " · predeterminada" : ""}{w.location ? ` · ${w.location}` : ""}</span></div>)}
        <div><b>{total}</b><span>Unidades {filter ? "en la ubicación" : "totales"}</span></div>
        <div className={low.length ? "hot" : ""}><b>{low.length}</b><span>Productos bajo mínimo</span></div>
      </div>

      <div className="grid-2">
        <Card title="Existencias por ubicación" flush extra={<a className="btn btn--ghost btn--sm" href="/api/reports/inventario?format=xlsx" target="_blank" rel="noopener"><Icon d={I.reports} />Excel</a>}>
          {levels.length === 0 ? <Empty hint="Registrá una entrada para empezar; las ventas descuentan solas de la ubicación predeterminada." /> : (
            <table className="table"><thead><tr><th>Código</th><th>Producto</th><th>Ubicación</th><th className="num">Cantidad</th><th className="num">Mínimo</th></tr></thead>
              <tbody>{levels.map((l, i) => <tr key={i}><td className="mono muted">{l.code}</td><td style={{ fontWeight: 600 }}>{l.name}</td><td className="muted">{l.warehouse}</td><td className="num mono" style={{ color: Number(l.quantity) <= l.min_stock ? "var(--bad)" : undefined, fontWeight: 700 }}>{Number(l.quantity)}</td><td className="num mono muted">{l.min_stock}</td></tr>)}</tbody></table>
          )}
        </Card>
        <Card title="Alertas de stock mínimo" flush>
          {low.length === 0 ? <Empty title="Todo en nivel" hint="Ningún producto está por debajo de su mínimo." /> : (
            <table className="table"><thead><tr><th>Producto</th><th className="num">Total</th><th className="num">Mínimo</th></tr></thead>
              <tbody>{low.map((l) => <tr key={l.product_id}><td><b>{l.name}</b><div className="meta">{l.code}</div></td><td className="num mono" style={{ color: "var(--bad)", fontWeight: 700 }}>{Number(l.quantity)}</td><td className="num mono muted">{l.min_stock}</td></tr>)}</tbody></table>
          )}
        </Card>
      </div>

      {modal === "move" && (
        <Modal title="Movimiento de inventario" onClose={() => setModal(null)} foot={<><button className="btn btn--ghost" onClick={() => setModal(null)}>Cancelar</button><button className="btn btn--crimson" onClick={submit}>Registrar</button></>}>
          <div className="grid-2">
            <Field label="Producto"><select className="select" value={mv.product_id} onChange={(e) => setMv({ ...mv, product_id: e.target.value })}><option value="">Seleccione…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}</select></Field>
            <Field label="Ubicación"><select className="select" value={mv.warehouse_id} onChange={(e) => setMv({ ...mv, warehouse_id: e.target.value })}><option value="">Seleccione…</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
            <Field label="Tipo"><select className="select" value={mv.kind} onChange={(e) => setMv({ ...mv, kind: e.target.value })}><option value="entrada">Entrada (compra)</option><option value="salida">Salida</option><option value="ajuste">Ajuste (+/−)</option></select></Field>
            <Field label="Cantidad"><input className="input input--mono" value={mv.quantity} onChange={(e) => setMv({ ...mv, quantity: e.target.value })} /></Field>
            <Field label="Referencia" hint="Orden de compra, factura del proveedor…"><input className="input" value={mv.reference} onChange={(e) => setMv({ ...mv, reference: e.target.value })} /></Field>
            <Field label="Nota"><input className="input" value={mv.note} onChange={(e) => setMv({ ...mv, note: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
      {modal === "transfer" && (
        <Modal title="Transferir entre ubicaciones" onClose={() => setModal(null)} foot={<><button className="btn btn--ghost" onClick={() => setModal(null)}>Cancelar</button><button className="btn btn--crimson" onClick={submit}>Transferir</button></>}>
          <Field label="Producto"><select className="select" value={tr.product_id} onChange={(e) => setTr({ ...tr, product_id: e.target.value })}><option value="">Seleccione…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}</select></Field>
          <div className="grid-3">
            <Field label="Desde"><select className="select" value={tr.from_id} onChange={(e) => setTr({ ...tr, from_id: e.target.value })}><option value="">…</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
            <Field label="Hacia"><select className="select" value={tr.to_id} onChange={(e) => setTr({ ...tr, to_id: e.target.value })}><option value="">…</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
            <Field label="Cantidad"><input className="input input--mono" value={tr.quantity} onChange={(e) => setTr({ ...tr, quantity: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
      {modal === "wh" && (
        <Modal title="Nueva ubicación" onClose={() => setModal(null)} foot={<><button className="btn btn--ghost" onClick={() => setModal(null)}>Cancelar</button><button className="btn btn--crimson" onClick={submit}>Crear</button></>}>
          <Field label="Nombre"><input className="input" value={wh.name} onChange={(e) => setWh({ ...wh, name: e.target.value })} /></Field>
          <Field label="Localización"><input className="input" value={wh.location} onChange={(e) => setWh({ ...wh, location: e.target.value })} /></Field>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={wh.is_default} onChange={(e) => setWh({ ...wh, is_default: e.target.checked })} />Predeterminada (las ventas descuentan de aquí)</label>
        </Modal>
      )}
    </>
  );
}
