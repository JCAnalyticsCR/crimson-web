/* Levantamiento del aliado: el mismo contenido que el del técnico de Crimson (puntos con los campos de la plantilla,
   materiales, fotos y la sección común), con las rutas del portal de aliados (fieldApi.ALLY_FIELD_API). Sin costeo,
   márgenes, recomendación de equipos ni informe: eso lo hace Crimson. Lo que el aliado guarda conserva lo que Crimson
   ya costeó (la API no lo toca). Al terminar: "Enviar a revisión". Si vuelve con observaciones, se marcan resueltas.
   El personal requerido y quienes hicieron la visita solo se mandan si el aliado los cambió (la API conserva lo demás,
   incluidos los técnicos de Crimson de la visita, que aquí no se ven). */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import { PhotoStrip } from "../../ui/MediaPicker";
import { ALLY_FIELD_API } from "../field/fieldApi";
import { CommonSection, PointFields, initialStatus, type Common, type CommonSpec, type DataStatus, type V2Field } from "../field/SurveyV2";
import { errorText, useAlly, type Item, type Point, type SurveyFull, type Tech } from "./shared";

type KindSpec = { label: string; point_prefix: string; point_label: string; fields: V2Field[]; materials: string[] };
type Labor = Record<string, { people: string; days: string }>;
type Draft = {
  kind: string; site: string; contact: Record<string, string>; visit_date: string; notes: string; photos: string[]; points: Point[]; items: Item[];
  common: Common | null; received_by: string; technician_id: number | null; labor: Labor; visit_tech_ids: number[];
};
const LABOR_ORDEN: [string, string][] = [["tecnico", "Técnicos"], ["civil", "Obra civil"], ["contratado", "Contratados"]];

const laborDe = (s: SurveyFull): Labor =>
  Object.fromEntries(LABOR_ORDEN.map(([k]) => [k, { people: String(s.labor?.[k]?.people ?? 0), days: String(Number(s.labor?.[k]?.days ?? 0)) }]));
const toDraft = (s: SurveyFull): Draft => ({
  kind: s.kind, site: s.site || "", contact: (s.contact as Record<string, string>) || {}, visit_date: s.visit_date || "", notes: s.notes || "",
  photos: s.photos || [], points: s.points || [], items: s.items || [], common: (s.common as Common) || null, received_by: s.received_by || "", technician_id: s.technician_id,
  labor: laborDe(s), visit_tech_ids: s.visit_tech_ids || [],
});
const n = (v: string) => Number(String(v).replace(",", ".")) || 0;

function Input({ f, value, onChange, disabled }: { f: V2Field; value: unknown; onChange: (v: unknown) => void; disabled?: boolean }) {
  if (f.type === "bool") return <label className="pf-bool"><input type="checkbox" disabled={disabled} checked={value === true} onChange={(e) => onChange(e.target.checked)} />Sí</label>;
  if (f.type === "select") return <select className="select" disabled={disabled} value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)}><option value="">—</option>{f.options?.map((o) => <option key={o}>{o}</option>)}</select>;
  if (f.type === "multi") {
    const v = Array.isArray(value) ? (value as string[]) : [];
    return <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{f.options?.map((o) => <label key={o} className="pf-bool"><input type="checkbox" disabled={disabled} checked={v.includes(o)} onChange={(e) => onChange(e.target.checked ? [...v, o] : v.filter((x) => x !== o))} />{o}</label>)}</div>;
  }
  return <input className={`input${f.type === "number" ? " input--mono" : ""}`} inputMode={f.type === "number" ? "decimal" : undefined} disabled={disabled} placeholder={f.placeholder || (f.unit ? f.unit : undefined)} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
}

