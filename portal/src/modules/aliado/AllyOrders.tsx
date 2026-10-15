/* Órdenes de trabajo del aliado (14:35-14:48): las de su empresa ("sus chiguines" las ejecutan con la app). Flujo del
   técnico desde el celular: Llegué · Iniciar · Guardar avance · Finalizar, con tareas, material usado, fotos y firma.
   El aliado además reasigna entre su equipo. Crear, editar o cancelar órdenes es de Crimson. Sin precios. */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Empty, Field, I, Icon } from "../../ui/components";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import { PhotoStrip } from "../../ui/MediaPicker";
import { ALLY_FIELD_API } from "../field/fieldApi";
import { errorText, fmtWhen, useAlly, type Material, type OrderRow, type Tech } from "./shared";

function OrderList() {
  const [estado, setEstado] = useState<"pendientes" | "finalizadas" | "todas">("pendientes");
  const [rows, setRows] = useState<OrderRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setRows(null); api<OrderRow[]>(`${ALLY_FIELD_API.orders}?estado=${estado}`).then(setRows).catch((e) => setErr(errorText(e))); }, [estado]);
  return (
    <>
      <div className="cp-h"><h1 className="h1">Órdenes de trabajo</h1></div>
      <div className="cp-seg">
        {(["pendientes", "finalizadas", "todas"] as const).map((k) => <button key={k} className={estado === k ? "is-on" : ""} onClick={() => setEstado(k)}>{k[0].toUpperCase() + k.slice(1)}</button>)}
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <div className="cp-list">
          {rows.map((o) => (
            <Link key={o.id} to={`/ordenes/${o.id}`} className="cp-row">
              <div className="cp-row__main">
                <b>{o.number} · {o.title}</b>
                <div className="cp-row__meta">{o.customer || "—"}{o.project ? ` · ${o.project}` : ""}{o.site ? ` · ${o.site}` : ""}</div>
                <div className="cp-row__meta">{o.technician || "Sin técnico"} · {o.scheduled_at ? fmtWhen(o.scheduled_at) : "Sin fecha"}</div>
              </div>
              <div className="cp-row__side"><Badge status={o.status} /></div>
            </Link>
          ))}
        </div>
      ) : <Empty title="No hay órdenes aquí" hint="Las órdenes que Crimson asigna a su empresa aparecen en esta lista." />}
    </>
  );
}

