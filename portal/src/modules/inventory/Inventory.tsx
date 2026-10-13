/* Inventarios por ubicacion: existencias (calculadas del ledger), movimientos, transferencias y alertas de stock minimo.
   Reservas formales (AC25: ver existencia no es retirar) y unidades con numero de serie. */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { Dropzone } from "../../ui/MediaPicker";
import AuthLink from "../../ui/AuthLink";
import WebInventory from "./WebInventory";

type Wh = { id: number; name: string; location: string | null; is_default: boolean; active: boolean };
type Level = { product_id: number; warehouse_id: number; quantity: string; code: string; name: string; min_stock: number; warehouse: string; committed?: string; available?: string; serialized?: boolean };
type Low = { product_id: number; code: string; name: string; quantity: string; min_stock: number };
type ProjectLite = { id: number; number: string; name: string };
export type Reservation = {
  id: number; product_id: number; code: string | null; name: string | null; warehouse_id: number; warehouse: string | null; quantity: string; consumed_quantity: string;
  project_id: number | null; project: string | null; project_name: string | null; status: string; expires_at: string | null; expired: boolean;
  requested_by: string | null; approved_by: string | null; approved_at: string | null; reason: string | null; closed_note: string | null; created_at: string; note?: string | null;
};
type Unit = {
  id: number; product_id: number; code: string | null; name: string | null; serial: string; warehouse_id: number | null; warehouse: string | null; status: string;
  project_id: number | null; project: string | null; condition: string; location: string | null; customer_asset_id: number | null; notes: string | null; received_at: string | null;
};

export const RES_STATUS: Record<string, { label: string; tone: string }> = {
  pendiente: { label: "Por aprobar", tone: "warn" }, confirmada: { label: "Confirmada", tone: "ok" }, cancelada: { label: "Cancelada", tone: "muted" },
  vencida: { label: "Vencida", tone: "bad" }, consumida: { label: "Consumida", tone: "info" },
};
const UNIT_STATUS: Record<string, string> = {
  disponible: "Disponible", reservado: "Reservado", solicitado: "Solicitado", en_transito: "En tránsito", entregado_proyecto: "Entregado a proyecto",
  instalado: "Instalado", devuelto: "Devuelto", averiado: "Averiado", garantia: "En garantía",
};
const UNIT_TONE: Record<string, string> = { disponible: "ok", reservado: "warn", instalado: "info", averiado: "bad", garantia: "bad" };
const MOVE_TYPES: [string, string][] = [
  ["compra", "Compra"], ["venta", "Venta"], ["consumo_proyecto", "Consumo de proyecto"], ["prestamo", "Préstamo"], ["consignacion", "Consignación"],
  ["asignacion_interna", "Asignación interna"], ["devolucion", "Devolución"], ["ajuste", "Ajuste"], ["traslado", "Traslado"],
];
const n = (v: string | number | null | undefined) => Number(v ?? 0);
const isoLocal = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

