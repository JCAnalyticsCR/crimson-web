/* Ajustes -> Bitacora (solo administracion): quien hizo que, cuando, desde que IP y que cambio.
   Cada fila abre el detalle con antes/despues campo por campo (y por punto, en los levantamientos).
   Es de solo lectura: en el servidor la tabla no se edita ni se borra. */
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../app/session";
import { Card, Empty, Loading, Modal } from "../ui/components";

type Row = {
  id: number; at: string; user_id: number | null; user: string | null; ip: string | null; action: string; entity: string; entity_id: number | null;
  diff: Record<string, unknown> | null; before: Record<string, unknown> | null; after: Record<string, unknown> | null;
  reason: string | null; version: string | null; company: string | null;
};
type Facets = { entities: string[]; actions: string[]; users: { id: number; name: string; email: string }[] };

const ENTIDAD: Record<string, string> = {
  survey: "Levantamiento", user: "Usuario", invitation: "Invitación", expense: "Gasto", project: "Proyecto", commission_rule: "Regla de comisión",
  report: "Reporte", media: "Archivo", quote: "Cotización", invoice: "Factura", payment: "Pago", customer: "Cliente", work_order: "Orden de trabajo",
  opportunity: "Oportunidad", support_ticket: "Ticket", product: "Producto",
};
const ACCION: Record<string, string> = {
  create: "Creó", update: "Editó", permissions: "Permisos / estado", export: "Exportó", upload: "Subió", delete: "Borró", void: "Anuló", pay: "Pagó",
  send: "Envió", login: "Ingresó", purge: "Borró definitivamente", invite: "Invitó", review_reset: "Revisión reiniciada",
};
const CAMPO: Record<string, string> = {
  role: "Rol", active: "Activo", site: "Sitio", notes: "Notas", photos: "Fotos", label: "Rótulo", data: "Datos", kind: "Tipo", status: "Estado",
  subtotal: "Subtotal", total: "Total", tax_amount: "Impuesto", tax_rate: "Tarifa IVA", description: "Descripción", date: "Fecha", reference: "Referencia",
  cost_labor: "Costo de mano de obra", cost_travel: "Costo de viáticos", cost_extra: "Costos extra", percent: "Porcentaje", base: "Base", name: "Nombre",
  quantity: "Cantidad", unit: "Unidad", note: "Observación", product_id: "Producto", attachment_url: "Adjunto", visit_date: "Fecha de visita",
};
const etiqueta = (m: Record<string, string>, k: string) => m[k] || k.replace(/_/g, " ");

function valor(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Sí" : "No";
  if (typeof v === "object" && Object.keys(v as object).length === 0) return "—";
  if (typeof v === "object") { const s = JSON.stringify(v); return s.length > 400 ? s.slice(0, 400) + "…" : s; }
  return String(v);
}

