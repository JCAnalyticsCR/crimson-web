/* Revisión 2 (puntos 3 y A3): "Alcances y responsables". Una sola fuente de datos ligada a la oportunidad: el
   levantamiento, la oportunidad y su proyecto muestran exactamente las mismas partidas (services/scope_items.py).
   Sin montos: lo económico vive en las solicitudes de precio y los acuerdos. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import "./opportunities.css";

export type ScopeParty = { kind: "crimson" | "aliado" | "por_asignar"; participation_id: number | null; name: string };
export type ScopeItem = {
  id: number; kind: string; kind_label: string; description: string; quantity: string | null; unit: string | null;
  supplier: ScopeParty; executor: ScopeParty; status: string; status_label: string; shared: boolean; notes: string | null;
  position?: number; confirmed_at: string | null; confirmed_by: string | null; updated_at?: string;
};
export type ScopeData = {
  source: { opportunity_id: number | null; survey_id: number | null };
  items: ScopeItem[];
  summary: { total: number; por_estado: Record<string, number>; por_asignar: number; completados: number };
  options: ScopeParty[];
  kinds: Record<string, string>;
  states: Record<string, string>;
  typical: { kind: string; description: string }[];
  can_edit?: boolean;
};

export const SCOPE_BADGE: Record<string, string> = { por_confirmar: "warn", confirmado: "info", en_curso: "info", completado: "ok" };

/* select de responsable: "crimson" | "por_asignar" | "aliado:<id>" */
export const partyKey = (p: Pick<ScopeParty, "kind" | "participation_id">) => (p.kind === "aliado" ? `aliado:${p.participation_id}` : p.kind);
const fromKey = (k: string): { kind: string; participation_id: number | null } =>
  k.startsWith("aliado:") ? { kind: "aliado", participation_id: Number(k.slice(7)) } : { kind: k, participation_id: null };

type Form = { id?: number; kind: string; description: string; quantity: string; unit: string; supplier: string; executor: string; status: string; shared: boolean; notes: string };
const blank: Form = { kind: "mano_obra", description: "", quantity: "", unit: "", supplier: "por_asignar", executor: "por_asignar", status: "por_confirmar", shared: true, notes: "" };

function body(f: Form) {
  const s = fromKey(f.supplier);
  const e = fromKey(f.executor);
  return {
    kind: f.kind, description: f.description.trim(), quantity: f.quantity.trim() ? Number(f.quantity.replace(",", ".")) : null, unit: f.unit.trim() || null,
    supplier_kind: s.kind, supplier_participation_id: s.participation_id, executor_kind: e.kind, executor_participation_id: e.participation_id,
    status: f.status, shared: f.shared, notes: f.notes.trim() || null,
  };
}

