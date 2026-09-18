/* Gestion de eventos: lista, crear, editar. Ruta privada /eventos. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { ImageField } from "../../ui/MediaPicker";

/* ---------- Types (exportados para EventDetail) ---------- */
export type TicketType = {
  id: number; name: string; price: number; quantity: number;
  max_per_order: number; active: boolean; available: number; sold: number;
};
export type EventData = {
  id: number; slug: string; name: string; description: string | null;
  venue: string | null; starts_at: string; ends_at: string | null;
  image_url: string | null; currency: string; tax_rate: number;
  status: "borrador" | "publicado" | "cerrado";
  ticket_types: TicketType[];
  stats: { valido: number; usado: number; pendiente: number; anulado: number };
  public_url: string;
};

/* ---------- Helpers ---------- */
const EVENT_STATUS: Record<string, { label: string; tone: string }> = {
  borrador: { label: "Borrador", tone: "muted" },
  publicado: { label: "Publicado", tone: "ok" },
  cerrado: { label: "Cerrado", tone: "bad" },
};

function EventBadge({ status }: { status: string }) {
  const s = EVENT_STATUS[status] ?? { label: status, tone: "muted" };
  return <span className={`badge badge--${s.tone}`}>{s.label}</span>;
}

/** Convierte YYYY-MM-DDTHH:mm (datetime-local) a ISO con offset CR -06:00 */
const toCRISO = (local: string) => (local ? `${local}:00-06:00` : "");

/** Convierte ISO server → valor de input datetime-local en hora CR (UTC−6) */
export const toLocalDT = (iso: string | null | undefined): string => {
  if (!iso) return "";
  const d = new Date(iso);
  // CR es UTC−6 sin horario de verano
  const cr = new Date(d.getTime() - 6 * 60 * 60 * 1000);
  return cr.toISOString().slice(0, 16);
};

const fmtEventDT = (iso: string) =>
  new Date(iso).toLocaleString("es-CR", {
    timeZone: "America/Costa_Rica",
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });

/* ---------- Types (form interno) ---------- */
type TicketTypeForm = {
  id?: number; name: string; price: number;
  quantity: number; max_per_order: number; active: boolean;
};
type EventForm = {
  name: string; venue: string; description: string;
  starts_at: string; ends_at: string;
  image_url: string | null; currency: string; tax_rate: number;
  status: "borrador" | "publicado" | "cerrado";
  ticket_types: TicketTypeForm[];
};

const blankTicket = (): TicketTypeForm => ({ name: "", price: 0, quantity: 100, max_per_order: 10, active: true });

const blankForm = (): EventForm => ({
  name: "", venue: "", description: "",
  starts_at: "", ends_at: "",
  image_url: null, currency: "CRC", tax_rate: 13,
  status: "borrador",
  ticket_types: [blankTicket()],
});

const formFromData = (ev: EventData): EventForm => ({
  name: ev.name,
  venue: ev.venue ?? "",
  description: ev.description ?? "",
  starts_at: toLocalDT(ev.starts_at),
  ends_at: toLocalDT(ev.ends_at),
  image_url: ev.image_url,
  currency: ev.currency,
  tax_rate: Number(ev.tax_rate),
  status: ev.status,
  ticket_types: ev.ticket_types.map((t) => ({
    id: t.id, name: t.name, price: Number(t.price),
    quantity: Number(t.quantity), max_per_order: Number(t.max_per_order), active: t.active,
  })),
});

/* ======================================================
   EventEditor — exportado para reusar en EventDetail
   ====================================================== */
