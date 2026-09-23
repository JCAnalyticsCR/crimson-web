/* Levantamientos tecnicos: lo que el tecnico llena en sitio desde el celular y lo que administracion cotiza despues.
   El formulario no esta escrito aqui: lo dibuja /field/specs, asi agregar un tipo de solucion es tocar solo la API. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts } from "../../ui/Lookup";

type FieldSpec = { key: string; label: string; type: "text" | "number" | "select" | "multi" | "bool"; options?: string[]; unit?: string; placeholder?: string };
type Spec = { label: string; point_prefix: string; point_label: string; fields: FieldSpec[]; materials: string[] };
type Point = { id?: number; code: string; label: string; data: Record<string, unknown>; photos: string[]; notes: string | null };
type Item = { id?: number; product_id: number | null; name: string; quantity: string; unit: string; note: string | null };
type Survey = {
  id: number; number: string; kind: string; kind_label: string; status: string; customer_id: number | null; customer: string | null;
  site: string | null; opportunity_id: number | null; technician: string | null; visit_date: string | null; techs: number; days: string;
  notes: string | null; photos: string[]; quote_id: number | null; points_count: number; created_at: string;
  points?: Point[]; items?: Item[];
};
type CostLine = { product_id: number | null; name: string; quantity: string; unit: string; unit_cost: string; cost: string; unit_price: string; price: string; has_cost: boolean };
type Costing = {
  lines: CostLine[]; labor: { techs: number; days: string; day_cost: string; cost: string; travel: string; price: string };
  cost_total: string; price_suggested: string; margin_pct: number; margin_target: number; missing_cost: string[];
};

const STATUS: Record<string, { label: string; tone: string }> = {
  borrador: { label: "Borrador", tone: "warn" }, enviado: { label: "Enviado a oficina", tone: "info" },
  cotizado: { label: "Cotizado", tone: "ok" }, cerrado: { label: "Cerrado", tone: "muted" },
};

const emptySurvey = (kind: string) => ({ kind, customer_id: "", customer_name: "", opportunity_id: "", site: "", visit_date: "", techs: 2, days: "1", notes: "", points: [] as Point[], items: [] as Item[] });
type Draft = ReturnType<typeof emptySurvey> & { id?: number; number?: string; status?: string; quote_id?: number | null };

export default function Surveys() {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [specs, setSpecs] = useState<Record<string, Spec>>({});
  const [rows, setRows] = useState<Survey[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [costing, setCosting] = useState<Costing | null>(null);
  const [margin, setMargin] = useState(35);
  const [busy, setBusy] = useState(false);
  const prefilled = useRef<string | null>(null); // no rearmar el borrador si el usuario ya empezó a escribir

  const verCostos = allows("catalog.precios");
  const spec = draft ? specs[draft.kind] : undefined;

  const load = useCallback(() => api<Survey[]>("/surveys").then(setRows), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api<Record<string, Spec>>("/field/specs").then(setSpecs);
  }, []);

  // /levantamientos?nuevo=<oportunidad> o ?id=<levantamiento> (desde la oportunidad o el correo)
  useEffect(() => {
    const id = params.get("id");
    const nuevo = params.get("nuevo");
    if (id && prefilled.current !== `s${id}`) {
      prefilled.current = `s${id}`;
      open(Number(id));
    } else if (nuevo && prefilled.current !== `n${nuevo}`) {
      prefilled.current = `n${nuevo}`;
      // Viene de una oportunidad: el cliente, el sitio y la solución ya se escribieron una vez.
      api<{ customer_id: number | null; customer: string | null; solution: string | null; title: string }>(`/opportunities/${nuevo}`)
        .then((o) => setDraft({
          ...emptySurvey(o.solution && o.solution in specs ? o.solution : "cctv"),
          opportunity_id: nuevo,
          customer_id: o.customer_id ? String(o.customer_id) : "",
          customer_name: o.customer || "",
        }))
        .catch(() => setDraft({ ...emptySurvey("cctv"), opportunity_id: nuevo }));
    }
  }, [params, specs]);

  const open = async (id: number) => {
    const s = await api<Survey>(`/surveys/${id}`);
    setDraft({
      id: s.id, number: s.number, status: s.status, quote_id: s.quote_id, kind: s.kind, customer_id: s.customer_id ? String(s.customer_id) : "", customer_name: s.customer || "",
      opportunity_id: s.opportunity_id ? String(s.opportunity_id) : "", site: s.site || "", visit_date: s.visit_date || "", techs: s.techs,
      days: String(s.days), notes: s.notes || "", points: s.points || [], items: (s.items || []).map((i) => ({ ...i, quantity: String(i.quantity) })),
    });
  };

  const close = () => { setDraft(null); setCosting(null); if (params.get("id") || params.get("nuevo")) setParams({}); };

  const body = (d: Draft) => ({
    kind: d.kind, customer_id: d.customer_id ? Number(d.customer_id) : null, opportunity_id: d.opportunity_id ? Number(d.opportunity_id) : null,
    site: d.site || null, visit_date: d.visit_date || null, techs: Number(d.techs), days: Number(d.days), notes: d.notes || null,
    points: d.points.map((p) => ({ id: p.id, code: p.code, label: p.label || null, data: p.data, photos: p.photos || [], notes: p.notes })),
    items: d.items.filter((i) => i.name.trim()).map((i) => ({ id: i.id, product_id: i.product_id, name: i.name, quantity: Number(i.quantity || 0), unit: i.unit || "Unid", note: i.note })),
  });

  const save = async (silent = false) => {
    if (!draft) return null;
    setBusy(true);
    try {
      const s = await api<Survey>(draft.id ? `/surveys/${draft.id}` : "/surveys", { method: draft.id ? "PUT" : "POST", json: body(draft) });
      if (!silent) toast(draft.id ? "Levantamiento guardado" : `Levantamiento ${s.number} creado`);
      setDraft((d) => (d ? { ...d, id: s.id, number: s.number, status: s.status } : d));
      load();
      return s;
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return null; }
    finally { setBusy(false); }
  };

  const suggest = async () => {
    const s = await save(true);
    if (!s) return;
    const mats = await api<{ name: string; quantity: string; unit: string }[]>(`/surveys/${s.id}/suggest`, { method: "POST" });
    setDraft((d) => {
      if (!d) return d;
      const have = new Set(d.items.map((i) => i.name.toLowerCase()));
      const nuevos = mats.filter((m) => !have.has(m.name.toLowerCase())).map((m) => ({ product_id: null, name: m.name, quantity: String(m.quantity), unit: m.unit, note: null }));
      return { ...d, items: [...d.items, ...nuevos] };
    });
    toast("Materiales sugeridos agregados; ajustá las cantidades.");
  };

  const send = async () => {
    const s = await save(true);
    if (!s) return;
    try { await api(`/surveys/${s.id}/send`, { method: "POST" }); toast("Enviado a oficina para costeo"); close(); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const cost = async (m = margin) => {
    const s = await save(true); // el costeo lee de la base: lo que está en pantalla tiene que estar guardado
    if (!s?.id) return;
    try { setCosting(await api<Costing>(`/surveys/${s.id}/costing?margin=${m}`)); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const toQuote = async () => {
    if (!draft?.id) return;
    if (!(await save(true))) return;
    try {
      const q = await api<{ quote_id: number; number: string }>(`/surveys/${draft.id}/quote`, { method: "POST", json: { margin, include_labor: true } });
      toast(`Cotización ${q.number} creada`); nav(`/cotizaciones/${q.quote_id}`);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const addPoint = () => setDraft((d) => {
    if (!d) return d;
    const pre = specs[d.kind]?.point_prefix || "PTO";
    return { ...d, points: [...d.points, { code: `${pre}-${String(d.points.length + 1).padStart(2, "0")}`, label: "", data: {}, photos: [], notes: null }] };
  });
  const setPoint = (i: number, patch: Partial<Point>) => setDraft((d) => (d ? { ...d, points: d.points.map((p, j) => (j === i ? { ...p, ...patch } : p)) } : d));
  const setData = (i: number, key: string, v: unknown) => setPoint(i, { data: { ...draft!.points[i].data, [key]: v } });
  const setItem = (i: number, patch: Partial<Item>) => setDraft((d) => (d ? { ...d, items: d.items.map((x, j) => (j === i ? { ...x, ...patch } : x)) } : d));

  const readOnly = draft?.status === "cotizado" || draft?.status === "cerrado";
  const puedeCotizar = verCostos && allows("sales.crear") && draft?.id && !draft.quote_id;

  const field = (f: FieldSpec, i: number) => {
    const v = draft!.points[i].data[f.key];
    if (f.type === "select") return <select className="select" value={String(v ?? "")} onChange={(e) => setData(i, f.key, e.target.value)}><option value="">—</option>{f.options?.map((o) => <option key={o}>{o}</option>)}</select>;
    if (f.type === "bool") return <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, minHeight: 38 }}><input type="checkbox" checked={!!v} onChange={(e) => setData(i, f.key, e.target.checked)} />Sí</label>;
    if (f.type === "multi") {
      const arr = Array.isArray(v) ? (v as string[]) : [];
      return <div className="chips">{f.options?.map((o) => <button type="button" key={o} className={`chip${arr.includes(o) ? " is-on" : ""}`} onClick={() => setData(i, f.key, arr.includes(o) ? arr.filter((x) => x !== o) : [...arr, o])}>{o}</button>)}</div>;
    }
    return <input className={f.type === "number" ? "input input--mono" : "input"} inputMode={f.type === "number" ? "decimal" : undefined} placeholder={f.placeholder} value={String(v ?? "")} onChange={(e) => setData(i, f.key, f.type === "number" ? Number(e.target.value) || "" : e.target.value)} />;
  };

  const pendientes = useMemo(() => rows.filter((r) => r.status === "enviado").length, [rows]);

  return (
    <>
      <div className="page-head">
        <div><div className="meta">07 · Campo</div><h1 className="h1">Levantamientos</h1></div>
        <div className="page-head__actions">
          {allows("field.crear") && <button className="btn btn--crimson" onClick={() => setDraft(emptySurvey("cctv"))}><Icon d={I.plus} />Nuevo levantamiento</button>}
        </div>
      </div>

      {verCostos && pendientes > 0 && <div className="status-line" style={{ color: "var(--warn)" }}><i className="rec-dot" />{pendientes} levantamiento{pendientes !== 1 ? "s" : ""} esperando costeo</div>}

      <Card flush>
        {rows.length === 0 ? <Empty title="Todavía no hay levantamientos" hint="El técnico llena el levantamiento en sitio y administración lo convierte en cotización sin volver a escribir nada." /> : (
          <table className="table">
            <thead><tr><th>Número</th><th>Tipo</th><th>Cliente</th><th>Sitio</th><th className="num">Puntos</th><th>Técnico</th><th>Estado</th><th /></tr></thead>
            <tbody>{rows.map((s) => (
              <tr key={s.id}>
                <td className="mono muted">{s.number}</td><td>{s.kind_label}</td><td style={{ fontWeight: 600 }}>{s.customer || "—"}</td>
                <td className="muted" style={{ fontSize: 13 }}>{s.site || "—"}</td><td className="num mono">{s.points_count}</td><td className="muted">{s.technician || "—"}</td>
                <td><span className={`badge badge--${STATUS[s.status]?.tone || "muted"}`}>{STATUS[s.status]?.label || s.status}</span></td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => open(s.id)}>Abrir</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {draft && (
        <Modal
          title={draft.number ? `${draft.number} · ${spec?.label || draft.kind}` : "Nuevo levantamiento"}
          onClose={close}
          wide
          foot={<>
            {puedeCotizar && <button className="btn btn--soft" style={{ marginRight: "auto" }} onClick={() => cost()}><Icon d={I.trend} />Costear</button>}
            <button className="btn btn--ghost" onClick={close}>Cerrar</button>
            {!readOnly && <>
              <button className="btn btn--ghost" onClick={suggest} disabled={busy || !draft.points.length}>Sugerir materiales</button>
              <button className="btn btn--ghost" onClick={() => save()} disabled={busy}>Guardar</button>
              <button className="btn btn--crimson" onClick={send} disabled={busy || !draft.points.length}>Enviar a oficina</button>
            </>}
          </>}
        >
          <div className="grid-3">
            <Field label="Tipo de solución"><select className="select" value={draft.kind} disabled={!!draft.id} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>{Object.entries(specs).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}</select></Field>
            <Field label="Cliente"><Lookup value={draft.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setDraft({ ...draft, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Fecha de visita"><input className="input" type="date" value={draft.visit_date} onChange={(e) => setDraft({ ...draft, visit_date: e.target.value })} /></Field>
            <Field label="Sitio" hint="Dirección o referencia para llegar."><input className="input" value={draft.site} onChange={(e) => setDraft({ ...draft, site: e.target.value })} placeholder="Condominio Los Robles, Alajuela" /></Field>
            <Field label="Técnicos"><input className="input input--mono" type="number" min={1} value={draft.techs} onChange={(e) => setDraft({ ...draft, techs: Number(e.target.value) })} /></Field>
            <Field label="Días de trabajo" hint="Se usa para la mano de obra."><input className="input input--mono" inputMode="decimal" value={draft.days} onChange={(e) => setDraft({ ...draft, days: e.target.value })} /></Field>
          </div>

          <Card title={`${spec?.point_label || "Puntos"} · ${draft.points.length}`} extra={!readOnly && <button className="btn btn--soft btn--sm" onClick={addPoint}><Icon d={I.plus} />Agregar</button>}>
            {draft.points.length === 0 ? <p className="muted" style={{ fontSize: 13, margin: 0 }}>Agregá un punto por cada cámara, puerta, terminal o salida de red que haya que instalar: de ahí salen los materiales y el precio.</p> : draft.points.map((pt, i) => (
              <div className="point" key={pt.id ?? `n${i}`}>
                <div className="point__head">
                  <input className="input input--mono" style={{ maxWidth: 110 }} value={pt.code} onChange={(e) => setPoint(i, { code: e.target.value })} />
                  <input className="input" value={pt.label} placeholder="Entrada principal" onChange={(e) => setPoint(i, { label: e.target.value })} />
                  {!readOnly && <button className="btn btn--ghost btn--sm" title="Quitar" onClick={() => setDraft({ ...draft, points: draft.points.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button>}
                </div>
                <div className="point__grid">{(spec?.fields || []).map((f) => <Field key={f.key} label={f.unit ? `${f.label}` : f.label}>{field(f, i)}</Field>)}</div>
                <Field label="Observaciones"><input className="input" value={pt.notes || ""} onChange={(e) => setPoint(i, { notes: e.target.value })} placeholder="Hay que romper cielo raso…" /></Field>
              </div>
            ))}
          </Card>

          <Card title={`Materiales y equipos · ${draft.items.length}`} extra={!readOnly && <button className="btn btn--soft btn--sm" onClick={() => setDraft({ ...draft, items: [...draft.items, { product_id: null, name: "", quantity: "1", unit: "Unid", note: null }] })}><Icon d={I.plus} />Agregar</button>} flush>
            {draft.items.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>Sugerí los materiales desde los puntos o agregalos a mano. Administración pone los precios.</p> : (
              <table className="table">
                <thead><tr><th>Material</th><th>Origen</th><th className="num">Cantidad</th><th>Unidad</th><th /></tr></thead>
                <tbody>{draft.items.map((it, i) => (
                  <tr key={it.id ?? `n${i}`}>
                    <td><Lookup value={it.name} placeholder="Buscar en el catálogo…" fetcher={searchProducts} onSelect={(pr, text) => setItem(i, { product_id: pr ? pr.id : null, name: text })} /></td>
                    <td className="muted" style={{ fontSize: 12.5 }}>{it.product_id ? "Del catálogo" : "Texto libre"}</td>
                    <td className="num"><input className="input input--mono" inputMode="decimal" style={{ maxWidth: 110 }} value={it.quantity} onChange={(e) => setItem(i, { quantity: e.target.value })} /></td>
                    <td><input className="input" style={{ maxWidth: 90 }} value={it.unit} onChange={(e) => setItem(i, { unit: e.target.value })} /></td>
                    <td className="num">{!readOnly && <button className="btn btn--ghost btn--sm" onClick={() => setDraft({ ...draft, items: draft.items.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button>}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>

          <Field label="Notas del levantamiento"><textarea className="textarea" value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} placeholder="Acceso por el portón trasero; el cliente pide trabajar sábado." /></Field>
        </Modal>
      )}

      {costing && draft && (
        <Modal title={`Costeo · ${draft.number}`} onClose={() => setCosting(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setCosting(null)}>Cerrar</button>
          <button className="btn btn--crimson" onClick={toQuote}>Aprobar y generar cotización</button>
        </>}>
          {costing.missing_cost.length > 0 && (
            <p style={{ background: "var(--bad-soft, rgba(226,35,58,.1))", border: "1px solid var(--bad)", borderRadius: 10, padding: "10px 14px", fontSize: 13, color: "var(--bad)" }}>
              Sin costo de proveedor: {costing.missing_cost.slice(0, 4).join(", ")}{costing.missing_cost.length > 4 ? ` y ${costing.missing_cost.length - 4} más` : ""}. El margen real va a ser menor al que ves.
            </p>
          )}
          <Field label={`Margen objetivo · ${margin}%`} hint="Solo cambia el precio sugerido; el costo es el del proveedor.">
            <input type="range" min={0} max={80} step={1} value={margin} onChange={(e) => setMargin(Number(e.target.value))} onMouseUp={() => cost()} onTouchEnd={() => cost()} style={{ width: "100%" }} />
          </Field>
          <div className="eco" style={{ margin: "12px 0" }}>
            <div className="eco__box"><span className="meta">Costo total</span><b className="money">{fmtMoney(costing.cost_total)}</b></div>
            <div className="eco__box"><span className="meta">Precio sugerido</span><b className="money">{fmtMoney(costing.price_suggested)}</b></div>
            <div className="eco__box is-good"><span className="meta">Margen resultante</span><b>{Number(costing.margin_pct).toFixed(1)}%</b></div>
            <div className="eco__box"><span className="meta">Mano de obra + viáticos</span><b className="money">{fmtMoney(Number(costing.labor.cost) + Number(costing.labor.travel))}</b></div>
          </div>
          <table className="table">
            <thead><tr><th>Material</th><th className="num">Cant.</th><th className="num">Costo unit.</th><th className="num">Costo</th><th className="num">Precio</th></tr></thead>
            <tbody>
              {costing.lines.map((l, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600 }}>{l.name}{!l.has_cost && " "}{!l.has_cost && <span className="badge badge--warn" style={{ marginLeft: 6 }}>sin costo</span>}</td>
                  <td className="num mono">{Number(l.quantity)}</td><td className="num money">{fmtMoney(l.unit_cost)}</td><td className="num money">{fmtMoney(l.cost)}</td><td className="num money">{fmtMoney(l.price)}</td>
                </tr>
              ))}
              <tr>
                <td style={{ fontWeight: 600 }}>Instalación · {costing.labor.techs} {costing.labor.techs === 1 ? "técnico" : "técnicos"} × {Number(costing.labor.days)} {Number(costing.labor.days) === 1 ? "día" : "días"}</td>
                <td className="num mono">1</td><td className="num money">{fmtMoney(costing.labor.day_cost)}</td><td className="num money">{fmtMoney(costing.labor.cost)}</td><td className="num money">{fmtMoney(costing.labor.price)}</td>
              </tr>
            </tbody>
          </table>
        </Modal>
      )}
    </>
  );
}