export default function Inventory() {
  const { toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "existencias";
  const [whs, setWhs] = useState<Wh[]>([]);
  const [levelsSt, setLevels] = useState<Level[] | null>(null); // null = cargando
  const levels = levelsSt ?? [];
  const [lowSt, setLow] = useState<Low[] | null>(null); // null = cargando
  const low = lowSt ?? [];
  const [products, setProducts] = useState<Product[]>([]);
  const [projects, setProjects] = useState<ProjectLite[]>([]);
  const [filter, setFilter] = useState<number | "">("");
  const [modal, setModal] = useState<"move" | "transfer" | "wh" | null>(null);
  const emptyMv = { product_id: "", warehouse_id: "", kind: "entrada", quantity: "1", reference: "", note: "", movement_type: "", project_id: "", serial: "", location: "", custodian: "", owner: "", voucher_media: "" };
  const [mv, setMv] = useState(emptyMv);
  const [mvMore, setMvMore] = useState(false);
  const [tr, setTr] = useState({ product_id: "", from_id: "", to_id: "", quantity: "1" });
  const [wh, setWh] = useState({ name: "", location: "", is_default: false });
  // ?stock=bajo llega desde "Productos con stock bajo" del inicio: la tabla muestra solo esos productos
  const onlyLow = params.get("stock") === "bajo";
  const lowIds = new Set(low.map((l) => l.product_id));
  const shown = onlyLow ? levels.filter((l) => lowIds.has(l.product_id)) : levels;

  const load = () => {
    api<Wh[]>("/warehouses").then(setWhs);
    api<{ levels: Level[]; low: Low[] }>(`/stock${filter ? `?warehouse_id=${filter}` : ""}`).then((r) => { setLevels(r.levels); setLow(r.low); });
    api<{ items: Product[] }>("/products?limit=100").then((r) => setProducts(r.items.filter((p) => p.item_type === "producto")));
  };
  useEffect(load, [filter]);
  // proyectos para ligar reservas y movimientos (bodega puede no tener acceso a proyectos: queda vacio)
  useEffect(() => { if (allows("projects.ver")) api<ProjectLite[]>("/projects?status=activos").then(setProjects).catch(() => setProjects([])); }, [allows]);

  const submit = async () => {
    try {
      if (modal === "move") {
        const extra: Record<string, unknown> = {};
        for (const k of ["movement_type", "serial", "location", "custodian", "owner", "voucher_media"] as const) if (mv[k].trim()) extra[k] = mv[k].trim();
        if (mv.project_id) extra.project_id = Number(mv.project_id);
        await api("/stock/movements", { method: "POST", json: { product_id: Number(mv.product_id), warehouse_id: Number(mv.warehouse_id), kind: mv.kind, quantity: mv.quantity, reference: mv.reference || null, note: mv.note || null, ...extra } });
      }
      if (modal === "transfer") await api("/stock/transfer", { method: "POST", json: { ...tr, product_id: Number(tr.product_id), from_id: Number(tr.from_id), to_id: Number(tr.to_id) } });
      if (modal === "wh") await api("/warehouses", { method: "POST", json: wh });
      toast("Guardado"); setModal(null); setMv(emptyMv); setMvMore(false); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const total = levels.reduce((a, l) => a + Number(l.quantity), 0);
  const comprometido = levels.reduce((a, l) => a + n(l.committed), 0);
  const setTab = (k: string) => { const p = new URLSearchParams(params); if (k === "existencias") p.delete("tab"); else p.set("tab", k); setParams(p); };

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

      <div className="tabs" style={{ alignSelf: "flex-start" }} role="tablist">
        {[["existencias", "Existencias"], ["reservas", "Reservas"], ["seriales", "Seriales"], ["web", "Inventario web"]].map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "is-active" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>

      {tab === "existencias" && <>
        <div className="pending">
          {whs.map((w) => <div key={w.id} className={filter === w.id ? "hot" : ""} onClick={() => setFilter(filter === w.id ? "" : w.id)} style={{ cursor: "pointer" }}><b>{levels.filter((l) => l.warehouse_id === w.id).reduce((a, l) => a + Number(l.quantity), 0)}</b><span>{w.name}{w.is_default ? " · predeterminada" : ""}{w.location ? ` · ${w.location}` : ""}</span></div>)}
          <div><b>{total}</b><span>Unidades {filter ? "en la ubicación" : "totales"}</span></div>
          <div><b>{comprometido}</b><span>Comprometidas en reservas</span></div>
          <div className={low.length ? "hot" : ""}><b>{low.length}</b><span>Productos bajo mínimo</span></div>
        </div>

        {onlyLow && (
          <div className="list-filter card" role="status" style={{ marginBottom: 12 }}>
            <span>Desde el inicio: <b>Productos con stock bajo</b> · {low.length} {low.length === 1 ? "producto" : "productos"} en o bajo su mínimo</span>
            <Link className="btn btn--ghost btn--sm" to="/inventario">Quitar filtro</Link>
          </div>
        )}
        {/* existencias a lo ancho: con comprometido y disponible no caben en media pantalla */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Card title={onlyLow ? "Existencias de los productos bajo mínimo" : "Existencias por ubicación"} flush extra={allows("reports.exportar") ? <AuthLink path="/reports/inventario?format=xlsx" download="inventario.xlsx"><Icon d={I.reports} />Excel</AuthLink> : undefined}>
            {levelsSt === null ? <Loading /> : shown.length === 0 ? <Empty hint={onlyLow ? "Ningún producto bajo mínimo tiene existencias registradas en bodega." : "Registrá una entrada para empezar; las ventas descuentan solas de la ubicación predeterminada."} /> : (
              <table className="table"><thead><tr><th>Producto</th><th>Ubicación</th><th className="num">Cantidad</th><th className="num" title="Reservas confirmadas y vigentes">Comprometido</th><th className="num">Disponible</th><th className="num">Mínimo</th></tr></thead>
                <tbody>{shown.map((l, i) => <tr key={i}><td><b>{l.name}</b>{l.serialized && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Serie</span>}<div className="meta mono">{l.code}</div></td><td className="muted">{l.warehouse}</td><td className="num mono" style={{ color: Number(l.quantity) <= l.min_stock ? "var(--bad)" : undefined, fontWeight: 700 }}>{Number(l.quantity)}</td><td className="num mono" style={{ color: n(l.committed) > 0 ? "var(--warn)" : "var(--text-3)" }}>{n(l.committed) || "—"}</td><td className="num mono" style={{ color: n(l.available ?? l.quantity) < 0 ? "var(--bad)" : undefined }}>{n(l.available ?? l.quantity)}</td><td className="num mono muted">{l.min_stock}</td></tr>)}</tbody></table>
            )}
          </Card>
          <Card title="Alertas de stock mínimo" flush>
            {lowSt === null ? <Loading /> : low.length === 0 ? <Empty title="Todo en nivel" hint="Ningún producto está por debajo de su mínimo." /> : (
              <table className="table"><thead><tr><th>Producto</th><th className="num">Total</th><th className="num">Mínimo</th></tr></thead>
                <tbody>{low.map((l) => <tr key={l.product_id}><td><b>{l.name}</b><div className="meta">{l.code}</div></td><td className="num mono" style={{ color: "var(--bad)", fontWeight: 700 }}>{Number(l.quantity)}</td><td className="num mono muted">{l.min_stock}</td></tr>)}</tbody></table>
            )}
          </Card>
        </div>
      </>}

      {tab === "reservas" && <Reservations products={products} whs={whs} projects={projects} onChange={load} />}
      {tab === "seriales" && <Units products={products} whs={whs} projects={projects} onChange={load} />}
      {tab === "web" && <WebInventory />}

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
          <button type="button" className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} aria-expanded={mvMore} onClick={() => setMvMore(!mvMore)}>{mvMore ? "Menos datos" : "Más datos (opcional)"}</button>
          {mvMore && (
            <div className="grid-2">
              <Field label="Motivo del movimiento"><select className="select" value={mv.movement_type} onChange={(e) => setMv({ ...mv, movement_type: e.target.value })}><option value="">Sin especificar</option>{MOVE_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
              <Field label="Proyecto"><select className="select" value={mv.project_id} onChange={(e) => setMv({ ...mv, project_id: e.target.value })}><option value="">Ninguno</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.number} · {p.name}</option>)}</select></Field>
              <Field label="Número de serie"><input className="input input--mono" style={{ textAlign: "left" }} value={mv.serial} onChange={(e) => setMv({ ...mv, serial: e.target.value })} /></Field>
              <Field label="Sub-ubicación" hint="Estante, rack, vehículo…"><input className="input" value={mv.location} onChange={(e) => setMv({ ...mv, location: e.target.value })} /></Field>
              <Field label="Custodio" hint="Vacío: la empresa."><input className="input" value={mv.custodian} onChange={(e) => setMv({ ...mv, custodian: e.target.value })} /></Field>
              <Field label="Propietario" hint="Vacío: la empresa."><input className="input" value={mv.owner} onChange={(e) => setMv({ ...mv, owner: e.target.value })} /></Field>
              <div style={{ gridColumn: "1 / -1" }}>
                <Field label="Comprobante">{mv.voucher_media ? <div style={{ display: "flex", gap: 8, alignItems: "center" }}><span className="mono muted" style={{ overflowWrap: "anywhere", fontSize: 12 }}>{mv.voucher_media.split("/").pop()}</span><button type="button" className="btn btn--ghost btn--sm" onClick={() => setMv({ ...mv, voucher_media: "" })}>Quitar</button></div> : <Dropzone compact accept="image/*,application/pdf" label="Adjuntar foto o PDF" onDone={(m) => setMv({ ...mv, voucher_media: m.url })} />}</Field>
              </div>
            </div>
          )}
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

/* ---------- Reservas: pedir, aprobar, cancelar, vencimiento ---------- */
export function ReservationRow({ r, canApprove, onAct, showProject = true }: { r: Reservation; canApprove: boolean; onAct: (r: Reservation, a: "confirm" | "cancel") => void; showProject?: boolean }) {
  const st = RES_STATUS[r.status] || { label: r.status, tone: "muted" };
  const abierta = r.status === "pendiente" || r.status === "confirmada";
  return (
    <tr>
      <td style={{ minWidth: 150 }}><b>{r.name}</b><div className="meta">{r.code} · {r.warehouse}</div></td>
      <td className="num mono" style={{ fontWeight: 700 }}>{n(r.quantity)}{n(r.consumed_quantity) > 0 && <div className="meta">usadas {n(r.consumed_quantity)}</div>}</td>
      {showProject && <td className="muted" style={{ minWidth: 150 }}>{r.project ? <Link to={`/proyectos/${r.project_id}`} style={{ whiteSpace: "nowrap" }}>{r.project}</Link> : "—"}{r.reason && <div style={{ fontSize: 12, maxWidth: 240 }}>{r.reason}</div>}</td>}
      <td><span className={`badge badge--${st.tone}`}>{st.label}</span>{r.approved_by && <div className="meta">{r.approved_by}</div>}{r.closed_note && !abierta && <div className="meta" style={{ maxWidth: 220 }}>{r.closed_note}</div>}</td>
      <td className="muted" style={{ color: r.expired ? "var(--bad)" : undefined, whiteSpace: "nowrap" }}>{fmtDate(r.expires_at?.slice(0, 10))}</td>
      <td className="num" style={{ whiteSpace: "nowrap" }}>
        {r.status === "pendiente" && canApprove && <button className="btn btn--soft btn--sm" onClick={() => onAct(r, "confirm")}><Icon d={I.check} size={14} />Aprobar</button>}
        {abierta && canApprove && <button className="btn btn--ghost btn--sm" onClick={() => onAct(r, "cancel")}>Cancelar</button>}
      </td>
    </tr>
  );
}

function Reservations({ products, whs, projects, onChange }: { products: Product[]; whs: Wh[]; projects: ProjectLite[]; onChange: () => void }) {
  const { toast, allows } = useSession();
  const [status, setStatus] = useState("abiertas");
  const [rowsSt, setRows] = useState<Reservation[] | null>(null);
  const [canApprove, setCanApprove] = useState(false);
  const [form, setForm] = useState<{ product_id: string; warehouse_id: string; quantity: string; project_id: string; reason: string; expires: string } | null>(null);
  const canRequest = allows("inventory.crear|projects.editar");

  const load = () => {
    setRows(null);
    api<{ items: Reservation[]; can_approve: boolean }>(`/stock/reservations${status ? `?status=${status}` : ""}`).then((r) => { setRows(r.items); setCanApprove(r.can_approve); }).catch(() => setRows([]));
  };
  useEffect(load, [status]);

  const act = async (r: Reservation, a: "confirm" | "cancel") => {
    try {
      await api(`/stock/reservations/${r.id}/${a}`, { method: "POST", json: {} });
      toast(a === "confirm" ? "Reserva aprobada: la existencia queda comprometida" : "Reserva cancelada"); load(); onChange();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); load(); }
  };
  const create = async () => {
    if (!form) return;
    try {
      const r = await api<Reservation>("/stock/reservations", { method: "POST", json: {
        product_id: Number(form.product_id), quantity: form.quantity, warehouse_id: form.warehouse_id ? Number(form.warehouse_id) : null,
        project_id: form.project_id ? Number(form.project_id) : null, reason: form.reason || null, expires_at: form.expires ? `${form.expires}T23:59:00` : null,
      } });
      toast(r.note || (r.status === "confirmada" ? "Reserva confirmada por la regla automática" : "Reserva enviada: queda por aprobar"), r.note ? "bad" : undefined);
      setForm(null); load(); onChange();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const rows = rowsSt ?? [];
  const porAprobar = rows.filter((r) => r.status === "pendiente").length;

  return (
    <>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
        <span className="muted" style={{ fontSize: 13, flex: "1 1 320px", maxWidth: 640 }}>Ver existencia no es retirar: una reserva compromete material solo cuando la aprueba quien maneja inventario. Las vencidas o canceladas lo liberan.</span>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", flex: "0 0 auto" }}>
          <select className="select" aria-label="Filtrar reservas" value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "auto", maxWidth: 190 }}>
            <option value="abiertas">Abiertas</option><option value="pendiente">Por aprobar</option><option value="confirmada">Confirmadas</option>
            <option value="vencida">Vencidas</option><option value="consumida">Consumidas</option><option value="cancelada">Canceladas</option><option value="">Todas</option>
          </select>
          {canRequest && <button className="btn btn--crimson btn--sm" onClick={() => setForm({ product_id: "", warehouse_id: "", quantity: "1", project_id: "", reason: "", expires: isoLocal(new Date(Date.now() + 15 * 864e5)) })}><Icon d={I.plus} />Nueva reserva</button>}
        </div>
      </div>
      <Card title={`Reservas${porAprobar ? ` · ${porAprobar} por aprobar` : ""}`} flush>
        {rowsSt === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin reservas" hint="Desde un proyecto, «Reservar materiales» pide lo planificado; o creá una reserva aquí." /> : (
          <table className="table">
            <thead><tr><th>Material</th><th className="num">Cantidad</th><th>Proyecto</th><th>Estado</th><th>Vence</th><th /></tr></thead>
            <tbody>{rows.map((r) => <ReservationRow key={r.id} r={r} canApprove={canApprove} onAct={act} />)}</tbody>
          </table>
        )}
      </Card>
      {form && (
        <Modal title="Nueva reserva de material" onClose={() => setForm(null)} foot={<><button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button><button className="btn btn--crimson" disabled={!form.product_id} onClick={create}>Pedir reserva</button></>}>
          <div className="grid-2">
            <Field label="Producto"><select className="select" value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })}><option value="">Seleccione…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}</select></Field>
            <Field label="Bodega" hint="Vacío: la del producto o la predeterminada."><select className="select" value={form.warehouse_id} onChange={(e) => setForm({ ...form, warehouse_id: e.target.value })}><option value="">Predeterminada</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
            <Field label="Cantidad"><input className="input input--mono" inputMode="decimal" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></Field>
            <Field label="Vence"><input className="input" type="date" value={form.expires} onChange={(e) => setForm({ ...form, expires: e.target.value })} /></Field>
            <Field label="Proyecto"><select className="select" value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}><option value="">Sin proyecto</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.number} · {p.name}</option>)}</select></Field>
            <Field label="Motivo"><input className="input" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </>
  );
}