function OrderDetail({ id }: { id: number }) {
  const { toast } = useSession();
  const { readOnly, can } = useAlly();
  const [o, setO] = useState<OrderRow | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [mats, setMats] = useState<Material[]>([]);
  const [tasks, setTasks] = useState<{ text: string; done: boolean }[]>([]);
  const [photos, setPhotos] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [firma, setFirma] = useState("");
  const [techs, setTechs] = useState<Tech[]>([]);
  const [busy, setBusy] = useState(false);
  const take = (x: OrderRow) => { setO(x); setMats(x.materials || []); setTasks(x.tasks || []); setPhotos(x.photos || []); setNotes(x.notes || ""); setFirma(x.customer_signature || ""); };
  const load = useCallback(() => { api<OrderRow>(ALLY_FIELD_API.order(id)).then(take).catch((e) => setErr(errorText(e))); }, [id]);
  useEffect(load, [load]);
  useEffect(() => { if (can("asignar")) api<Tech[]>(ALLY_FIELD_API.technicians).then(setTechs).catch(() => setTechs([])); }, [can]);
  const productos = useCallback(async (q: string): Promise<LookupItem[]> => {
    const r = await api<{ id: number; code: string; name: string }[]>(`${ALLY_FIELD_API.productSearch}?q=${encodeURIComponent(q)}`);
    return r.map((p) => ({ id: p.id, label: p.name, hint: p.code }));
  }, []);
  if (err) return <p className="muted">{err}</p>;
  if (!o) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const act = o.can_act && !readOnly;
  const accion = async (a: "arrive" | "start" | "progress" | "finish") => {
    setBusy(true);
    try {
      const body = { tasks, photos, notes, customer_signature: firma || null, materials: mats.map((m) => ({ id: m.id || undefined, product_id: m.product_id, name: m.name, quantity: m.quantity || 0, unit: m.unit || "Unid", planned: m.planned || 0 })) };
      take(await api<OrderRow>(ALLY_FIELD_API.orderAction(id, a), { method: "POST", json: body }));
      toast(a === "finish" ? "Orden finalizada" : "Guardado");
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
    finally { setBusy(false); }
  };
  const asignar = async (tid: number) => {
    try { take(await api<OrderRow>(`/aliado/ordenes/${id}/asignar`, { method: "PATCH", json: { technician_id: tid, helpers: o.helpers.filter((h) => h !== tid) } })); toast("Orden reasignada"); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo reasignar", "bad"); }
  };
  const setMat = (i: number, patch: Partial<Material>) => setMats(mats.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  return (
    <>
      <div className="cp-h">
        <div><div className="meta">{o.number} · {o.customer || "—"}{o.project ? ` · ${o.project}` : ""}</div><h1 className="h1">{o.title}</h1></div>
        <Badge status={o.status} />
      </div>
      <div className="cp-card">
        <dl className="al-kv">
          <dt>Sitio</dt><dd>{o.site || "—"}</dd>
          <dt>Fecha</dt><dd>{o.scheduled_at ? fmtWhen(o.scheduled_at) : "Sin fecha"}</dd>
          <dt>Equipo</dt><dd>{o.crew.map((c) => c.name).filter(Boolean).join(", ") || "Sin técnico"}</dd>
          {o.running.length > 0 && <><dt>Trabajando</dt><dd>{o.running.map((r) => r.user).join(", ")}</dd></>}
        </dl>
        {can("asignar") && act && techs.length > 0 && (
          <Field label="Asignar técnico">
            <select className="select" value={o.technician_id ?? ""} onChange={(e) => e.target.value && asignar(Number(e.target.value))}>
              <option value="">Elegí…</option>
              {techs.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
        )}
      </div>
      {act && (
        <div className="al-actions" style={{ margin: "12px 0" }}>
          {o.status === "asignada" && <button className="btn btn--soft" disabled={busy} onClick={() => accion("arrive")}><Icon d={I.check} />Llegué</button>}
          {o.status !== "en_proceso" && <button className="btn btn--soft" disabled={busy} onClick={() => accion("start")}><Icon d={I.clock} />Iniciar</button>}
          <button className="btn btn--soft" disabled={busy} onClick={() => accion("progress")}><Icon d={I.check} />Guardar avance</button>
          <button className="btn btn--crimson" disabled={busy} onClick={() => accion("finish")}><Icon d={I.check} />Finalizar</button>
        </div>
      )}
      <h2 className="h3 al-sec">Tareas</h2>
      {tasks.length ? (
        <div className="cp-list">
          {tasks.map((t, i) => (
            <label key={i} className="cp-row" style={{ alignItems: "center" }}>
              <input type="checkbox" disabled={!act} checked={t.done} onChange={(e) => setTasks(tasks.map((x, j) => (j === i ? { ...x, done: e.target.checked } : x)))} />
              <span className="cp-row__main">{t.text}</span>
            </label>
          ))}
        </div>
      ) : <p className="muted">Sin tareas.</p>}
      <h2 className="h3 al-sec">Material usado</h2>
      <div className="al-lines">
        {mats.map((m, i) => (
          <div className="al-line" key={`${m.id}-${i}`}>
            <Field label="Material">{m.id ? <b>{m.name}</b> : <Lookup value={m.name} disabled={!act} fetcher={productos} placeholder="Buscar en el catálogo" onSelect={(sel, text) => setMat(i, sel ? { product_id: sel.id, name: sel.label } : { product_id: null, name: text })} />}</Field>
            <Field label="Planificado"><span className="input input--mono" style={{ display: "block" }}>{Number(m.planned || 0)}</span></Field>
            <Field label="Usado"><input className="input input--mono" inputMode="decimal" disabled={!act} value={String(m.quantity)} onChange={(e) => setMat(i, { quantity: e.target.value })} /></Field>
            <Field label="Unidad"><span className="input" style={{ display: "block" }}>{m.unit}</span></Field>
            {act && <button type="button" className="btn btn--ghost btn--icon" aria-label="Quitar" onClick={() => setMats(mats.filter((_, j) => j !== i))}><Icon d={I.x} /></button>}
          </div>
        ))}
        {act && <button type="button" className="btn btn--soft btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setMats([...mats, { id: 0, product_id: null, name: "", quantity: "1", unit: "Unid", planned: "0" }])}><Icon d={I.plus} size={14} />Agregar material</button>}
      </div>
      <h2 className="h3 al-sec">Evidencia</h2>
      <PhotoStrip value={photos} disabled={!act} uploadPath={ALLY_FIELD_API.mediaUpload} caption={o.number} onChange={setPhotos} />
      <div className="cp-form" style={{ marginTop: 12 }}>
        <Field label="Notas"><textarea className="textarea" rows={3} disabled={!act} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <Field label="Recibe conforme (nombre)"><input className="input" disabled={!act} value={firma} maxLength={160} onChange={(e) => setFirma(e.target.value)} /></Field>
      </div>
    </>
  );
}

export default function AllyOrders() {
  const { id } = useParams();
  return id ? <OrderDetail id={Number(id)} /> : <OrderList />;
}