export default function AllySurvey() {
  const { id } = useParams();
  const sid = Number(id);
  const { toast } = useSession();
  const { readOnly, can, isTech } = useAlly();
  const nav = useNavigate();
  const [s, setS] = useState<SurveyFull | null>(null);
  const [d, setD] = useState<Draft | null>(null);
  const [specs, setSpecs] = useState<Record<string, KindSpec> | null>(null);
  const [common, setCommon] = useState<CommonSpec | null>(null);
  const [techs, setTechs] = useState<Tech[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);

  const load = useCallback(() => {
    api<SurveyFull>(ALLY_FIELD_API.survey(sid)).then((x) => { setS(x); setD(toDraft(x)); setDirty(false); }).catch((e) => setErr(errorText(e)));
  }, [sid]);
  useEffect(load, [load]);
  useEffect(() => {
    api<Record<string, KindSpec>>(ALLY_FIELD_API.specs).then(setSpecs).catch(() => setSpecs({}));
    api<CommonSpec>(ALLY_FIELD_API.specsCommon).then(setCommon).catch(() => setCommon(null));
    api<Tech[]>(ALLY_FIELD_API.technicians).then(setTechs).catch(() => setTechs([]));
  }, []);
  const productos = useCallback(async (q: string): Promise<LookupItem[]> => {
    const r = await api<{ id: number; code: string; name: string; unit: string }[]>(`${ALLY_FIELD_API.productSearch}?q=${encodeURIComponent(q)}`);
    return r.map((p) => ({ id: p.id, label: p.name, hint: p.code }));
  }, []);
  const spec = useMemo(() => (specs && d ? specs[d.kind] || specs.otro : null), [specs, d]);

  if (err) return <p className="muted">{err}</p>;
  if (!s || !d) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const ro = readOnly || !s.can_edit;
  const up = (patch: Partial<Draft>) => { setD({ ...d, ...patch }); setDirty(true); };
  const setPoint = (i: number, patch: Partial<Point>) => up({ points: d.points.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
  const setItem = (i: number, patch: Partial<Item>) => up({ items: d.items.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
  const addPoint = () => {
    const pre = spec?.point_prefix || "PT";
    const n = d.points.length + 1;
    up({ points: [...d.points, { code: `${pre}-${String(n).padStart(2, "0")}`, label: null, data: {}, data_status: initialStatus(spec?.fields), photos: [], notes: null }] });
  };

  const original = toDraft(s);
  const laborCambio = JSON.stringify(d.labor) !== JSON.stringify(original.labor);
  const visitaCambio = JSON.stringify(d.visit_tech_ids) !== JSON.stringify(original.visit_tech_ids);
  const body = () => ({
    kind: d.kind, site: d.site || null, contact: d.contact, visit_date: d.visit_date || null, notes: d.notes || null, photos: d.photos,
    points: d.points.map((p) => ({ id: p.id, code: p.code, kind: p.kind && p.kind !== d.kind ? p.kind : null, label: p.label, data: p.data, data_status: p.data_status, photos: p.photos, notes: p.notes })),
    items: d.items.filter((it) => it.name.trim()).map((it) => ({ id: it.id, product_id: it.product_id, name: it.name, quantity: it.quantity || 0, unit: it.unit || "Unid", note: it.note, kind: it.kind })),
    common: d.common, received_by: d.received_by || null,
    ...(d.technician_id && d.technician_id !== s.technician_id ? { technician_id: d.technician_id } : {}),
    // solo si los cambió: la API conserva el personal que Crimson costea y los técnicos de Crimson de la visita
    ...(laborCambio ? { labor: Object.fromEntries(Object.entries(d.labor).map(([k, v]) => [k, { people: Math.round(n(v.people)), days: n(v.days) }])) } : {}),
    ...(visitaCambio ? { visit_tech_ids: d.visit_tech_ids } : {}),
  });
  // quienes hicieron la visita: su equipo (/aliado/tecnicos) y los de su equipo que ya estaban anotados
  const visitantes = [
    ...techs.map((t) => ({ id: t.id, name: t.name })),
    ...(s.visit_tech_ids || []).map((id, i) => ({ id, name: s.visit_techs?.[i] || "Técnico" })).filter((v) => !techs.some((t) => t.id === v.id)),
  ];
  const setLabor = (k: string, patch: Partial<{ people: string; days: string }>) => up({ labor: { ...d.labor, [k]: { ...d.labor[k], ...patch } } });
  const guardar = async (silencioso = false) => {
    setBusy(true);
    try { const x = await api<SurveyFull>(ALLY_FIELD_API.saveSurvey(sid), { method: "PUT", json: body() }); setS(x); setD(toDraft(x)); setDirty(false); if (!silencioso) toast("Levantamiento guardado"); return true; }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); return false; }
    finally { setBusy(false); }
  };
  const enviar = async () => {
    if (dirty && !(await guardar(true))) return;
    setBusy(true);
    try {
      const x = await api<SurveyFull>(ALLY_FIELD_API.sendSurvey(sid), { method: "POST" });
      toast("Enviado a revisión de Crimson");
      // el técnico solo ve sus borradores y los devueltos: el enviado sale de su lista y vuelve a "Hoy"
      if (isTech) { nav("/", { replace: true }); return; }
      setS(x); setD(toDraft(x)); setDirty(false);
    }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo enviar", "bad"); }
    finally { setBusy(false); }
  };
  const resolver = async (oid: number) => {
    const resolution = window.prompt("¿Qué se corrigió? (opcional)") ?? "";
    try { const x = await api<SurveyFull>(ALLY_FIELD_API.resolveObs(sid, oid), { method: "POST", json: { resolution } }); setS(x); } catch (e) { toast(e instanceof Error ? e.message : "No se pudo marcar", "bad"); }
  };

  return (
    <>
      <div className="cp-h">
        <div><div className="meta">{s.number} · {s.kind_label}{s.opportunity ? ` · ${s.opportunity.number} ${s.opportunity.title}` : ""}</div><h1 className="h1">{s.customer || "Levantamiento"}</h1></div>
        <div className="al-actions">
          {s.pending_review ? <span className="badge badge--info">{s.pending_review.label}</span> : <span className="badge badge--muted">{s.review_label || s.status}</span>}
          {!ro && <button className="btn btn--soft" disabled={busy || !dirty} onClick={() => guardar()}><Icon d={I.check} />Guardar</button>}
          {!ro && <button className="btn btn--crimson" disabled={busy} onClick={enviar}><Icon d={I.arrow} />Enviar a revisión</button>}
        </div>
      </div>
      {s.observations.length > 0 && (
        <div className="cp-list" style={{ marginBottom: 12 }}>
          {s.observations.map((o) => (
            <div key={o.id} className={`al-obs${o.resolved ? " is-done" : ""}`}>
              <b>{o.point_code || "General"}</b> · {o.text}
              <div className="meta">{o.created_by}{o.resolved ? ` · resuelta${o.resolution ? `: ${o.resolution}` : ""}` : ""}</div>
              {!o.resolved && !readOnly && s.can_edit && <button className="btn btn--soft btn--sm" style={{ marginTop: 6 }} onClick={() => resolver(o.id)}>Marcar resuelta</button>}
            </div>
          ))}
        </div>
      )}
      <div className="cp-card cp-form">
        <div className="cp-grid cp-grid--2">
          <Field label="Sitio"><input className="input" disabled={ro} value={d.site} onChange={(e) => up({ site: e.target.value })} /></Field>
          <Field label="Fecha de la visita"><input className="input" type="date" disabled={ro} value={d.visit_date} onChange={(e) => up({ visit_date: e.target.value })} /></Field>
          <Field label="Contacto en sitio"><input className="input" disabled={ro} value={d.contact.name || ""} onChange={(e) => up({ contact: { ...d.contact, name: e.target.value } })} /></Field>
          <Field label="Teléfono"><input className="input" disabled={ro} value={d.contact.phone || ""} onChange={(e) => up({ contact: { ...d.contact, phone: e.target.value } })} /></Field>
          {can("asignar") && techs.length > 0 && (
            <Field label="Técnico a cargo">
              <select className="select" disabled={ro} value={d.technician_id ?? ""} onChange={(e) => up({ technician_id: e.target.value ? Number(e.target.value) : null })}>
                {techs.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </Field>
          )}
        </div>
        <Field label="Notas generales"><textarea className="textarea" rows={3} disabled={ro} value={d.notes} onChange={(e) => up({ notes: e.target.value })} /></Field>
        <Field label="Personal requerido para el trabajo" hint="Cuántas personas y cuántos días. Crimson lo usa para estimar la mano de obra.">
          <div className="al-labor">
            {LABOR_ORDEN.map(([k, label]) => (
              <div key={k} className="al-labor__row">
                <span>{label}</span>
                <input className="input input--mono" inputMode="numeric" aria-label={`${label}: personas`} disabled={ro} value={d.labor[k]?.people ?? "0"} onChange={(e) => setLabor(k, { people: e.target.value })} />
                <span className="meta">personas ×</span>
                <input className="input input--mono" inputMode="decimal" aria-label={`${label}: días`} disabled={ro} value={d.labor[k]?.days ?? "0"} onChange={(e) => setLabor(k, { days: e.target.value })} />
                <span className="meta">días</span>
              </div>
            ))}
          </div>
        </Field>
        {visitantes.length > 0 && (
          <Field label="Quiénes hicieron la visita">
            <div className="chips">
              {visitantes.map((v) => {
                const on = d.visit_tech_ids.includes(v.id);
                return <button type="button" key={v.id} className={`chip${on ? " is-on" : ""}`} aria-pressed={on} disabled={ro} onClick={() => up({ visit_tech_ids: on ? d.visit_tech_ids.filter((x) => x !== v.id) : [...d.visit_tech_ids, v.id] })}>{v.name}</button>;
              })}
            </div>
          </Field>
        )}
        <Field label="Fotos generales"><PhotoStrip value={d.photos} disabled={ro} uploadPath={ALLY_FIELD_API.mediaUpload} onChange={(v) => up({ photos: v })} /></Field>
      </div>
      <div style={{ margin: "12px 0" }}>
        <CommonSection spec={common} value={d.common} onChange={(c) => up({ common: c })} receivedBy={d.received_by} onReceivedBy={(v) => up({ received_by: v })} kinds={[d.kind]} readOnly={ro} />
      </div>
      <h2 className="h3 al-sec">{spec?.point_label || "Puntos"}s ({d.points.length})</h2>
      <div className="cp-list">
        {d.points.map((p, i) => (
          <div className="al-point" key={`${p.code}-${i}`}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input className="input input--mono" style={{ maxWidth: 120 }} disabled={ro} value={p.code} onChange={(e) => setPoint(i, { code: e.target.value })} aria-label="Código del punto" />
              <input className="input" disabled={ro} value={p.label || ""} placeholder="Descripción" onChange={(e) => setPoint(i, { label: e.target.value })} />
              {!ro && <button type="button" className="btn btn--ghost btn--icon" aria-label="Quitar punto" onClick={() => up({ points: d.points.filter((_, j) => j !== i) })}><Icon d={I.x} /></button>}
            </div>
            {spec && (
              <PointFields
                fields={spec.fields}
                data={p.data}
                status={p.data_status}
                readOnly={ro}
                onStatus={(k, v: DataStatus) => setPoint(i, { data_status: { ...(p.data_status || {}), [k]: v } })}
                render={(f) => <Input f={f} value={p.data[f.key]} disabled={ro} onChange={(v) => setPoint(i, { data: { ...p.data, [f.key]: v } })} />}
              />
            )}
            <PhotoStrip value={p.photos} disabled={ro} uploadPath={ALLY_FIELD_API.mediaUpload} caption={p.code} onChange={(v) => setPoint(i, { photos: v })} />
            <textarea className="textarea" rows={2} disabled={ro} placeholder="Notas del punto" value={p.notes || ""} onChange={(e) => setPoint(i, { notes: e.target.value })} />
          </div>
        ))}
      </div>
      {!ro && <button type="button" className="btn btn--soft" style={{ marginTop: 8 }} onClick={addPoint}><Icon d={I.plus} />Agregar {spec?.point_label?.toLowerCase() || "punto"}</button>}
      <h2 className="h3 al-sec">Materiales y equipos</h2>
      <div className="al-lines">
        {d.items.map((it, i) => (
          <div className="al-line" key={`${it.id ?? "n"}-${i}`}>
            <Field label="Producto o material">
              <Lookup value={it.name} disabled={ro} placeholder="Buscar en el catálogo o escribir" fetcher={productos} onSelect={(sel, text) => setItem(i, sel ? { product_id: sel.id, name: sel.label } : { product_id: null, name: text })} />
            </Field>
            <Field label="Cantidad"><input className="input input--mono" inputMode="decimal" disabled={ro} value={String(it.quantity)} onChange={(e) => setItem(i, { quantity: e.target.value })} /></Field>
            <Field label="Unidad"><input className="input" disabled={ro} value={it.unit} maxLength={10} onChange={(e) => setItem(i, { unit: e.target.value })} /></Field>
            <Field label="Nota"><input className="input" disabled={ro} value={it.note || ""} onChange={(e) => setItem(i, { note: e.target.value })} /></Field>
            {!ro && <button type="button" className="btn btn--ghost btn--icon" aria-label="Quitar" onClick={() => up({ items: d.items.filter((_, j) => j !== i) })}><Icon d={I.x} /></button>}
          </div>
        ))}
        {!ro && <button type="button" className="btn btn--soft btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => up({ items: [...d.items, { product_id: null, name: "", quantity: "1", unit: "Unid", note: null, kind: null }] })}><Icon d={I.plus} size={14} />Agregar material</button>}
      </div>
      {ro && !readOnly && s.pending_review && <p className="muted" style={{ marginTop: 12 }}>Está en revisión de Crimson: si lo devuelven con observaciones, se vuelve a poder editar.</p>}
    </>
  );
}
