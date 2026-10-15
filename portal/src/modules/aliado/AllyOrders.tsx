/* Órdenes de trabajo del aliado (14:35-14:48): las de su empresa ("sus chiguines" las ejecutan con la app). Flujo del
   técnico desde el celular: Llegué · Iniciar jornada · Terminar jornada · Guardar avance · Finalizar, con tareas,
   material usado, fotos y firma. El aliado además reasigna entre su equipo. Crear, editar o cancelar órdenes es de
   Crimson. Sin precios.
   Cada botón manda SOLO lo que cambió desde la última respuesta del servidor (como el panel interno): el material que
   ya existe va por id (quantities / removed) y el nuevo en "agregados". Mandar todo pisaría lo que Crimson u otro
   técnico anotaron mientras tanto. Si quedó tiempo corriendo de más (se olvidaron de terminar la jornada), se indica
   la hora real en que terminaron. */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Empty, Field, I, Icon } from "../../ui/components";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import { PhotoStrip } from "../../ui/MediaPicker";
import { ALLY_FIELD_API } from "../field/fieldApi";
import { errorText, fmtWhen, useAlly, type Material, type OrderRow, type Tech } from "./shared";

const num = (v: string | number | null | undefined) => Number(String(v ?? "").replace(",", ".")) || 0;
/* Costa Rica es UTC-6 todo el año: la hora se escribe en hora de aquí aunque el teléfono tenga mal la zona. */
const toISO = (local: string) => new Date(`${local}:00-06:00`).toISOString();

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
  const [finJornada, setFinJornada] = useState("");
  const take = (x: OrderRow) => { setO(x); setMats(x.materials || []); setTasks(x.tasks || []); setPhotos(x.photos || []); setNotes(x.notes || ""); setFirma(x.customer_signature || ""); setFinJornada(""); };
  const load = useCallback(() => { api<OrderRow>(ALLY_FIELD_API.order(id)).then(take).catch((e) => setErr(errorText(e))); }, [id]);
  useEffect(load, [load]);
  // su equipo activo (el técnico: solo él): para saber si el tiempo que corre es de su gente y para reasignar
  useEffect(() => { api<Tech[]>(ALLY_FIELD_API.technicians).then(setTechs).catch(() => setTechs([])); }, []);
  const productos = useCallback(async (q: string): Promise<LookupItem[]> => {
    const r = await api<{ id: number; code: string; name: string }[]>(`${ALLY_FIELD_API.productSearch}?q=${encodeURIComponent(q)}`);
    return r.map((p) => ({ id: p.id, label: p.name, hint: p.code }));
  }, []);
  if (err) return <p className="muted">{err}</p>;
  if (!o) return <div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const act = o.can_act && !readOnly;
  const equipo = new Set(techs.map((t) => t.id));
  const corriendo = o.running.filter((r) => equipo.has(r.user_id));
  /** Solo lo distinto de la última respuesta del servidor. */
  const cambios = () => {
    const b: Record<string, unknown> = {};
    if (JSON.stringify(tasks) !== JSON.stringify(o.tasks || [])) b.tasks = tasks;
    if (JSON.stringify(photos) !== JSON.stringify(o.photos || [])) b.photos = photos;
    if (notes !== (o.notes || "")) b.notes = notes;
    if (firma !== (o.customer_signature || "")) b.customer_signature = firma;
    const antes = new Map((o.materials || []).map((m) => [m.id, m]));
    const quantities = Object.fromEntries(mats.filter((m) => m.id && antes.has(m.id) && num(m.quantity) !== num(antes.get(m.id)!.quantity)).map((m) => [String(m.id), num(m.quantity)]));
    if (Object.keys(quantities).length) b.quantities = quantities;
    const removed = [...antes.keys()].filter((mid) => !mats.some((m) => m.id === mid));
    if (removed.length) b.removed = removed;
    const agregados = mats.filter((m) => !m.id && m.name.trim()).map((m) => ({ product_id: m.product_id, name: m.name, quantity: num(m.quantity), unit: m.unit || "Unid", planned: 0 }));
    if (agregados.length) b.agregados = agregados;
    return b;
  };
  const accion = async (a: "arrive" | "start" | "pause" | "progress" | "finish") => {
    if ((a === "pause" || a === "finish") && o.stale && !finJornada) { toast("Indicá a qué hora terminaron ese día", "bad"); return; }
    setBusy(true);
    try {
      const body = { ...cambios(), ...((a === "pause" || a === "finish") && o.stale && finJornada ? { fin_jornada: toISO(finJornada) } : {}) };
      take(await api<OrderRow>(ALLY_FIELD_API.orderAction(id, a), { method: "POST", json: body }));
      toast(a === "finish" ? "Orden finalizada" : a === "start" ? "Jornada iniciada" : a === "pause" ? "Jornada terminada" : "Guardado");
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
      {act && o.stale && (
        <div className="al-stale" role="alert">
          <span>Quedó tiempo corriendo desde el {fmtWhen(corriendo.map((r) => r.started_at).sort()[0] || null)}. ¿A qué hora terminaron ese día?</span>
          <input className="input" type="datetime-local" aria-label="Hora en que terminaron" value={finJornada} onChange={(e) => setFinJornada(e.target.value)} style={{ maxWidth: 240 }} />
          <span className="meta">Se usa al terminar la jornada o al finalizar. Una jornada no pasa de 16 horas.</span>
        </div>
      )}
      {act && (
        <div className="al-actions" style={{ margin: "12px 0" }}>
          {o.status === "asignada" && <button className="btn btn--soft" disabled={busy} onClick={() => accion("arrive")}><Icon d={I.check} />Llegué</button>}
          {corriendo.length === 0 && <button className="btn btn--soft" disabled={busy} onClick={() => accion("start")}><Icon d={I.clock} />Iniciar jornada</button>}
          {corriendo.length > 0 && <button className="btn btn--soft" disabled={busy} onClick={() => accion("pause")}><Icon d={I.clock} />Terminar jornada</button>}
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