export function EventEditor({
  event,
  onDone,
  onClose,
}: {
  event?: EventData | null;
  onDone: () => void;
  onClose: () => void;
}) {
  const { toast } = useSession();
  const [form, setForm] = useState<EventForm>(() =>
    event ? formFromData(event) : blankForm()
  );
  const [busy, setBusy] = useState(false);

  const set = (patch: Partial<EventForm>) => setForm((f) => ({ ...f, ...patch }));

  const setTicket = (i: number, patch: Partial<TicketTypeForm>) =>
    setForm((f) => ({
      ...f,
      ticket_types: f.ticket_types.map((t, idx) => (idx === i ? { ...t, ...patch } : t)),
    }));

  const addTicket = () => setForm((f) => ({ ...f, ticket_types: [...f.ticket_types, blankTicket()] }));
  const removeTicket = (i: number) =>
    setForm((f) => ({ ...f, ticket_types: f.ticket_types.filter((_, idx) => idx !== i) }));

  const save = async () => {
    if (!form.name.trim()) { toast("El nombre del evento es obligatorio", "bad"); return; }
    if (!form.starts_at) { toast("Indicá la fecha de inicio", "bad"); return; }
    setBusy(true);
    try {
      const body = {
        ...form,
        venue: form.venue || null,
        description: form.description || null,
        starts_at: toCRISO(form.starts_at),
        ends_at: form.ends_at ? toCRISO(form.ends_at) : null,
      };
      if (event?.id) {
        await api(`/events/${event.id}`, { method: "PUT", json: body });
      } else {
        await api("/events", { method: "POST", json: body });
      }
      toast(event?.id ? "Evento actualizado" : "Evento creado");
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error al guardar", "bad");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={event?.id ? `Editar: ${event.name}` : "Nuevo evento"}
      onClose={onClose}
      wide
      foot={
        <>
          <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={busy}>
            {busy ? <span className="spinner" style={{ width: 15, height: 15 }} /> : <Icon d={I.check} />}
            Guardar
          </button>
        </>
      }
    >
      {/* Info básica */}
      <Field label="Nombre del evento">
        <input className="input" value={form.name} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      <div className="grid-2">
        <Field label="Sede / lugar">
          <input className="input" value={form.venue} onChange={(e) => set({ venue: e.target.value })} placeholder="Teatro Nacional, etc." />
        </Field>
        <Field label="Estado">
          <select className="select" value={form.status} onChange={(e) => set({ status: e.target.value as EventForm["status"] })}>
            <option value="borrador">Borrador</option>
            <option value="publicado">Publicado</option>
            <option value="cerrado">Cerrado</option>
          </select>
        </Field>
        <Field label="Inicio">
          <input className="input" type="datetime-local" value={form.starts_at} onChange={(e) => set({ starts_at: e.target.value })} />
        </Field>
        <Field label="Fin (opcional)">
          <input className="input" type="datetime-local" value={form.ends_at} onChange={(e) => set({ ends_at: e.target.value })} />
        </Field>
        <Field label="Divisa">
          <select className="select" value={form.currency} onChange={(e) => set({ currency: e.target.value })}>
            <option>CRC</option><option>USD</option>
          </select>
        </Field>
        <Field label="Tasa IVA %">
          <input className="input input--mono" type="number" min={0} max={100} step={1} value={form.tax_rate} onChange={(e) => set({ tax_rate: Number(e.target.value) })} />
        </Field>
      </div>
      <Field label="Descripción">
        <textarea className="textarea" value={form.description} onChange={(e) => set({ description: e.target.value })} placeholder="Detalles del evento…" />
      </Field>
      <Field label="Imagen de portada">
        <ImageField value={form.image_url} onChange={(url) => set({ image_url: url })} label="Portada" />
      </Field>

      {/* Tipos de entrada */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
          <span className="h3">Tipos de entrada</span>
          <button className="btn btn--ghost btn--sm" onClick={addTicket}><Icon d={I.plus} size={13} />Agregar tipo</button>
        </div>
        {form.ticket_types.map((tt, i) => (
          <div key={i} style={{ padding: "12px", border: "1px solid var(--hair)", borderRadius: "var(--r)", marginBottom: 8, background: "var(--surface-2)" }}>
            <div className="grid-2" style={{ gap: 10 }}>
              <Field label="Nombre">
                <input className="input" value={tt.name} onChange={(e) => setTicket(i, { name: e.target.value })} placeholder="General, VIP…" />
              </Field>
              <Field label="Precio (IVA incluido)">
                <input className="input input--mono" type="number" min={0} step={100} value={tt.price} onChange={(e) => setTicket(i, { price: Number(e.target.value) })} />
              </Field>
              <Field label="Cantidad total">
                <input className="input input--mono" type="number" min={1} value={tt.quantity} onChange={(e) => setTicket(i, { quantity: Number(e.target.value) })} />
              </Field>
              <Field label="Máx por orden">
                <input className="input input--mono" type="number" min={1} value={tt.max_per_order} onChange={(e) => setTicket(i, { max_per_order: Number(e.target.value) })} />
              </Field>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, cursor: "pointer" }}>
                <input type="checkbox" checked={tt.active} onChange={(e) => setTicket(i, { active: e.target.checked })} />
                Activo (disponible para compra)
              </label>
              {form.ticket_types.length > 1 && (
                <button className="btn btn--danger btn--sm" onClick={() => removeTicket(i)}>
                  <Icon d={I.x} size={13} />Quitar
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

/* ======================================================
   Events — página principal (default export)
   ====================================================== */
export default function Events() {
  const { toast } = useSession();
  const [items, setItems] = useState<EventData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showEditor, setShowEditor] = useState(false);

  const load = () => {
    setLoading(true);
    api<EventData[]>("/events")
      .then(setItems)
      .catch((e) => toast(e instanceof Error ? e.message : "Error al cargar eventos", "bad"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="page-head">
        <div><div className="meta">Eventos</div><h1 className="h1">Eventos</h1></div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button>
          <button className="btn btn--crimson" onClick={() => setShowEditor(true)}>
            <Icon d={I.plus} />Crear evento
          </button>
        </div>
      </div>

      <Card flush>
        {loading ? (
          <div style={{ padding: 44, display: "grid", placeItems: "center" }}><span className="spinner" /></div>
        ) : items.length === 0 ? (
          <Empty
            hint="Creá el primer evento para empezar a vender entradas."
            action={<button className="btn btn--crimson" onClick={() => setShowEditor(true)}><Icon d={I.plus} />Crear evento</button>}
          />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Evento</th>
                <th>Inicio</th>
                <th>Estado</th>
                <th className="num">Vendidas</th>
                <th className="num">Disponibles</th>
                <th className="num">Recaudado</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((ev) => {
                const vendidas = ev.stats.valido + ev.stats.usado + ev.stats.pendiente;
                const disponibles = ev.ticket_types.reduce((s, t) => s + Number(t.available), 0);
                const recaudado = ev.ticket_types.reduce((s, t) => s + Number(t.price) * Number(t.sold), 0);
                return (
                  <tr key={ev.id}>
                    <td>
                      <span style={{ fontWeight: 600 }}>{ev.name}</span>
                      {ev.venue && <div className="meta" style={{ textTransform: "none", marginTop: 2 }}>{ev.venue}</div>}
                    </td>
                    <td className="muted" style={{ whiteSpace: "nowrap" }}>
                      {fmtEventDT(ev.starts_at)}
                      {ev.ends_at && <div className="meta" style={{ textTransform: "none" }}>{fmtDate(ev.ends_at)}</div>}
                    </td>
                    <td><EventBadge status={ev.status} /></td>
                    <td className="num mono">{vendidas}</td>
                    <td className="num mono">{disponibles}</td>
                    <td className="num money">{fmtMoney(recaudado, ev.currency)}</td>
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      <Link className="btn btn--ghost btn--sm" to={`/eventos/${ev.id}`}>Abrir</Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      {showEditor && (
        <EventEditor
          onDone={() => { setShowEditor(false); load(); }}
          onClose={() => setShowEditor(false)}
        />
      )}
    </>
  );
}