/* ---------- Unidades serializadas: alta al recibir y estado de cada serie ---------- */
function Units({ products, whs, projects, onChange }: { products: Product[]; whs: Wh[]; projects: ProjectLite[]; onChange: () => void }) {
  const { toast, allows } = useSession();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [rowsSt, setRows] = useState<Unit[] | null>(null);
  const [alta, setAlta] = useState<{ product_id: string; warehouse_id: string; serials: string; condition: string; location: string; register_entry: boolean } | null>(null);
  const [edit, setEdit] = useState<{ u: Unit; status: string; project_id: string; location: string; asset_location: string } | null>(null);

  const load = () => {
    const qs = new URLSearchParams();
    if (q.trim()) qs.set("q", q.trim());
    if (status) qs.set("status", status);
    api<Unit[]>(`/stock/units${qs.toString() ? `?${qs}` : ""}`).then(setRows).catch(() => setRows([]));
  };
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [q, status]); // eslint-disable-line react-hooks/exhaustive-deps

  const series = (alta?.serials || "").split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
  const saveAlta = async () => {
    if (!alta) return;
    try {
      const r = await api<Unit[]>("/stock/units", { method: "POST", json: { product_id: Number(alta.product_id), warehouse_id: alta.warehouse_id ? Number(alta.warehouse_id) : null, serials: series, condition: alta.condition, location: alta.location || null, register_entry: alta.register_entry } });
      toast(`${r.length} serie${r.length !== 1 ? "s" : ""} dada${r.length !== 1 ? "s" : ""} de alta`); setAlta(null); load(); onChange();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const saveEdit = async () => {
    if (!edit) return;
    try {
      await api(`/stock/units/${edit.u.id}`, { method: "PATCH", json: { status: edit.status, project_id: edit.project_id ? Number(edit.project_id) : null, location: edit.location || null, asset_location: edit.asset_location || null } });
      toast(edit.status === "instalado" ? "Serie instalada y registrada en los equipos del cliente" : "Serie actualizada"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const rows = rowsSt ?? [];

  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input className="input" style={{ flex: "1 1 180px", maxWidth: 280 }} placeholder="Buscar serie…" aria-label="Buscar serie" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" style={{ width: "auto", maxWidth: 200 }} aria-label="Filtrar por estado" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Todos los estados</option>{Object.entries(UNIT_STATUS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        {allows("inventory.crear") && <button className="btn btn--crimson btn--sm" style={{ marginLeft: "auto" }} onClick={() => setAlta({ product_id: "", warehouse_id: "", serials: "", condition: "nuevo", location: "", register_entry: false })}><Icon d={I.plus} />Dar de alta seriales</button>}
      </div>
      <Card title="Unidades con número de serie" flush>
        {rowsSt === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin series registradas" hint="Al recibir equipos con número de serie (cámaras, NVR, switches), dalos de alta aquí para saber dónde está cada uno." /> : (
          <table className="table">
            <thead><tr><th>Serie</th><th>Producto</th><th>Bodega</th><th>Estado</th><th>Proyecto</th><th /></tr></thead>
            <tbody>{rows.map((u) => (
              <tr key={u.id}>
                <td className="mono" style={{ fontWeight: 600 }}>{u.serial}</td>
                <td>{u.name}<div className="meta">{u.code}{u.condition !== "nuevo" ? ` · ${u.condition}` : ""}</div></td>
                <td className="muted">{u.warehouse || "—"}{u.location && <div className="meta">{u.location}</div>}</td>
                <td><span className={`badge badge--${UNIT_TONE[u.status] || "muted"}`}>{UNIT_STATUS[u.status] || u.status}</span></td>
                <td className="muted">{u.project ? <Link to={`/proyectos/${u.project_id}`}>{u.project}</Link> : "—"}</td>
                <td className="num">{allows("inventory.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ u, status: u.status, project_id: u.project_id ? String(u.project_id) : "", location: u.location || "", asset_location: "" })}>Cambiar</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {alta && (
        <Modal title="Alta de seriales" onClose={() => setAlta(null)} foot={<><button className="btn btn--ghost" onClick={() => setAlta(null)}>Cancelar</button><button className="btn btn--crimson" disabled={!alta.product_id || series.length === 0} onClick={saveAlta}>Dar de alta {series.length || ""}</button></>}>
          <div className="grid-2">
            <Field label="Producto"><select className="select" value={alta.product_id} onChange={(e) => setAlta({ ...alta, product_id: e.target.value })}><option value="">Seleccione…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}</select></Field>
            <Field label="Bodega"><select className="select" value={alta.warehouse_id} onChange={(e) => setAlta({ ...alta, warehouse_id: e.target.value })}><option value="">Predeterminada</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
            <Field label="Condición"><select className="select" value={alta.condition} onChange={(e) => setAlta({ ...alta, condition: e.target.value })}><option value="nuevo">Nuevo</option><option value="usado">Usado</option><option value="reacondicionado">Reacondicionado</option><option value="danado">Dañado</option></select></Field>
            <Field label="Sub-ubicación" hint="Estante, rack…"><input className="input" value={alta.location} onChange={(e) => setAlta({ ...alta, location: e.target.value })} /></Field>
          </div>
          <Field label={`Números de serie${series.length ? ` · ${series.length}` : ""}`} hint="Uno por línea (sirve un lector de código de barras) o separados por coma."><textarea className="textarea input--mono" style={{ minHeight: 120 }} value={alta.serials} onChange={(e) => setAlta({ ...alta, serials: e.target.value })} /></Field>
          <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}><input type="checkbox" checked={alta.register_entry} onChange={(e) => setAlta({ ...alta, register_entry: e.target.checked })} style={{ marginTop: 3 }} /><span>Sumar también estas unidades a la existencia<br /><small className="muted">Dejalo sin marcar si la compra ya entró a bodega (por ejemplo, al recibir la solicitud de compra).</small></span></label>
        </Modal>
      )}
      {edit && (
        <Modal title={`Serie ${edit.u.serial}`} onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" disabled={edit.status === "instalado" && !edit.project_id} onClick={saveEdit}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Estado"><select className="select" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}>{Object.entries(UNIT_STATUS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Proyecto" hint={edit.status === "instalado" ? "Obligatorio para instalar." : undefined}><select className="select" value={edit.project_id} onChange={(e) => setEdit({ ...edit, project_id: e.target.value })}><option value="">Ninguno</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.number} · {p.name}</option>)}</select></Field>
            <Field label="Sub-ubicación en bodega"><input className="input" value={edit.location} onChange={(e) => setEdit({ ...edit, location: e.target.value })} /></Field>
            {edit.status === "instalado" && !edit.u.customer_asset_id && <Field label="Dónde quedó instalada" hint="Pasa a los equipos del cliente."><input className="input" value={edit.asset_location} onChange={(e) => setEdit({ ...edit, asset_location: e.target.value })} /></Field>}
          </div>
        </Modal>
      )}
    </>
  );
}