/** Tabla campo | antes | despues. "puntos" y "materiales" traen un nivel mas: uno por codigo o nombre. */
function Cambios({ before, after }: { before: Record<string, unknown> | null; after: Record<string, unknown> | null }) {
  const b = before || {}, a = after || {};
  const keys = [...new Set([...Object.keys(b), ...Object.keys(a)])];
  const simples = keys.filter((k) => k !== "puntos" && k !== "materiales");
  const hijos = keys.filter((k) => k === "puntos" || k === "materiales");
  if (!keys.length) return <p className="muted" style={{ fontSize: 13 }}>Este registro no guarda antes/después (registro anterior a la bitácora completa o acción sin cambios de campos).</p>;
  return (
    <>
      {simples.length > 0 && (
        <table className="table"><thead><tr><th>Campo</th><th>Antes</th><th>Después</th></tr></thead>
          <tbody>{simples.map((k) => <tr key={k}><td style={{ fontWeight: 600 }}>{etiqueta(CAMPO, k)}</td><td className="muted" style={{ wordBreak: "break-word" }}>{valor(b[k])}</td><td style={{ wordBreak: "break-word" }}>{valor(a[k])}</td></tr>)}</tbody></table>
      )}
      {hijos.map((k) => {
        const hb = (b[k] || {}) as Record<string, Record<string, unknown>>, ha = (a[k] || {}) as Record<string, Record<string, unknown>>;
        const codigos = [...new Set([...Object.keys(hb), ...Object.keys(ha)])];
        return (
          <div key={k} style={{ marginTop: 14 }}>
            <div className="meta" style={{ marginBottom: 6 }}>{k === "puntos" ? "Puntos" : "Materiales"}</div>
            {codigos.map((c) => {
              const estado = !(c in hb) ? "agregado" : !(c in ha) ? "quitado" : "cambiado";
              return (
                <div key={c} style={{ border: "1px solid var(--hair-2)", borderRadius: 10, padding: "8px 10px", marginBottom: 8 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}><b className="mono">{c}</b><span className={`badge badge--${estado === "agregado" ? "ok" : estado === "quitado" ? "bad" : "info"}`}>{estado}</span></div>
                  <Cambios before={hb[c] || null} after={ha[c] || null} />
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

export function Bitacora() {
  const { toast } = useSession();
  const [f, setF] = useState({ entity: "", user_id: "", action: "", from: "", to: "", company: "" });
  const [facets, setFacets] = useState<Facets | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [open, setOpen] = useState<Row | null>(null);

  const qs = useCallback((before?: number | null) => {
    const p = new URLSearchParams();
    Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v); });
    if (before) p.set("before_id", String(before));
    p.set("limit", "50");
    return p.toString();
  }, [f]);
  const load = useCallback(async (more = false) => {
    try {
      const r = await api<{ rows: Row[]; next_before_id: number | null }>(`/settings/audit?${qs(more ? next : null)}`);
      setRows((prev) => (more && prev ? [...prev, ...r.rows] : r.rows));
      setNext(r.next_before_id);
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo cargar la bitácora", "bad"); setRows([]); }
  }, [qs, next, toast]);
  useEffect(() => { api<Facets>("/settings/audit/facets").then(setFacets).catch(() => setFacets({ entities: [], actions: [], users: [] })); }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setRows(null); load(false); }, [f]);

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const ncambios = (r: Row) => Object.keys(r.after || r.before || {}).length;

  return (
    <>
      <Card title="Bitácora" flush extra={<span className="meta">solo lectura · no se edita ni se borra</span>}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: "12px 18px", borderBottom: "1px solid var(--hair)" }}>
          <select className="select" aria-label="Entidad" style={{ width: "auto", minWidth: 150 }} value={f.entity} onChange={set("entity")}>
            <option value="">Todas las entidades</option>
            {(facets?.entities || []).map((e) => <option key={e} value={e}>{etiqueta(ENTIDAD, e)}</option>)}
          </select>
          <select className="select" aria-label="Usuario" style={{ width: "auto", minWidth: 150 }} value={f.user_id} onChange={set("user_id")}>
            <option value="">Todos los usuarios</option>
            {(facets?.users || []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <select className="select" aria-label="Acción" style={{ width: "auto", minWidth: 130 }} value={f.action} onChange={set("action")}>
            <option value="">Todas las acciones</option>
            {(facets?.actions || []).map((a) => <option key={a} value={a}>{etiqueta(ACCION, a)}</option>)}
          </select>
          <input className="input" type="date" aria-label="Desde" style={{ width: "auto" }} value={f.from} onChange={set("from")} />
          <input className="input" type="date" aria-label="Hasta" style={{ width: "auto" }} value={f.to} onChange={set("to")} />
          <input className="input" aria-label="Contexto" placeholder="Cliente o proveedor" style={{ width: "auto", minWidth: 160 }} value={f.company} onChange={set("company")} />
        </div>
        {rows === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin movimientos" hint="Probá con otros filtros." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table"><thead><tr><th>Fecha</th><th>Usuario</th><th>Acción</th><th>Registro</th><th>Contexto</th><th className="num">Cambios</th><th /></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id}>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>{new Date(r.at).toLocaleString("es-CR", { dateStyle: "short", timeStyle: "short" })}</td>
                  <td>{r.user || <span className="muted">Sistema</span>}</td>
                  <td>{etiqueta(ACCION, r.action)}{r.reason && <div className="meta" style={{ textTransform: "none" }} title={r.reason}>Motivo: {r.reason.length > 40 ? r.reason.slice(0, 40) + "…" : r.reason}</div>}</td>
                  <td>{etiqueta(ENTIDAD, r.entity)}{r.entity_id ? <span className="mono muted"> #{r.entity_id}</span> : null}{r.version && <span className="meta"> · v{r.version}</span>}</td>
                  <td className="muted">{r.company || "—"}</td>
                  <td className="num mono">{ncambios(r) || "—"}</td>
                  <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setOpen(r)}>Ver</button></td>
                </tr>
              ))}</tbody></table>
          </div>
        )}
        {next && <div style={{ padding: 12, textAlign: "center" }}><button className="btn btn--ghost btn--sm" onClick={() => load(true)}>Cargar más</button></div>}
      </Card>
      {open && (
        <Modal title={`${etiqueta(ACCION, open.action)} · ${etiqueta(ENTIDAD, open.entity)}${open.entity_id ? ` #${open.entity_id}` : ""}`} wide onClose={() => setOpen(null)}>
          <div className="grid-2" style={{ fontSize: 13, marginBottom: 12, gap: 6 }}>
            <div><span className="meta">Fecha</span><div>{new Date(open.at).toLocaleString("es-CR")}</div></div>
            <div><span className="meta">Usuario</span><div>{open.user || "Sistema"}</div></div>
            <div><span className="meta">IP</span><div className="mono">{open.ip || "—"}</div></div>
            <div><span className="meta">Versión</span><div>{open.version || "—"}</div></div>
            <div><span className="meta">Contexto</span><div>{open.company || "—"}</div></div>
            <div><span className="meta">Motivo</span><div>{open.reason || "—"}</div></div>
          </div>
          <Cambios before={open.before} after={open.after} />
          {open.diff && <details style={{ marginTop: 12 }}><summary className="meta" style={{ cursor: "pointer" }}>Detalle técnico</summary><pre className="mono" style={{ fontSize: 11, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{JSON.stringify(open.diff, null, 2)}</pre></details>}
        </Modal>
      )}
    </>
  );
}