/** Alcances y responsables. `url` = GET (…/scope); `editBase` = …/scope-items (sin él, solo lectura). */
export default function ScopeItems({ url, editBase, canEdit, title = "Alcances y responsables", hint, extra, onLoaded, reloadKey }: {
  url: string; editBase?: string | null; canEdit: boolean; title?: string; hint?: string; extra?: React.ReactNode;
  onLoaded?: (d: ScopeData) => void; reloadKey?: unknown;
}) {
  const { toast } = useSession();
  const [data, setData] = useState<ScopeData | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const editable = canEdit && !!editBase && data?.can_edit !== false;

  const load = useCallback(() => {
    api<ScopeData>(url).then((d) => { setData(d); setErr(null); onLoaded?.(d); }).catch((e) => setErr(e instanceof Error ? e.message : "No se pudo cargar"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);
  useEffect(() => { load(); }, [load, reloadKey]);

  const run = async (fn: () => Promise<ScopeData>, ok: string) => {
    if (busy) return false;
    setBusy(true);
    try { const d = await fn(); setData(d); onLoaded?.(d); toast(ok); return true; }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return false; }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!form || !editBase || form.description.trim().length < 2) return;
    const ok = await run(
      () => api<ScopeData>(form.id ? `${editBase}/${form.id}` : editBase, { method: form.id ? "PUT" : "POST", json: body(form) }),
      form.id ? "Alcance actualizado" : "Alcance agregado",
    );
    if (ok) setForm(null);
  };
  const remove = (it: ScopeItem) => {
    if (!editBase || !confirm(`¿Quitar el alcance «${it.kind_label} · ${it.description}»?`)) return;
    run(() => api<ScopeData>(`${editBase}/${it.id}`, { method: "DELETE" }), "Alcance quitado");
  };
  const typical = async () => {
    if (!editBase || !data || busy) return;
    const ya = new Set(data.items.map((x) => x.kind));
    const faltan = data.typical.filter((t) => !ya.has(t.kind));
    if (!faltan.length) { toast("Ya están los alcances típicos"); return; }
    setBusy(true);
    try {
      let d = data;
      for (const t of faltan) d = await api<ScopeData>(editBase, { method: "POST", json: { kind: t.kind, description: t.description } });
      setData(d); onLoaded?.(d); toast(`${faltan.length} alcances típicos agregados (por asignar)`);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const edit = (it: ScopeItem) => setForm({
    id: it.id, kind: it.kind, description: it.description, quantity: it.quantity ?? "", unit: it.unit ?? "", supplier: partyKey(it.supplier), executor: partyKey(it.executor),
    status: it.status, shared: it.shared, notes: it.notes ?? "",
  });

  const sum = data?.summary;
  return (
    <section className="ally-panel scope-panel" aria-label={title}>
      <div className="ally-panel__head">
        <div style={{ minWidth: 0 }}>
          <b>{title}</b>
          {sum && sum.total > 0 && (
            <span className="meta" style={{ marginLeft: 8 }}>
              {sum.total} partida{sum.total !== 1 ? "s" : ""} · {sum.completados} completada{sum.completados !== 1 ? "s" : ""}
              {sum.por_estado.por_confirmar ? ` · ${sum.por_estado.por_confirmar} por confirmar` : ""}
              {sum.por_asignar ? ` · ${sum.por_asignar} sin responsable` : ""}
            </span>
          )}
        </div>
        <div className="ally__actions">
          {extra}
          {editable && !form && data && data.typical.length > 0 && <button className="btn btn--ghost btn--sm" disabled={busy} onClick={typical}>Cargar alcances típicos</button>}
          {editable && !form && <button className="btn btn--soft btn--sm" onClick={() => setForm({ ...blank })}><Icon d={I.plus} />Agregar alcance</button>}
        </div>
      </div>
      {hint && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>{hint}</p>}
      {err && <p className="muted" style={{ fontSize: 13, margin: 0, color: "var(--bad)" }}>{err}</p>}
      {data && data.items.length === 0 && !form && (
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          Todavía no hay alcances. Definí qué hace falta para la obra (mano de obra, viáticos, equipos, materiales, configuración, obra civil), quién lo suministra y quién lo ejecuta.
        </p>
      )}
      {data && data.items.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="table table--sm table--stack">
            <thead><tr><th>Alcance</th><th>Qué se necesita</th><th>Cantidad</th><th>Suministra</th><th>Ejecuta</th><th>Estado</th>{editable && <th />}</tr></thead>
            <tbody>{data.items.map((it) => (
              <tr key={it.id}>
                <td data-label="Alcance" style={{ fontWeight: 600 }}>{it.kind_label}</td>
                <td data-label="Qué se necesita" className="stack-full">
                  {it.description}
                  {!it.shared && <div className="meta">Solo la ve su responsable</div>}
                  {it.notes && <div className="meta" style={{ whiteSpace: "pre-wrap" }}>{it.notes}</div>}
                </td>
                <td data-label="Cantidad" className="mono">{it.quantity ? `${Number(it.quantity).toLocaleString("es-CR")} ${it.unit || ""}` : "—"}</td>
                <td data-label="Suministra" style={{ color: it.supplier.kind === "por_asignar" ? "var(--warn)" : undefined }}>{it.supplier.name}</td>
                <td data-label="Ejecuta" style={{ color: it.executor.kind === "por_asignar" ? "var(--warn)" : undefined }}>{it.executor.name}</td>
                <td data-label="Estado">
                  <span className={`badge badge--${SCOPE_BADGE[it.status] || "muted"}`}>{it.status_label}</span>
                  {it.confirmed_at && <div className="meta">Confirmado {fmtDate(it.confirmed_at)}{it.confirmed_by ? ` · ${it.confirmed_by}` : ""}</div>}
                </td>
                {editable && (
                  <td className="num stack-actions">
                    <button className="btn btn--ghost btn--sm" onClick={() => edit(it)}>Editar</button>
                    <button className="btn btn--ghost btn--sm" onClick={() => remove(it)} aria-label={`Quitar ${it.kind_label}`}><Icon d={I.x} size={14} /></button>
                  </td>
                )}
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {form && data && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>{form.id ? "Editar alcance" : "Nuevo alcance"}</b><span className="muted">Crimson propone; el aliado asignado lo confirma desde su portal.</span></div>
          <div className="grid-3">
            <Field label="Alcance"><select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{Object.entries(data.kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Cantidad (opcional)"><input className="input input--mono" inputMode="decimal" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} placeholder="3" /></Field>
            <Field label="Unidad (opcional)"><input className="input" maxLength={12} value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} placeholder="días, m, u" /></Field>
          </div>
          <Field label="Qué se necesita"><input className="input" autoFocus maxLength={400} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Mano de obra para instalar cámaras" /></Field>
          <div className="grid-3">
            <Field label="Quién lo suministra"><PartySelect options={data.options} value={form.supplier} onChange={(v) => setForm({ ...form, supplier: v })} /></Field>
            <Field label="Quién lo ejecuta"><PartySelect options={data.options} value={form.executor} onChange={(v) => setForm({ ...form, executor: v })} /></Field>
            <Field label="Estado"><select className="select" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>{Object.entries(data.states).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          </div>
          {data.source.opportunity_id == null && <p className="meta" style={{ margin: 0 }}>El levantamiento no tiene oportunidad: por ahora solo Crimson o por asignar. Al ligarlo a una oportunidad, estos alcances pasan a ella y se pueden asignar a sus aliados.</p>}
          <Field label="Notas (las ve quien ve este alcance)"><textarea className="textarea" style={{ minHeight: 44 }} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
          <label className="pf-bool"><input type="checkbox" checked={form.shared} onChange={(e) => setForm({ ...form, shared: e.target.checked })} />Compartido con los aliados de la oportunidad
            <span className="meta" style={{ marginLeft: 6 }}>Por defecto todos ven aportes, responsables y avances. Desmarcado, solo lo ve su responsable.</span></label>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setForm(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={busy || form.description.trim().length < 2} onClick={save}>Guardar alcance</button>
          </div>
        </div>
      )}
    </section>
  );
}

export function PartySelect({ options, value, onChange }: { options: ScopeParty[]; value: string; onChange: (v: string) => void }) {
  const has = options.some((o) => partyKey(o) === value);
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => <option key={partyKey(o)} value={partyKey(o)}>{o.name}</option>)}
      {!has && <option value={value}>Por asignar</option>}
    </select>
  );
}
