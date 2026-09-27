/* Levantamientos tecnicos: lo que el tecnico llena en sitio desde el celular y lo que administracion cotiza despues.
   El formulario no esta escrito aqui: lo dibuja /field/specs, asi agregar un tipo de solucion es tocar solo la API. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtMoney, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts } from "../../ui/Lookup";
import { PhotoStrip } from "../../ui/MediaPicker";
import { InviteTechButton } from "./InviteTech";

type FieldSpec = { key: string; label: string; type: "text" | "number" | "select" | "multi" | "bool"; options?: string[]; unit?: string; placeholder?: string };
type Spec = { label: string; point_prefix: string; point_label: string; fields: FieldSpec[]; materials: string[] };
type Point = { id?: number; code: string; label: string; data: Record<string, unknown>; photos: string[]; notes: string | null };
type Kind = "equipo" | "material";
type Item = { id?: number; product_id: number | null; name: string; quantity: string; unit: string; note: string | null; kind: Kind };
type LaborKey = "tecnico" | "civil" | "contratado";
type Labor = Record<LaborKey, { people: string; days: string }>;
type Tech = { id: number; name: string; role: string };
type Review = { roles: string[]; people: string[] };
type Survey = {
  id: number; number: string; kind: string; kind_label: string; status: string; customer_id: number | null; customer: string | null;
  site: string | null; opportunity_id: number | null; technician: string | null; visit_date: string | null; techs: number; days: string;
  notes: string | null; photos: string[]; quote_id: number | null; points_count: number; created_at: string;
  sent_at: string | null; sent_by: string | null; pending_review?: Review; visit_tech_ids: number[]; visit_techs: string[];
  labor: Record<LaborKey, { label: string; people: number; days: string | number }>;
  points?: Point[]; items?: (Omit<Item, "quantity"> & { quantity: string | number })[];
};
type CostLine = {
  item_id: number; product_id: number | null; name: string; kind: Kind; quantity: string; unit: string; unit_cost: string; cost: string;
  unit_price: string; price: string; has_cost: boolean; cost_source: "catalogo" | "levantamiento" | null; price_from_cost: boolean;
};
type LaborType = { key: LaborKey; label: string; people: number; days: string; day_cost: string; cost: string };
type Costing = {
  lines: CostLine[];
  labor: { techs: number; days: string; day_cost: string; types: LaborType[]; cost: string; transport: string; per_diem: string; viaticos: string; travel: string; price: string };
  cost_total: string; price_suggested: string; margin_pct: number; margin_target: number; missing_cost: string[]; can_save_catalog: boolean;
};
type Suggestion = {
  name: string; quantity: string | number; unit: string; kind: Kind; reason: string; action: "nuevo" | "sumar" | "cubierto" | "revisar";
  existing_quantity: string | number; add_quantity: string | number; item_id: number | null;
};
type SugRow = Suggestion & { checked: boolean; qty: string };

const STATUS: Record<string, { label: string; tone: string }> = {
  borrador: { label: "Borrador", tone: "warn" }, enviado: { label: "Enviado a oficina", tone: "info" },
  cotizado: { label: "Cotizado", tone: "ok" }, cerrado: { label: "Cerrado", tone: "muted" },
};
const LABOR: [LaborKey, string][] = [["tecnico", "Personal técnico"], ["civil", "Personal de obra civil"], ["contratado", "Personal contratado"]];
const KINDS: [Kind, string, string][] = [
  ["equipo", "Equipos", "Cámaras, grabadores, UPS, antenas, gabinetes: lo que se instala y tiene serie."],
  ["material", "Materiales", "Cable, tubo, placas, conectores: lo que se consume en la instalación."],
];
const ACTION: Record<Suggestion["action"], { label: string; tone: string }> = {
  nuevo: { label: "Nuevo", tone: "ok" }, sumar: { label: "Ya está: sumar", tone: "info" },
  cubierto: { label: "Ya alcanza", tone: "muted" }, revisar: { label: "Revisar cantidad", tone: "warn" },
};

const emptyLabor = (): Labor => ({ tecnico: { people: "2", days: "1" }, civil: { people: "0", days: "0" }, contratado: { people: "0", days: "0" } });
const emptySurvey = (kind: string) => ({
  kind, customer_id: "", customer_name: "", opportunity_id: "", site: "", visit_date: "", labor: emptyLabor(), visit_tech_ids: [] as number[],
  notes: "", photos: [] as string[], points: [] as Point[], items: [] as Item[],
});
type Draft = ReturnType<typeof emptySurvey> & {
  id?: number; number?: string; status?: string; quote_id?: number | null; sent_at?: string | null; sent_by?: string | null; pending_review?: Review;
};

const num = (v: string | number | null | undefined) => { const n = Number(String(v ?? "").replace(",", ".")); return Number.isFinite(n) ? n : 0; };
const when = (s: string | null | undefined) => (s ? parseTs(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
const reviewText = (r?: Review) => (r ? (r.people.length ? r.people.join(", ") : r.roles.join(" / ")) : "");
/* misma formula que services/pricing.sale_price: margen sobre la venta y redondeo hacia arriba a la centena */
const priceFrom = (cost: number, margin: number) => (margin >= 100 ? 0 : Math.ceil(cost / (1 - margin / 100) / 100) * 100);

