/* Garantías (Andrés, 19/10): el equipo del cliente pasa primero por una revisión de Crimson y, si procede, se manda al
   distribuidor (tiene taller especializado); vuelve reparado, reemplazado o rechazado y se le entrega al cliente.
   Cada paso mueve el equipo entre bodegas que nunca se venden (revisión y distribuidor); la API concilia el kardex. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts } from "../../ui/Lookup";
import "../store/orders.css"; // mismas cajas de detalle y panel de acciones que los pedidos

type W = {
  id: number; number: string; status: "recibido" | "en_distribuidor" | "devuelto" | "cerrado" | "anulado"; status_label: string;
  customer_id: number; customer: string | null; ticket_id: number | null; ticket_number: string | null; asset_id: number | null; product_id: number | null;
  equipment: string; serial: string | null; problem: string | null;
  review: "procede" | "no_procede" | null; review_label: string | null; review_note: string | null;
  supplier_id: number | null; supplier: string | null; supplier_ref: string | null; sent_at: string | null; returned_at: string | null;
  result: string | null; result_label: string | null; new_serial: string | null;
  replacement: string | null; replacement_qty: string | number; replacement_serial: string | null;
  closed_at: string | null; closing_note: string | null; created_at: string; days_open: number | null;
  history?: { at: string; by_name: string | null; status: string; note: string }[];
};
type Asset = { id: number; name: string; serial: string | null; product_id: number | null };
type Supplier = { id: number; name: string };

const TONE: Record<W["status"], string> = { recibido: "warn", en_distribuidor: "info", devuelto: "ok", cerrado: "muted", anulado: "muted" };
const FILTERS: [string, string][] = [["abiertas", "Abiertas"], ["", "Todas"], ["cerrado", "Cerradas"]];
const when = (s: string | null) => (s ? fmtDate(s.slice(0, 10)) : "—");
const blank = { customer_id: "", customer_name: "", ticket_id: "", asset_id: "", product_id: "", product_name: "", equipment: "", serial: "", problem: "" };

export default function Warranties() {
  const { toast, allows } = useSession();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("abiertas");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<W[] | null>(null);
  const [open, setOpen] = useState<W | null>(null);
  const [form, setForm] = useState<typeof blank | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);

  const load = useCallback(() => {
    const qs = new URLSearchParams();
    if (filter) qs.set("status", filter);
    if (q.trim()) qs.set("q", q.trim());
    api<{ items: W[] }>(`/warranties?${qs}`).then((r) => setRows(r.items)).catch((e) => toastRef.current(e instanceof Error ? e.message : "Error", "bad"));
  }, [filter, q]);
  useEffect(() => { load(); }, [load]);

  // ?id= abre una garantía; ?ticket= abre el formulario con el cliente y el equipo del ticket
  useEffect(() => {
    const id = params.get("id");
    const ticket = params.get("ticket");
    if (id) api<W>(`/warranties/${id}`).then(setOpen).catch(() => {});
    if (ticket && allows("garantias.crear")) {
      api<{ id: number; customer_id: number | null; customer: string | null; asset_id: number | null; subject: string; description: string | null }>(`/tickets/${ticket}`)
        .then((t) => setForm({ ...blank, ticket_id: String(t.id), customer_id: t.customer_id ? String(t.customer_id) : "", customer_name: t.customer || "", asset_id: t.asset_id ? String(t.asset_id) : "", problem: [t.subject, t.description].filter(Boolean).join(" — ") }))
        .catch(() => {});
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const customerId = form?.customer_id;
  useEffect(() => {
    if (!customerId || !allows("assets.ver")) { setAssets([]); return; }
    api<Asset[]>(`/assets?customer_id=${customerId}`).then(setAssets).catch(() => setAssets([]));
  }, [customerId, allows]);

  const closeDetail = () => { setOpen(null); if (params.get("id") || params.get("ticket")) setParams(new URLSearchParams(), { replace: true }); };

  const create = async () => {
    if (!form) return;
    try {
      const w = await api<W>("/warranties", { method: "POST", json: {
        customer_id: Number(form.customer_id), ticket_id: form.ticket_id ? Number(form.ticket_id) : null, asset_id: form.asset_id ? Number(form.asset_id) : null,
        product_id: form.product_id ? Number(form.product_id) : null, equipment: form.equipment.trim() || null, serial: form.serial.trim() || null, problem: form.problem.trim() || null,
      } });
      toast(`Garantía ${w.number} abierta: el equipo pasa a revisión de Crimson`);
      setForm(null); setOpen(w); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const all = rows ?? [];
  const count = (s: W["status"]) => all.filter((w) => w.status === s).length;
  const viejas = all.filter((w) => (w.days_open ?? 0) > 30).length;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">13 · Soporte</div><h1 className="h1">Garantías</h1></div>
        <div className="page-head__actions">
          {allows("garantias.crear") && <button className="btn btn--crimson" onClick={() => setForm({ ...blank })}><Icon d={I.plus} />Recibir equipo</button>}
        </div>
      </div>

      <div className="eco">
        <div className="eco__box"><span className="meta">En revisión de Crimson</span><b>{count("recibido")}</b></div>
        <div className="eco__box"><span className="meta">En el distribuidor</span><b>{count("en_distribuidor")}</b></div>
        <div className="eco__box"><span className="meta">Listas para entregar</span><b>{count("devuelto")}</b></div>
        <div className={`eco__box ${viejas ? "is-bad" : ""}`}><span className="meta">Más de 30 días</span><b>{viejas}</b></div>
      </div>

      <Card flush>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: "12px 16px" }}>
          <div className="tabs" role="tablist">{FILTERS.map(([k, l]) => <button key={k} role="tab" aria-selected={filter === k} className={`tab ${filter === k ? "is-active" : ""}`} onClick={() => setFilter(k)}>{l}</button>)}</div>
          <input className="input" style={{ maxWidth: 260, marginLeft: "auto" }} placeholder="Número, equipo, serie o boleta…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {rows === null ? <Loading /> : all.length === 0 ? <Empty title="Sin garantías" hint="Cuando un cliente trae un equipo dañado, recibilo acá: queda en revisión de Crimson antes de mandarlo al distribuidor." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>Número</th><th>Cliente</th><th>Equipo</th><th>Estado</th><th>Distribuidor</th><th className="num">Días</th></tr></thead>
              <tbody>{all.map((w) => (
                <tr key={w.id} className="row-link" style={{ cursor: "pointer" }} onClick={() => api<W>(`/warranties/${w.id}`).then(setOpen).catch((e) => toast(e.message, "bad"))}>
                  <td className="mono">{w.number}{w.ticket_number && <div className="meta">{w.ticket_number}</div>}</td>
                  <td>{w.customer || "—"}</td>
                  <td><b>{w.equipment}</b>{w.serial && <div className="meta mono">S/N {w.serial}</div>}</td>
                  <td><span className={`badge badge--${TONE[w.status]}`}>{w.status_label}</span>{w.review && w.status === "recibido" && <div className="meta">Revisión: {w.review_label}</div>}</td>
                  <td className="muted">{w.supplier || "—"}{w.supplier_ref && <div className="meta mono">{w.supplier_ref}</div>}</td>
                  <td className="num">{w.days_open == null ? "—" : <span className={w.days_open > 30 ? "badge badge--bad" : ""}>{w.days_open}</span>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>

      {form && (
        <Modal title="Recibir equipo en garantía" onClose={() => setForm(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setForm(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={create} disabled={!form.customer_id || !(form.asset_id || form.product_id || form.equipment.trim())}>Recibir</button>
        </>}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>El equipo entra a la bodega de revisión de Crimson (no se vende). Después de la revisión se decide si va al distribuidor.</p>
          <div className="grid-3">
            <Field label="Cliente"><Lookup value={form.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setForm({ ...form, customer_id: it ? String(it.id) : "", customer_name: text, asset_id: "" })} /></Field>
            <Field label="Equipo instalado del cliente" hint={assets.length ? "Opcional: trae serie y producto." : "El cliente no tiene equipos registrados."}>
              <select className="select" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })} disabled={!assets.length}>
                <option value="">—</option>
                {assets.map((a) => <option key={a.id} value={a.id}>{a.name}{a.serial ? ` · ${a.serial}` : ""}</option>)}
              </select>
            </Field>
            <Field label="Producto del catálogo" hint="Para seguirlo en bodega."><Lookup value={form.product_name} placeholder="Buscar producto…" fetcher={searchProducts} onSelect={(it, text) => setForm({ ...form, product_id: it ? String(it.id) : "", product_name: text })} /></Field>
            <Field label="Equipo (como lo describe el cliente)"><input className="input" value={form.equipment} onChange={(e) => setForm({ ...form, equipment: e.target.value })} placeholder="Cámara domo 4MP del portón" maxLength={200} /></Field>
            <Field label="Serie"><input className="input input--mono" style={{ textAlign: "left" }} value={form.serial} onChange={(e) => setForm({ ...form, serial: e.target.value })} maxLength={80} /></Field>
          </div>
          <Field label="Qué falla"><textarea className="textarea" value={form.problem} onChange={(e) => setForm({ ...form, problem: e.target.value })} maxLength={4000} /></Field>
        </Modal>
      )}

      {open && <Detail w={open} onClose={closeDetail} onChange={(w) => { setOpen(w); load(); }} />}
    </>
  );
}

function Detail({ w, onClose, onChange }: { w: W; onClose: () => void; onChange: (w: W) => void }) {
  const { toast, allows } = useSession();
  const [act, setAct] = useState<string | null>(null);
  const [f, setF] = useState({ review: "procede", result: "reparado", supplier_id: "", supplier_ref: "", new_serial: "", product_id: "", product_name: "", serial: "", note: "" });
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [busy, setBusy] = useState(false);
  const edit = allows("garantias.editar");
  const abierta = w.status === "recibido" || w.status === "en_distribuidor" || w.status === "devuelto";

  useEffect(() => { if (act === "enviar" && !suppliers.length) api<Supplier[]>("/suppliers").then(setSuppliers).catch(() => {}); }, [act, suppliers.length]);

  const run = async () => {
    if (!act) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = { action: act, note: f.note.trim() || null };
      if (act === "revisar") body.review = f.review;
      if (act === "enviar") Object.assign(body, { supplier_id: f.supplier_id ? Number(f.supplier_id) : null, supplier_ref: f.supplier_ref.trim() || null });
      if (act === "recibir") Object.assign(body, { result: f.result, new_serial: f.new_serial.trim() || null });
      if (act === "reemplazar") Object.assign(body, { product_id: f.product_id ? Number(f.product_id) : null, serial: f.serial.trim() || null });
      const r = await api<W>(`/warranties/${w.id}/action`, { method: "POST", json: body });
      toast(`${r.number}: ${r.status_label}`);
      setAct(null); setF({ ...f, note: "" }); onChange(r);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(false); }
  };

  const ACTIONS: [string, string, boolean][] = [
    ["revisar", w.review ? "Cambiar la revisión" : "Revisión de Crimson", w.status === "recibido"],
    ["enviar", "Mandar al distribuidor", w.status === "recibido" && w.review === "procede"],
    ["recibir", "Volvió del distribuidor", w.status === "en_distribuidor"],
    ["cerrar", "Entregar al cliente y cerrar", w.status === "devuelto" || (w.status === "recibido" && w.review === "no_procede")],
    ["reemplazar", "Darle un reemplazo de bodega", abierta && !Number(w.replacement_qty)],
    ["anular", "Anular", abierta],
  ];

  return (
    <Modal title={<>Garantía {w.number} <span className={`badge badge--${TONE[w.status]}`} style={{ verticalAlign: "middle", marginLeft: 6 }}>{w.status_label}</span></>} onClose={onClose} wide foot={<button className="btn btn--ghost" onClick={onClose}>Cerrar</button>}>
      <div className="wo-detail">
        <div className="wo-grid">
          <div className="wo-box"><span className="meta">Cliente</span><b>{w.customer || "—"}</b>{w.ticket_number && <span>Ticket {w.ticket_number}</span>}<span>Recibido {when(w.created_at)}</span></div>
          <div className="wo-box"><span className="meta">Equipo</span><b>{w.equipment}</b>{w.serial && <span className="mono">S/N {w.serial}</span>}{w.new_serial && <span className="mono">Serie nueva {w.new_serial}</span>}</div>
          <div className="wo-box"><span className="meta">Revisión de Crimson</span><b>{w.review_label || "Pendiente"}</b>{w.review_note && <span>{w.review_note}</span>}</div>
          <div className="wo-box"><span className="meta">Distribuidor</span><b>{w.supplier || (w.sent_at ? "Enviado" : "—")}</b>{w.supplier_ref && <span className="mono">Boleta {w.supplier_ref}</span>}{w.sent_at && <span>Enviado {when(w.sent_at)}</span>}{w.result_label && <span>{w.result_label} · {when(w.returned_at)}</span>}</div>
        </div>
        {w.problem && <div className="wo-box"><span className="meta">Qué falla</span>{w.problem}</div>}
        {Number(w.replacement_qty) > 0 && <div className="wo-box"><span className="meta">Reemplazo de Crimson</span>{w.replacement}{w.replacement_serial ? ` · S/N ${w.replacement_serial}` : ""} (salió de la bodega de venta; lo que vuelva del distribuidor entra a la venta)</div>}

        {edit && abierta && (
          <div className="wo-act">
            <div className="wo-act__row">{ACTIONS.filter(([, , ok]) => ok).map(([k, l]) => (
              <button key={k} className={`btn btn--sm ${act === k ? "btn--crimson" : k === "anular" ? "btn--ghost" : "btn--soft"}`} onClick={() => setAct(act === k ? null : k)}>{l}</button>
            ))}</div>
            {act && (
              <>
                <div className="wo-act__row">
                  {act === "revisar" && <Field label="¿Procede la garantía?"><select className="select" value={f.review} onChange={(e) => setF({ ...f, review: e.target.value })}><option value="procede">Sí: se manda al distribuidor</option><option value="no_procede">No: se le devuelve al cliente</option></select></Field>}
                  {act === "enviar" && <><Field label="Distribuidor"><select className="select" value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field><Field label="Boleta o RMA del distribuidor"><input className="input input--mono" style={{ textAlign: "left" }} value={f.supplier_ref} onChange={(e) => setF({ ...f, supplier_ref: e.target.value })} maxLength={80} /></Field></>}
                  {act === "recibir" && <><Field label="Qué hizo el distribuidor"><select className="select" value={f.result} onChange={(e) => setF({ ...f, result: e.target.value })}><option value="reparado">Lo reparó</option><option value="reemplazado">Lo cambió por otro</option><option value="rechazado">Rechazó la garantía</option></select></Field>{f.result === "reemplazado" && <Field label="Serie del equipo nuevo"><input className="input input--mono" style={{ textAlign: "left" }} value={f.new_serial} onChange={(e) => setF({ ...f, new_serial: e.target.value })} maxLength={80} /></Field>}</>}
                  {act === "reemplazar" && <><Field label="Producto que se le da"><Lookup value={f.product_name} placeholder="Buscar producto…" fetcher={searchProducts} onSelect={(it, text) => setF({ ...f, product_id: it ? String(it.id) : "", product_name: text })} /></Field><Field label="Serie"><input className="input input--mono" style={{ textAlign: "left" }} value={f.serial} onChange={(e) => setF({ ...f, serial: e.target.value })} maxLength={80} /></Field></>}
                  <Field label={act === "anular" ? "Motivo" : "Nota (opcional)"}><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} maxLength={1000} /></Field>
                </div>
                <div className="wo-act__row" style={{ justifyContent: "flex-end" }}>
                  <button className="btn btn--crimson btn--sm" onClick={run} disabled={busy || (act === "anular" && !f.note.trim()) || (act === "reemplazar" && !f.product_id)}><Icon d={I.check} />{busy ? "Guardando…" : "Aplicar"}</button>
                </div>
              </>
            )}
          </div>
        )}

        {(w.history?.length ?? 0) > 0 && (
          <div className="wo-box"><span className="meta">Historial</span>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>{[...(w.history || [])].reverse().map((h, i) => <li key={i}>{new Date(h.at).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })} · {h.by_name || "Sistema"} · {h.note}</li>)}</ul>
          </div>
        )}
      </div>
    </Modal>
  );
}