export default function Surveys() {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [specs, setSpecs] = useState<Record<string, Spec>>({});
  const [techs, setTechs] = useState<Tech[]>([]);
  const [rows, setRows] = useState<Survey[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [costing, setCosting] = useState<Costing | null>(null);
  const [costEdits, setCostEdits] = useState<Record<number, { cost: string; save: boolean }>>({});
  const [sugs, setSugs] = useState<SugRow[] | null>(null);
  const [sent, setSent] = useState<{ number: string; notified: Tech[]; review?: Review } | null>(null);
  const [margin, setMargin] = useState(35);
  const [busy, setBusy] = useState(false);
  const prefilled = useRef<string | null>(null); // no rearmar el borrador si el usuario ya empezó a escribir

  const verCostos = allows("catalog.costos");
  const spec = draft ? specs[draft.kind] : undefined;

  const load = useCallback(() => api<Survey[]>("/surveys").then(setRows), []);
  const loadTechs = useCallback(() => api<Tech[]>("/field/technicians").then(setTechs).catch(() => setTechs([])), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api<Record<string, Spec>>("/field/specs").then(setSpecs);
    loadTechs();
  }, [loadTechs]);

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
    const labor = emptyLabor();
    for (const [k] of LABOR) {
      const l = s.labor?.[k];
      labor[k] = { people: String(l?.people ?? 0), days: String(Number(l?.days ?? 0)) };
    }
    setDraft({
      id: s.id, number: s.number, status: s.status, quote_id: s.quote_id, kind: s.kind, customer_id: s.customer_id ? String(s.customer_id) : "", customer_name: s.customer || "",
      opportunity_id: s.opportunity_id ? String(s.opportunity_id) : "", site: s.site || "", visit_date: s.visit_date || "", labor, visit_tech_ids: s.visit_tech_ids || [],
      notes: s.notes || "", photos: s.photos || [], points: s.points || [],
      items: (s.items || []).map((i) => ({ ...i, quantity: String(i.quantity), kind: i.kind === "equipo" ? "equipo" : "material" })),
      sent_at: s.sent_at, sent_by: s.sent_by, pending_review: s.pending_review,
    });
  };

  const close = () => { setDraft(null); setCosting(null); setCostEdits({}); if (params.get("id") || params.get("nuevo")) setParams({}); };

  const body = (d: Draft) => ({
    kind: d.kind, customer_id: d.customer_id ? Number(d.customer_id) : null, opportunity_id: d.opportunity_id ? Number(d.opportunity_id) : null,
    site: d.site || null, visit_date: d.visit_date || null, notes: d.notes || null, photos: d.photos, visit_tech_ids: d.visit_tech_ids,
    labor: Object.fromEntries(LABOR.map(([k]) => [k, { people: Math.max(0, Math.round(num(d.labor[k].people))), days: num(d.labor[k].days) }])),
    // los numeros del punto van tal cual (2,5 o 2.5): la API los valida y los guarda con decimales
    points: d.points.map((p) => ({ id: p.id, code: p.code, label: p.label || null, data: p.data, photos: p.photos || [], notes: p.notes })),
    items: d.items.filter((i) => i.name.trim()).map((i) => ({ id: i.id, product_id: i.product_id, name: i.name, quantity: num(i.quantity), unit: i.unit || "Unid", note: i.note, kind: i.kind })),
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

  /* Antes "Sugerir" reemplazaba la lista sin preguntar. Ahora la API solo propone (con el porqué) y aquí
     se elige qué agregar; lo que ya existe se suma en vez de duplicarse. */
  const suggest = async () => {
    const s = await save(true);
    if (!s) return;
    try {
      const list = await api<Suggestion[]>(`/surveys/${s.id}/suggest`, { method: "POST" });
      if (!list.length) { toast("No hay nada que sugerir todavía: agregá puntos primero."); return; }
      setSugs(list.map((x) => ({
        ...x,
        checked: x.action === "nuevo",
        qty: x.action === "revisar" ? "" : String(Number(x.action === "sumar" ? x.add_quantity : x.quantity) || ""),
      })));
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const applySugs = async () => {
    if (!draft?.id || !sugs) return;
    const chosen = sugs.filter((x) => x.checked && num(x.qty) > 0);
    if (!chosen.length) { setSugs(null); return; }
    try {
      const r = await api<{ added: number; summed: number }>(`/surveys/${draft.id}/suggest/apply`, {
        method: "POST",
        json: chosen.map((x) => ({ name: x.name, quantity: num(x.qty), unit: x.unit, kind: x.kind })),
      });
      setSugs(null);
      await open(draft.id);
      toast(`${r.added} agregado${r.added !== 1 ? "s" : ""}${r.summed ? ` · ${r.summed} con cantidad sumada` : ""}. Nada de lo anterior se borró.`);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const send = async () => {
    const s = await save(true);
    if (!s) return;
    try {
      const r = await api<Survey & { notified: Tech[] }>(`/surveys/${s.id}/send`, { method: "POST" });
      setSent({ number: r.number, notified: r.notified || [], review: r.pending_review });
      close(); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const cost = async (m = margin) => {
    const s = await save(true); // el costeo lee de la base: lo que está en pantalla tiene que estar guardado
    if (!s?.id) return;
    try { setCosting(await api<Costing>(`/surveys/${s.id}/costing?margin=${m}`)); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const pendingCosts = Object.entries(costEdits).filter(([, v]) => v.cost.trim() !== "");
  const saveCosts = async () => {
    if (!draft?.id || !pendingCosts.length) return true;
    try {
      const r = await api<Costing>(`/surveys/${draft.id}/costs?margin=${margin}`, {
        method: "POST",
        json: pendingCosts.map(([id, v]) => ({ item_id: Number(id), unit_cost: num(v.cost), save_to_catalog: v.save })),
      });
      setCosting(r); setCostEdits({});
      toast(pendingCosts.some(([, v]) => v.save) ? "Costos guardados (y en el catálogo)" : "Costos guardados en este levantamiento");
      return true;
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return false; }
  };

  const toQuote = async () => {
    if (!draft?.id) return;
    if (!(await saveCosts())) return;
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
  /* En sitio, las cámaras de un mismo tramo comparten casi todo: se copia la anterior y solo se
     corrige lo que cambia (lo pidió Andrés: "que utilice la misma configuración para estas cámaras"). */
  const dupPoint = (i: number) => setDraft((d) => {
    if (!d) return d;
    const base = d.points[i];
    const pre = specs[d.kind]?.point_prefix || "PTO";
    const copia: Point = { code: `${pre}-${String(d.points.length + 1).padStart(2, "0")}`, label: base.label, data: { ...base.data }, photos: [], notes: null };
    return { ...d, points: [...d.points.slice(0, i + 1), copia, ...d.points.slice(i + 1)] };
  });
  const setPoint = (i: number, patch: Partial<Point>) => setDraft((d) => (d ? { ...d, points: d.points.map((p, j) => (j === i ? { ...p, ...patch } : p)) } : d));
  const setData = (i: number, key: string, v: unknown) => setPoint(i, { data: { ...draft!.points[i].data, [key]: v } });
  const setItem = (i: number, patch: Partial<Item>) => setDraft((d) => (d ? { ...d, items: d.items.map((x, j) => (j === i ? { ...x, ...patch } : x)) } : d));
  const setLabor = (k: LaborKey, patch: Partial<Labor[LaborKey]>) => setDraft((d) => (d ? { ...d, labor: { ...d.labor, [k]: { ...d.labor[k], ...patch } } } : d));
  const toggleVisit = (id: number) => setDraft((d) => (d ? { ...d, visit_tech_ids: d.visit_tech_ids.includes(id) ? d.visit_tech_ids.filter((x) => x !== id) : [...d.visit_tech_ids, id] } : d));

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
    /* Medidas con decimales (altura 2.5 m). Antes Number(valor) || "" se comía el punto al teclear "2.", y un
       type="number" sin step marcaba 2.5 como inválido. Tampoco sirve type="number" con step="any": en un teclado
       en español la coma ("2,5") deja el valor vacío y se borra lo escrito. Texto con teclado decimal y el valor tal
       cual; la API acepta punto o coma, valida y guarda el número con decimales. */
    if (f.type === "number") {
      const bad = v !== undefined && v !== null && v !== "" && !/^\d*([.,]\d*)?$/.test(String(v).trim());
      return <input className="input input--mono" inputMode="decimal" pattern="[0-9]*[.,]?[0-9]*" aria-invalid={bad} style={bad ? { borderColor: "var(--bad)" } : undefined} placeholder={f.unit ? `0 ${f.unit}` : undefined} value={String(v ?? "")} onChange={(e) => setData(i, f.key, e.target.value)} />;
    };
    return <input className="input" placeholder={f.placeholder} value={String(v ?? "")} onChange={(e) => setData(i, f.key, e.target.value)} />;
  };

  const pendientes = useMemo(() => rows.filter((r) => r.status === "enviado").length, [rows]);

  /* ---------- costeo en vivo: el costo escrito recalcula precio y margen antes de guardarlo ---------- */
  const live = useMemo(() => {
    if (!costing) return null;
    const lines = costing.lines.map((l) => {
      const ed = costEdits[l.item_id];
      if (!ed || ed.cost.trim() === "") return { ...l, edited: false, c: num(l.cost), p: num(l.price), uc: num(l.unit_cost), up: num(l.unit_price) };
      const uc = num(ed.cost);
      const up = l.price_from_cost ? priceFrom(uc, margin) : num(l.unit_price);
      const q = num(l.quantity);
      return { ...l, edited: true, has_cost: uc > 0, c: uc * q, p: up * q, uc, up };
    });
    const labor = num(costing.labor.cost) + num(costing.labor.travel);
    const costTotal = lines.reduce((a, l) => a + l.c, 0) + labor;
    const price = lines.reduce((a, l) => a + l.p, 0) + num(costing.labor.price);
    const group = (k: Kind) => lines.filter((l) => l.kind === k);
    return {
      lines, costTotal, price, margin: price > 0 ? ((price - costTotal) / price) * 100 : 0,
      missing: lines.filter((l) => !l.has_cost).map((l) => l.name), groups: KINDS.map(([k, label]) => ({ k, label, lines: group(k) })),
    };
  }, [costing, costEdits, margin]);

  const itemsBlock = (kind: Kind, title: string, hint: string) => {
    const idx = draft!.items.map((it, i) => ({ it, i })).filter(({ it }) => it.kind === kind);
    return (
      <Card key={kind} title={`${title} · ${idx.length}`} extra={!readOnly && <button className="btn btn--soft btn--sm" onClick={() => setDraft({ ...draft!, items: [...draft!.items, { product_id: null, name: "", quantity: "1", unit: kind === "material" ? "m" : "Unid", note: null, kind }] })}><Icon d={I.plus} />Agregar</button>} flush>
        {idx.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: "0 18px 14px" }}>{hint}</p> : (
          <table className="table">
            <thead><tr><th>{kind === "equipo" ? "Equipo" : "Material"}</th><th>Origen</th><th className="num">Cantidad</th><th>Unidad</th><th>Bloque</th><th /></tr></thead>
            <tbody>{idx.map(({ it, i }) => (
              <tr key={it.id ?? `n${i}`}>
                <td><Lookup value={it.name} placeholder="Buscar en el catálogo…" fetcher={searchProducts} disabled={readOnly} onSelect={(pr, text) => setItem(i, { product_id: pr ? pr.id : null, name: text })} /></td>
                <td className="muted" style={{ fontSize: 12.5 }}>{it.product_id ? "Del catálogo" : "Texto libre"}</td>
                <td className="num"><input className="input input--mono" inputMode="decimal" style={{ maxWidth: 110 }} value={it.quantity} disabled={readOnly} onChange={(e) => setItem(i, { quantity: e.target.value })} /></td>
                <td><input className="input" style={{ maxWidth: 90 }} value={it.unit} disabled={readOnly} onChange={(e) => setItem(i, { unit: e.target.value })} /></td>
                <td><select className="select select--sm" value={it.kind} disabled={readOnly} onChange={(e) => setItem(i, { kind: e.target.value as Kind })} title="Mover a Equipos o Materiales"><option value="equipo">Equipo</option><option value="material">Material</option></select></td>
                <td className="num">{!readOnly && <button className="btn btn--ghost btn--sm" onClick={() => setDraft({ ...draft!, items: draft!.items.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    );
  };

  const sentLine = (s: { sent_at?: string | null; sent_by?: string | null; pending_review?: Review; status?: string }) =>
    s.status === "enviado" && s.sent_at ? `${when(s.sent_at)}${s.sent_by ? ` · por ${s.sent_by}` : ""}${s.pending_review ? ` · pendiente de revisión por ${reviewText(s.pending_review)}` : ""}` : "";

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
                <td>
                  <span className={`badge badge--${STATUS[s.status]?.tone || "muted"}`}>{STATUS[s.status]?.label || s.status}</span>
                  {sentLine(s) && <div className="meta" style={{ marginTop: 4, textTransform: "none", letterSpacing: 0 }}>{sentLine(s)}</div>}
                </td>
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
              <button className="btn btn--crimson" onClick={send} disabled={busy || !draft.points.length}>{draft.status === "enviado" ? "Reenviar a oficina" : "Enviar a oficina"}</button>
            </>}
          </>}
        >
          {draft.status === "enviado" && (
            <p style={{ background: "var(--info-soft, rgba(60,130,240,.1))", border: "1px solid var(--info, #3c82f0)", borderRadius: 10, padding: "10px 14px", fontSize: 13, margin: 0 }}>
              <b>Enviado a oficina</b> · {sentLine(draft)}
            </p>
          )}
          <div className="grid-3">
            <Field label="Tipo de solución"><select className="select" value={draft.kind} disabled={!!draft.id} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>{Object.entries(specs).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}</select></Field>
            <Field label="Cliente"><Lookup value={draft.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setDraft({ ...draft, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Fecha de visita"><input className="input" type="date" value={draft.visit_date} onChange={(e) => setDraft({ ...draft, visit_date: e.target.value })} /></Field>
          </div>
          <Field label="Sitio" hint="Dirección o referencia para llegar."><input className="input" value={draft.site} onChange={(e) => setDraft({ ...draft, site: e.target.value })} placeholder="Condominio Los Robles, Alajuela" /></Field>

          <Field label="Técnicos que hicieron la visita" hint="Solo informativo: quién fue al sitio. No se usa para costear.">
            <div className="chips" style={{ alignItems: "center" }}>
              {techs.length === 0 && <span className="muted" style={{ fontSize: 13 }}>No hay técnicos registrados.</span>}
              {techs.map((t) => <button type="button" key={t.id} disabled={readOnly} className={`chip${draft.visit_tech_ids.includes(t.id) ? " is-on" : ""}`} onClick={() => toggleVisit(t.id)}>{t.name}</button>)}
              {!readOnly && <InviteTechButton />}
            </div>
          </Field>

          <Card title="Personal requerido para la obra" flush>
            <p className="muted" style={{ fontSize: 12.5, padding: "0 18px", margin: "0 0 6px" }}>Cuántas personas y cuántos días necesita la instalación. De aquí salen la mano de obra y los viáticos del costeo.</p>
            <table className="table">
              <thead><tr><th>Tipo</th><th className="num">Personas</th><th className="num">Días</th></tr></thead>
              <tbody>{LABOR.map(([k, label]) => (
                <tr key={k}>
                  <td style={{ fontWeight: 600 }}>{label}</td>
                  <td className="num"><input className="input input--mono" type="number" min={0} step={1} inputMode="numeric" style={{ maxWidth: 100 }} disabled={readOnly} value={draft.labor[k].people} onChange={(e) => setLabor(k, { people: e.target.value })} /></td>
                  <td className="num"><input className="input input--mono" inputMode="decimal" style={{ maxWidth: 100 }} disabled={readOnly} value={draft.labor[k].days} onChange={(e) => setLabor(k, { days: e.target.value })} /></td>
                </tr>
              ))}</tbody>
            </table>
          </Card>

          <Card title={`${spec?.point_label || "Puntos"} · ${draft.points.length}`} extra={!readOnly && <button className="btn btn--soft btn--sm" onClick={addPoint}><Icon d={I.plus} />Agregar</button>}>
            {draft.points.length === 0 ? <p className="muted" style={{ fontSize: 13, margin: 0 }}>Agregá un punto por cada cámara, puerta, terminal o salida de red que haya que instalar: de ahí salen los materiales y el precio.</p> : draft.points.map((pt, i) => (
              <div className="point" key={pt.id ?? `n${i}`}>
                <div className="point__head">
                  <input className="input input--mono" style={{ maxWidth: 110 }} value={pt.code} onChange={(e) => setPoint(i, { code: e.target.value })} />
                  <input className="input" value={pt.label} placeholder="Entrada principal" onChange={(e) => setPoint(i, { label: e.target.value })} />
                  {!readOnly && <button className="btn btn--ghost btn--sm" title="Duplicar: mismas características, otro punto" onClick={() => dupPoint(i)}><Icon d={I.copy} size={14} /></button>}
                  {!readOnly && <button className="btn btn--ghost btn--sm" title="Quitar" onClick={() => setDraft({ ...draft, points: draft.points.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button>}
                </div>
                <div className="point__grid">{(spec?.fields || []).map((f) => <Field key={f.key} label={f.label}>{field(f, i)}</Field>)}</div>
                <Field label="Observaciones" hint="También se usan para sugerir materiales (cielo raso, poste, concreto…)."><input className="input" value={pt.notes || ""} onChange={(e) => setPoint(i, { notes: e.target.value })} placeholder="Hay que romper cielo raso…" /></Field>
                <Field label="Fotografías del punto" hint="En el celular abre la cámara directo.">
                  <PhotoStrip value={pt.photos || []} onChange={(photos) => setPoint(i, { photos })} disabled={readOnly} label="Foto" />
                </Field>
              </div>
            ))}
          </Card>

          {KINDS.map(([k, title, hint]) => itemsBlock(k, title, draft.items.length === 0 ? `${hint} Sugerilos desde los puntos o agregalos a mano; administración pone los precios.` : hint))}

          <Field label="Fotografías generales del sitio" hint="Fachada, gabinete, ruta del cable: lo que ayude a cotizar sin volver.">
            <PhotoStrip value={draft.photos} onChange={(photos) => setDraft({ ...draft, photos })} disabled={readOnly} />
          </Field>
          <Field label="Notas del levantamiento"><textarea className="textarea" value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} placeholder="Acceso por el portón trasero; el cliente pide trabajar sábado." /></Field>
        </Modal>
      )}

      {sugs && draft && (
        <Modal title="Materiales y equipos sugeridos" onClose={() => setSugs(null)} wide foot={<>
          <span className="muted" style={{ marginRight: "auto", fontSize: 13 }}>{sugs.filter((x) => x.checked && num(x.qty) > 0).length} para agregar · lo que ya cargaste no se borra</span>
          <button className="btn btn--ghost" onClick={() => setSugs(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={applySugs}>Agregar seleccionados</button>
        </>}>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Salen de los puntos levantados, sus observaciones y lo que ya está en la lista. Marcá lo que querés agregar y ajustá la cantidad; si el material ya existe se suma a esa línea.</p>
          <table className="table">
            <thead><tr><th style={{ width: 32 }} /><th>Sugerencia</th><th>Bloque</th><th className="num">Ya cargado</th><th className="num">Agregar</th><th>Por qué</th></tr></thead>
            <tbody>{sugs.map((x, i) => (
              <tr key={x.name} style={{ opacity: x.action === "cubierto" && !x.checked ? 0.6 : 1 }}>
                <td><input type="checkbox" checked={x.checked} onChange={(e) => setSugs(sugs.map((y, j) => (j === i ? { ...y, checked: e.target.checked } : y)))} /></td>
                <td><b>{x.name}</b> <span className={`badge badge--${ACTION[x.action].tone}`} style={{ marginLeft: 6 }}>{ACTION[x.action].label}</span></td>
                <td className="muted" style={{ textTransform: "capitalize" }}>{x.kind}</td>
                <td className="num mono">{Number(x.existing_quantity) ? Number(x.existing_quantity) : "—"}</td>
                <td className="num"><input className="input input--mono" inputMode="decimal" style={{ maxWidth: 90 }} value={x.qty} placeholder="0" onChange={(e) => setSugs(sugs.map((y, j) => (j === i ? { ...y, qty: e.target.value, checked: num(e.target.value) > 0 ? true : y.checked } : y)))} /> <span className="muted" style={{ fontSize: 12 }}>{x.unit}</span></td>
                <td className="muted" style={{ fontSize: 12.5 }}>{x.reason}</td>
              </tr>
            ))}</tbody>
          </table>
        </Modal>
      )}

      {sent && (
        <Modal title={`${sent.number} enviado a oficina`} onClose={() => setSent(null)} foot={<button className="btn btn--crimson" onClick={() => setSent(null)}>Entendido</button>}>
          {sent.notified.length ? (
            <>
              <p style={{ margin: 0, fontSize: 14 }}>Le avisamos por correo a:</p>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14 }}>{sent.notified.map((n) => <li key={n.id}><b>{n.name}</b> <span className="muted">· {n.role}</span></li>)}</ul>
            </>
          ) : <p style={{ margin: 0, fontSize: 14, color: "var(--warn)" }}>No hay usuarios activos con rol {sent.review?.roles.join(" / ") || "revisor"} para avisar. Avisale a la oficina por otro medio.</p>}
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>Queda pendiente de revisión por {sent.review ? sent.review.roles.join(" / ") : "administración"}. Lo ves en la lista con el estado "Enviado a oficina".</p>
        </Modal>
      )}

      {costing && live && draft && (
        <Modal title={`Costeo · ${draft.number}`} onClose={() => { setCosting(null); setCostEdits({}); }} wide foot={<>
          {pendingCosts.length > 0 && <button className="btn btn--soft" style={{ marginRight: "auto" }} onClick={saveCosts}>Guardar costos ({pendingCosts.length})</button>}
          <button className="btn btn--ghost" onClick={() => { setCosting(null); setCostEdits({}); }}>Cerrar</button>
          <button className="btn btn--crimson" onClick={toQuote}>Aprobar y generar cotización</button>
        </>}>
          {live.missing.length > 0 && (
            <p style={{ background: "var(--bad-soft, rgba(226,35,58,.1))", border: "1px solid var(--bad)", borderRadius: 10, padding: "10px 14px", fontSize: 13, color: "var(--bad)" }}>
              Sin costo de proveedor: {live.missing.slice(0, 4).join(", ")}{live.missing.length > 4 ? ` y ${live.missing.length - 4} más` : ""}. Escribí el costo en la línea; mientras tanto el margen real va a ser menor al que ves.
            </p>
          )}
          <Field label={`Margen objetivo · ${margin}%`} hint="Solo cambia el precio sugerido; el costo es el del proveedor.">
            <input type="range" min={0} max={80} step={1} value={margin} onChange={(e) => setMargin(Number(e.target.value))} onMouseUp={() => cost()} onTouchEnd={() => cost()} style={{ width: "100%" }} />
          </Field>
          <div className="eco" style={{ margin: "12px 0" }}>
            <div className="eco__box"><span className="meta">Costo total</span><b className="money">{fmtMoney(live.costTotal)}</b></div>
            <div className="eco__box"><span className="meta">Precio sugerido</span><b className="money">{fmtMoney(live.price)}</b></div>
            <div className="eco__box is-good"><span className="meta">Margen resultante</span><b>{live.margin.toFixed(1)}%</b></div>
            <div className="eco__box"><span className="meta">Mano de obra + viáticos</span><b className="money">{fmtMoney(num(costing.labor.cost) + num(costing.labor.travel))}</b></div>
          </div>
          <table className="table">
            <thead><tr><th>Concepto</th><th className="num">Cant.</th><th className="num">Costo unit.</th><th className="num">Costo</th><th className="num">Precio</th></tr></thead>
            <tbody>
              {live.groups.filter((g) => g.lines.length).map((g) => (
                <GroupRows key={g.k} label={g.label} cost={g.lines.reduce((a, l) => a + l.c, 0)} price={g.lines.reduce((a, l) => a + l.p, 0)}>
                  {g.lines.map((l) => {
                    const editable = !costing.lines.find((x) => x.item_id === l.item_id)?.has_cost || l.cost_source === "levantamiento";
                    const ed = costEdits[l.item_id];
                    return (
                      <tr key={l.item_id}>
                        <td style={{ fontWeight: 600 }}>
                          {l.name}
                          {!l.has_cost && <span className="badge badge--warn" style={{ marginLeft: 6 }}>sin costo</span>}
                          {l.cost_source === "levantamiento" && !l.edited && <span className="badge badge--muted" style={{ marginLeft: 6 }}>costo escrito aquí</span>}
                          {editable && l.product_id && costing.can_save_catalog && (
                            <label className="muted" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, fontWeight: 400, marginTop: 4 }}>
                              <input type="checkbox" checked={!!ed?.save} onChange={(e) => setCostEdits({ ...costEdits, [l.item_id]: { cost: ed?.cost ?? (l.has_cost ? String(l.uc) : ""), save: e.target.checked } })} />
                              Guardar este costo en el catálogo
                            </label>
                          )}
                        </td>
                        <td className="num mono">{Number(l.quantity)}</td>
                        <td className="num">
                          {editable
                            ? <input className="input input--mono" inputMode="decimal" style={{ maxWidth: 120, marginLeft: "auto" }} placeholder="₡ costo" value={ed?.cost ?? (l.has_cost ? String(l.uc) : "")} onChange={(e) => setCostEdits({ ...costEdits, [l.item_id]: { cost: e.target.value, save: ed?.save ?? false } })} />
                            : <span className="money">{fmtMoney(l.uc)}</span>}
                        </td>
                        <td className="num money">{fmtMoney(l.c)}</td><td className="num money">{fmtMoney(l.p)}</td>
                      </tr>
                    );
                  })}
                </GroupRows>
              ))}
              <GroupRows label="Mano de obra y viáticos" cost={num(costing.labor.cost) + num(costing.labor.travel)} price={num(costing.labor.price)}>
                {costing.labor.types.filter((t) => t.people > 0).map((t) => (
                  <tr key={t.key}>
                    <td style={{ fontWeight: 600 }}>{t.label} · {t.people} {t.people === 1 ? "persona" : "personas"} × {Number(t.days)} {Number(t.days) === 1 ? "día" : "días"}</td>
                    <td className="num mono">{t.people * Number(t.days)}</td><td className="num money">{fmtMoney(t.day_cost)}</td><td className="num money">{fmtMoney(t.cost)}</td><td className="num muted">—</td>
                  </tr>
                ))}
                <tr><td style={{ fontWeight: 600 }}>Transporte</td><td className="num mono">1</td><td className="num money">{fmtMoney(costing.labor.transport)}</td><td className="num money">{fmtMoney(costing.labor.transport)}</td><td className="num muted">—</td></tr>
                {num(costing.labor.viaticos) > 0 && <tr><td style={{ fontWeight: 600 }}>Viáticos (alimentación)</td><td className="num mono">—</td><td className="num money">{fmtMoney(costing.labor.per_diem)}</td><td className="num money">{fmtMoney(costing.labor.viaticos)}</td><td className="num muted">—</td></tr>}
              </GroupRows>
            </tbody>
          </table>
        </Modal>
      )}
    </>
  );
}

/* Encabezado de bloque del costeo (Equipos / Materiales / Mano de obra) con su subtotal. */
function GroupRows({ label, cost, price, children }: { label: string; cost: number; price: number; children: React.ReactNode }) {
  return (
    <>
      <tr style={{ background: "var(--surface-2, rgba(127,127,127,.06))" }}>
        <td colSpan={3} className="meta" style={{ fontWeight: 700 }}>{label}</td>
        <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(cost)}</td><td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(price)}</td>
      </tr>
      {children}
    </>
  );
}
