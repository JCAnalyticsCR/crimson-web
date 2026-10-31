/* Grupo B: partes y aliados de la oportunidad.
   Andrés: "Nodo contrata a Crimson, y Crimson contrata a Hauset". Aquí viven las demás empresas, sus relaciones
   concretas (A2: aliado comercial, responsable de ejecución, quién le paga a Crimson), sus contactos por función y
   las solicitudes de precio.
   Revisión 2 (puntos 7-11): "Solicitar precio al aliado" crea la PETICIÓN (sin precio ni vigencia); el aliado completa
   su OFERTA después. Aceptarla guarda el acuerdo como versión; cada cambio posterior es otra versión.
   El monto de la oferta solo lo ve quien ve costos (el servidor ni lo manda a los demás). */
import { useCallback, useState } from "react";
import { ApiError, api, fmtDate, fmtMoney, uploadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import DealTerms from "../commissions/DealTerms";
import type { ScopeData } from "./ScopeItems";
import "./opportunities.css";

export type Contact = { name?: string | null; email?: string | null; phone?: string | null };
type Attachment = { id: number; url: string; filename: string };
export type OfferLine = { concept: string; quantity: string; unit: string | null; unit_price: string };
export type ReqLine = { concept: string; quantity: string | null; unit: string | null; scope_item_id?: number | null };
export type Agreement = {
  id: number; request_id: number; participation_id: number; version: number; status: "vigente" | "reemplazado" | "anulado"; what: string; scope: string | null;
  amount: string | null; currency: string; valid_until: string | null; conditions: string | null; exclusions: string | null; lines: OfferLine[] | null;
  shared: boolean; changes: string[]; note: string | null; created_by: string | null; created_at: string;
};
export type Delivery = { to: string | null; to_portal: boolean; sent_at: string | null; seen_at: string | null; seen_by: string | null; channel: string; channel_label: string };
export type CostRequest = {
  id: number; participation_id: number; opportunity_id: number; what: string; responsible_id: number | null; responsible: string | null;
  due_date: string | null; status: string; stored_status?: string; status_label?: string; pending: boolean; overdue: boolean; costs_visible: boolean;
  /* la petición (la ve todo el que ve la oportunidad) */
  scope?: string | null; site?: string | null; inclusions?: string | null; req_exclusions?: string | null; req_lines?: ReqLine[]; req_attachments?: Attachment[];
  delivery?: Delivery; has_offer?: boolean; decided_at?: string | null; decided_by?: string | null;
  /* la oferta (solo quien ve costos) */
  amount?: string | null; currency?: string; valid_until?: string | null; conditions?: string | null; exclusions?: string | null; attachments?: Attachment[];
  decision_note?: string | null; agreements?: Agreement[];
  source?: "crimson" | "aliado"; responded_at?: string | null; responded_by?: string | null; lines?: OfferLine[];
};
export type Ally = {
  id: number; opportunity_id: number; project_id: number | null; ally_company_id?: number | null; customer_id: number | null; supplier_id: number | null;
  company_kind: "ally" | "customer" | "supplier" | null; ally_state?: string | null; visible_to_ally?: boolean;
  name: string; role: string; scope: string | null; scope_lines: string[]; contacts: Record<string, Contact>; requests?: CostRequest[];
  is_origin?: boolean; is_executor?: boolean; is_payer?: boolean; relations?: string[];
  /* economia (solo en la ficha del proyecto y para quien ve costos): que recibe el aliado en este negocio */
  deal?: { pago_acordado_crc: string; commission_summary: string | null; commission_general?: boolean; bonus_summary: string | null; committed_date: string | null } | null;
};
export type PendingCost = CostRequest & { ally: string; opportunity_number: string; opportunity_title: string };
export type Relations = {
  end_customer: { id: number | null; name: string | null } | null;
  origin: { participation_id: number; name: string } | null;
  executor: { kind: "crimson" | "aliado"; participation_id: number | null; name: string };
  payer: { customer_id: number | null; name: string | null; participation_id: number | null; is_end_customer: boolean };
  billing_line: string;
  open_costs: boolean;
};

/* compatibilidad: el papel viejo (se sigue guardando, la interfaz muestra las relaciones concretas) */
export const ALLY_ROLES: Record<string, string> = {
  contratante: "Contratante", referido: "Referido", subcontratista: "Subcontratista", suministro: "Suministro", configuracion: "Configuración", software: "Software",
};
export const CONTACT_FUNCS: Record<string, string> = { comercial: "Comercial", tecnico: "Técnico", aprobador: "Aprobador", pagos: "Pagos" };
export const COST_STATES: Record<string, string> = {
  borrador: "Borrador", enviado: "Enviada", visto: "Vista por el aliado", respondido: "Respondida", aceptado: "Aceptada", rechazado: "Rechazada", vencido: "Vencida",
  /* de antes de la revisión 2 */
  solicitado: "Enviada", recibido: "Respondida", aprobado: "Aceptada",
};
export const STATE_BADGE: Record<string, string> = {
  borrador: "muted", enviado: "warn", visto: "warn", respondido: "info", aceptado: "ok", rechazado: "muted", vencido: "bad", solicitado: "warn", recibido: "info", aprobado: "ok",
};
const CHANNELS: Record<string, string> = { portal: "Por su portal de aliados", correo: "Por correo", whatsapp: "Por WhatsApp", telefono: "Por teléfono" };

/** Chips de las relaciones concretas de una participación (A2). */
export const relationsOf = (a: Pick<Ally, "relations" | "role">) => (a.relations && a.relations.length ? a.relations : [ALLY_ROLES[a.role] || a.role]);

/** "Nodo Latam → para Yobel · Sitio X": la misma frase que deja la bitácora al cambiar las partes. */
export const partyLine = (o: { customer: string | null; end_customer?: string | null; site?: string | null }) =>
  `${o.customer || "Sin cliente"}${o.end_customer ? ` → para ${o.end_customer}` : ""}${o.site ? ` · ${o.site}` : ""}`;

const fmtTs = (s: string | null | undefined) => (s ? fmtDate(s) : "—");

/* Empresa de la participacion: una empresa aliada (lo normal, 12:45 "lo ocupo que salgan solo los aliados") o, con
   "¿No es aliado?", un cliente o proveedor como antes. El buscador devuelve ids de tres tablas: se guardan aparte. */
type CompanyKind = "ally" | "customer" | "supplier";
type CompanyRef = { kind: CompanyKind; id: number };
const picked = new Map<number, CompanyRef>();
let seq = 0;
const searchCompanies = (todos: boolean) => async (q: string): Promise<LookupItem[]> => {
  const r = await api<{ kind: CompanyKind; id: number; name: string; hint: string; tax_id?: string | null; suspended?: boolean }[]>(
    `/opportunity-allies/companies?q=${encodeURIComponent(q)}${todos ? "&todos=true" : ""}`,
  );
  return r.map((c) => {
    seq += 1;
    picked.set(seq, { kind: c.kind, id: c.id });
    return { id: seq, label: c.name, hint: [c.hint, c.tax_id, c.suspended ? "suspendido" : null].filter(Boolean).join(" · ") };
  });
};

type AllyForm = {
  id?: number; company: CompanyRef | null; company_name: string; scope: string; scope_lines: string[]; contacts: Record<string, Contact>; visible: boolean;
  is_origin: boolean; is_executor: boolean;
};
type QuickAlly = { name: string; tax_id: string; func: string; contact: string; existing?: { id: number; name: string } | null };
type FormLine = { concept: string; quantity: string; unit: string; scope_item_id: number | null };
type ReqForm = {
  id?: number; ally_id: number; ally: string; ally_portal: boolean; stored_status: string; what: string; scope: string; lines: FormLine[]; site: string;
  due_date: string; inclusions: string; req_exclusions: string; attachments: Attachment[]; responsible_id: string; responsible?: string | null; channel: string;
};
type OfferForm = { rid: number; ally: string; what: string; mode: "monto" | "lineas"; amount: string; lines: { concept: string; quantity: string; unit: string; unit_price: string }[]; currency: string; valid_until: string; conditions: string; exclusions: string; attachments: Attachment[]; channel: string };
type DecisionForm = { rid: number; ally: string; what: string; action: "aceptar" | "rechazar"; note: string; shared: boolean };
type AgreementForm = { rid: number; ally: string; current: Agreement; amount: string; currency: string; valid_until: string; conditions: string; exclusions: string; shared: boolean; note: string };

const num = (s: string) => Number(s.replace(",", "."));

export function AlliesPanel({ oppId, allies, canEdit, seesCosts, sellers, quoteId, site, onChange }: {
  oppId: number; allies: Ally[]; canEdit: boolean; seesCosts: boolean; sellers: { id: number; name: string }[]; quoteId: number | null;
  site?: string | null; onChange: (next: Ally[]) => void;
}) {
  const { toast, allows, me } = useSession();
  const [form, setForm] = useState<AllyForm | null>(null);
  const [req, setReq] = useState<ReqForm | null>(null);
  const [offer, setOffer] = useState<OfferForm | null>(null);
  const [decision, setDecision] = useState<DecisionForm | null>(null);
  const [agreement, setAgreement] = useState<AgreementForm | null>(null);
  const [lines, setLines] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [todos, setTodos] = useState(false);
  const [quick, setQuick] = useState<QuickAlly | null>(null);
  const [deal, setDeal] = useState<{ id: number; name: string } | null>(null); // acuerdo economico (economia)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const fetcher = useCallback(searchCompanies(todos), [todos]);
  const reqs = allies.flatMap((a) => a.requests || []);
  const pend = reqs.filter((r) => r.pending);
  const late = reqs.filter((r) => r.overdue);

  const closeForms = () => { setForm(null); setReq(null); setOffer(null); setDecision(null); setAgreement(null); };
  const loadLines = () => {
    if (lines !== null || !quoteId || !allows("sales.ver")) return;
    api<{ lines: { name: string }[] }>(`/quotes/${quoteId}`).then((q) => setLines(q.lines.map((l) => l.name))).catch(() => setLines([]));
  };
  const openAlly = (a?: Ally) => {
    loadLines(); closeForms();
    setQuick(null);
    const ref: CompanyRef | null = a ? (a.ally_company_id ? { kind: "ally", id: a.ally_company_id } : a.supplier_id ? { kind: "supplier", id: a.supplier_id } : a.customer_id ? { kind: "customer", id: a.customer_id } : null) : null;
    setTodos(!!a && a.company_kind !== "ally");
    setForm(a
      ? { id: a.id, company: ref, company_name: a.name, scope: a.scope || "", scope_lines: a.scope_lines, contacts: a.contacts || {}, visible: a.visible_to_ally !== false, is_origin: !!a.is_origin, is_executor: !!a.is_executor }
      : { company: null, company_name: "", scope: "", scope_lines: [], contacts: {}, visible: true, is_origin: false, is_executor: false });
  };
  /* "+ Crear aliado «texto»": alta rapida (nombre, cedula y un contacto por funcion) y queda elegido */
  const crearAliado = async () => {
    if (!quick || !form) return;
    try {
      const contacts = quick.contact.trim() ? { [quick.func]: { name: quick.contact.trim() } } : {};
      const c = await api<{ kind: CompanyKind; id: number; name: string }>("/opportunity-allies/companies", { method: "POST", json: { name: quick.name, tax_id: quick.tax_id || null, contacts } });
      setForm({ ...form, company: { kind: "ally", id: c.id }, company_name: c.name });
      setQuick(null);
      toast(`Aliado ${c.name} creado`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setQuick({ ...quick, existing: (e.data as { existing?: { id: number; name: string } })?.existing || null });
      else toast(e instanceof Error ? e.message : "No se pudo crear", "bad");
    }
  };
  const usarExistente = () => {
    if (!quick?.existing || !form) return;
    setForm({ ...form, company: { kind: "ally", id: quick.existing.id }, company_name: quick.existing.name });
    setQuick(null);
  };
  /* Sin elegir empresa, la API solo liga por vinculo exacto o por la misma cedula. Si hay una empresa aliada con el
     mismo nombre y otra cedula responde 409 con ella: se usa solo si la persona confirma que es la misma. */
  const convertir = (a: Ally) => {
    if (!confirm(`¿Convertir a ${a.name} en empresa aliada? Así podrá ver esta oportunidad en su portal.`)) return;
    const url = `/opportunity-allies/${a.id}/to-ally`;
    run(async () => {
      try {
        return await api<Ally[]>(url, { method: "POST", json: {} });
      } catch (e) {
        const ex = e instanceof ApiError && e.status === 409 ? (e.data as { existing?: { id: number; name: string } })?.existing : null;
        if (!ex) throw e;
        if (!confirm(`Ya existe la empresa aliada «${ex.name}» con ese nombre, pero no con la misma cédula. ¿Es la misma empresa? Aceptar la usa.`)) {
          throw new Error("No se convirtió: elegí la empresa aliada en «Editar» si es otra");
        }
        return await api<Ally[]>(url, { method: "POST", json: { ally_company_id: ex.id } });
      }
    }, `${a.name} ahora es empresa aliada`);
  };
  const run = async (fn: () => Promise<Ally[]>, ok: string) => {
    if (busy) return false;
    setBusy(true);
    try { onChange(await fn()); toast(ok); return true; }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); return false; }
    finally { setBusy(false); }
  };

  const saveAlly = async () => {
    if (!form || !form.company) return;
    const k = form.company;
    const json = {
      ally_company_id: k.kind === "ally" ? k.id : null, customer_id: k.kind === "customer" ? k.id : null, supplier_id: k.kind === "supplier" ? k.id : null,
      scope: form.scope.trim() || null, scope_lines: form.scope_lines, contacts: form.contacts, visible_to_ally: form.visible,
      is_origin: form.is_origin, is_executor: form.is_executor,
    };
    const done = await run(
      () => api<Ally[]>(form.id ? `/opportunity-allies/${form.id}` : `/opportunities/${oppId}/allies`, { method: form.id ? "PUT" : "POST", json }),
      form.id ? "Aliado actualizado" : `${form.company_name} agregado`,
    );
    if (done) setForm(null);
  };
  const removeAlly = (a: Ally) => {
    if (!confirm(`¿Quitar a ${a.name} de la oportunidad?${a.requests?.length ? " Se borran también sus solicitudes de precio." : ""} Sus alcances quedan por asignar.`)) return;
    run(() => api<Ally[]>(`/opportunity-allies/${a.id}`, { method: "DELETE" }), `${a.name} quitado`);
  };

  /* ---------- la petición ---------- */
  const openReq = (a: Ally, r?: CostRequest) => {
    closeForms();
    const portal = a.company_kind === "ally" && a.visible_to_ally !== false;
    setReq(r
      ? {
          id: r.id, ally_id: a.id, ally: a.name, ally_portal: portal, stored_status: r.stored_status || r.status, what: r.what, scope: r.scope || "",
          lines: (r.req_lines || []).map((l) => ({ concept: l.concept, quantity: l.quantity ?? "", unit: l.unit ?? "", scope_item_id: l.scope_item_id ?? null })),
          site: r.site || "", due_date: r.due_date || "", inclusions: r.inclusions || "", req_exclusions: r.req_exclusions || "", attachments: r.req_attachments || [],
          responsible_id: r.responsible_id ? String(r.responsible_id) : "", responsible: r.responsible, channel: r.delivery?.channel || (portal ? "portal" : "correo"),
        }
      : {
          ally_id: a.id, ally: a.name, ally_portal: portal, stored_status: "nuevo", what: "", scope: a.scope || "", lines: [], site: site || "", due_date: "", inclusions: "",
          req_exclusions: "", attachments: [], responsible_id: me ? String(me.user.id) : "", channel: portal ? "portal" : "correo",
        });
  };
  const loadScopeLines = async () => {
    if (!req) return;
    try {
      const d = await api<ScopeData>(`/opportunities/${oppId}/scope`);
      const mine = d.items.filter((it) => it.supplier.participation_id === req.ally_id || it.executor.participation_id === req.ally_id);
      if (!mine.length) { toast("Este aliado no tiene alcances asignados todavía (Alcances y responsables)"); return; }
      const ya = new Set(req.lines.map((l) => l.scope_item_id).filter(Boolean));
      const nuevas = mine.filter((it) => !ya.has(it.id)).map((it) => ({ concept: `${it.kind_label}: ${it.description}`.slice(0, 200), quantity: it.quantity ?? "", unit: it.unit ?? "", scope_item_id: it.id }));
      setReq({ ...req, lines: [...req.lines, ...nuevas] });
      toast(nuevas.length ? `${nuevas.length} partida(s) cargadas de sus alcances` : "Ya estaban cargadas");
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudieron cargar los alcances", "bad"); }
  };
  const saveReq = async (status: "borrador" | "enviado" | null) => {
    if (!req || req.what.trim().length < 2) return;
    const json: Record<string, unknown> = {
      what: req.what.trim(), responsible_id: req.responsible_id ? Number(req.responsible_id) : null, due_date: req.due_date || null,
      scope: req.scope.trim() || null, site: req.site.trim() || null, inclusions: req.inclusions.trim() || null, req_exclusions: req.req_exclusions.trim() || null,
      req_lines: req.lines.filter((l) => l.concept.trim()).map((l) => ({ concept: l.concept.trim(), quantity: l.quantity.trim() ? num(l.quantity) : null, unit: l.unit.trim() || null, scope_item_id: l.scope_item_id })),
      req_attachment_ids: req.attachments.map((x) => x.id), response_channel: req.channel,
    };
    if (status) json.status = status;
    const done = await run(
      () => api<Ally[]>(req.id ? `/opportunity-allies/requests/${req.id}` : `/opportunity-allies/${req.ally_id}/requests`, { method: req.id ? "PUT" : "POST", json }),
      status === "borrador" ? "Borrador guardado (el aliado todavía no lo ve)" : req.id && status !== "enviado" ? "Solicitud actualizada" : `Solicitud enviada a ${req.ally}`,
    );
    if (done) setReq(null);
  };
  const sendReq = (r: CostRequest, a: Ally) => {
    if (!confirm(`¿Enviar la solicitud «${r.what}» a ${a.name}? Desde ahora la ve en su portal${a.company_kind === "ally" ? " y le llega el aviso" : ""}.`)) return;
    run(() => api<Ally[]>(`/opportunity-allies/requests/${r.id}/send`, { method: "POST" }), `Solicitud enviada a ${a.name}`);
  };
  const removeReq = (r: CostRequest) => {
    if (!confirm(`¿Eliminar la solicitud «${r.what}»?${(r.agreements || []).length ? " También se borra el historial de su acuerdo." : ""}`)) return;
    run(() => api<Ally[]>(`/opportunity-allies/requests/${r.id}`, { method: "DELETE" }), "Solicitud eliminada");
  };
  const attach = async (f: File | undefined, to: "req" | "offer") => {
    if (!f) return;
    try {
      const m = await uploadFile<Attachment>("/media", f);
      const x = { id: m.id, url: m.url, filename: m.filename };
      if (to === "req" && req) setReq({ ...req, attachments: [...req.attachments.filter((y) => y.id !== m.id), x] });
      if (to === "offer" && offer) setOffer({ ...offer, attachments: [...offer.attachments.filter((y) => y.id !== m.id), x] });
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo subir el archivo", "bad"); }
  };

  /* ---------- la oferta que llegó por fuera, la decisión y las versiones del acuerdo (quien ve costos) ---------- */
  const openOffer = (a: Ally, r: CostRequest) => {
    closeForms();
    const hasLines = (r.lines || []).length > 0;
    setOffer({
      rid: r.id, ally: a.name, what: r.what, mode: hasLines ? "lineas" : "monto", amount: r.amount != null ? String(Number(r.amount)) : "",
      lines: hasLines ? (r.lines || []).map((l) => ({ concept: l.concept, quantity: String(Number(l.quantity)), unit: l.unit || "", unit_price: String(Number(l.unit_price)) })) : [{ concept: "", quantity: "1", unit: "", unit_price: "" }],
      currency: r.currency || "CRC", valid_until: r.valid_until || "", conditions: r.conditions || "", exclusions: r.exclusions || "", attachments: r.attachments || [],
      channel: r.delivery?.channel && r.delivery.channel !== "portal" ? r.delivery.channel : "correo",
    });
  };
  const saveOffer = async () => {
    if (!offer) return;
    const json: Record<string, unknown> = {
      currency: offer.currency, valid_until: offer.valid_until || null, conditions: offer.conditions.trim() || null, exclusions: offer.exclusions.trim() || null,
      attachment_ids: offer.attachments.map((x) => x.id), channel: offer.channel,
    };
    if (offer.mode === "monto") json.amount = offer.amount ? num(offer.amount) : null;
    else json.lines = offer.lines.filter((l) => l.concept.trim() && l.unit_price !== "").map((l) => ({ concept: l.concept.trim(), quantity: num(l.quantity || "1"), unit: l.unit.trim() || null, unit_price: num(l.unit_price) }));
    const done = await run(() => api<Ally[]>(`/opportunity-allies/requests/${offer.rid}/offer`, { method: "POST", json }), `Oferta de ${offer.ally} registrada`);
    if (done) setOffer(null);
  };
  const openDecision = (a: Ally, r: CostRequest, action: "aceptar" | "rechazar") => { closeForms(); setDecision({ rid: r.id, ally: a.name, what: r.what, action, note: "", shared: true }); };
  const saveDecision = async () => {
    if (!decision) return;
    const done = await run(
      () => api<Ally[]>(`/opportunity-allies/requests/${decision.rid}/decision`, { method: "POST", json: { action: decision.action, note: decision.note.trim() || null, shared: decision.shared } }),
      decision.action === "aceptar" ? "Oferta aceptada: el acuerdo quedó guardado (versión 1)" : "Oferta rechazada",
    );
    if (done) setDecision(null);
  };
  const openAgreement = (a: Ally, r: CostRequest) => {
    const cur = (r.agreements || []).slice().sort((x, y) => y.version - x.version)[0];
    if (!cur) return;
    closeForms();
    setAgreement({ rid: r.id, ally: a.name, current: cur, amount: cur.amount != null ? String(Number(cur.amount)) : "", currency: cur.currency, valid_until: cur.valid_until || "", conditions: cur.conditions || "", exclusions: cur.exclusions || "", shared: cur.shared, note: "" });
  };
  const saveAgreement = async () => {
    if (!agreement) return;
    const c = agreement.current;
    const json: Record<string, unknown> = {};
    if (agreement.amount && num(agreement.amount) !== Number(c.amount)) json.amount = num(agreement.amount);
    if (agreement.currency !== c.currency) json.currency = agreement.currency;
    if ((agreement.valid_until || null) !== (c.valid_until || null)) json.valid_until = agreement.valid_until || null;
    if ((agreement.conditions.trim() || null) !== (c.conditions || null)) json.conditions = agreement.conditions.trim() || null;
    if ((agreement.exclusions.trim() || null) !== (c.exclusions || null)) json.exclusions = agreement.exclusions.trim() || null;
    if (agreement.shared !== c.shared) json.shared = agreement.shared;
    if (!Object.keys(json).length) { toast("No cambió nada del acuerdo", "bad"); return; }
    if (agreement.note.trim()) json.note = agreement.note.trim();
    const done = await run(() => api<Ally[]>(`/opportunity-allies/requests/${agreement.rid}/agreement`, { method: "POST", json }), `Acuerdo con ${agreement.ally}: nueva versión guardada`);
    if (done) setAgreement(null);
  };

  const offerTotal = offer && offer.mode === "lineas" ? offer.lines.reduce((s, l) => s + (num(l.quantity || "0") || 0) * (num(l.unit_price || "0") || 0), 0) : null;

  return (
    <section className="ally-panel" aria-label="Aliados">
      <div className="ally-panel__head">
        <b>Aliados y participación</b>
        {canEdit && !form && <button className="btn btn--soft btn--sm" onClick={() => openAlly()}><Icon d={I.plus} />Agregar aliado</button>}
      </div>
      {pend.length > 0 && (
        <div className={`ally-alert${late.length ? " is-late" : ""}`} role="status">
          {pend.length === 1 ? "1 solicitud de precio pendiente" : `${pend.length} solicitudes de precio pendientes`}{late.length ? ` · ${late.length} vencida${late.length > 1 ? "s" : ""}` : ""}
        </div>
      )}
      {allies.length === 0 && !form && <p className="muted" style={{ fontSize: 13, margin: 0 }}>Sin aliados. Agregá quién más participa: quien originó la oportunidad, quien instala, quien suministra o configura.</p>}

      {allies.map((a) => (
        <article className="ally" key={a.id}>
          <div className="ally__head">
            <div className="ally__name">
              <b>{a.name}</b>
              {relationsOf(a).map((x) => <span key={x} className="opp__kind">{x}</span>)}
              <span className="meta">{a.company_kind === "ally" ? "Aliado" : a.company_kind === "supplier" ? "Proveedor" : "Cliente"}</span>
              {a.company_kind === "ally" && a.visible_to_ally === false && <span className="badge badge--muted">Oculta al aliado</span>}
              {a.company_kind === "ally" && a.ally_state && a.ally_state !== "activo" && <span className="badge badge--warn">{a.ally_state}</span>}
            </div>
            {canEdit && (
              <div className="ally__actions">
                {a.company_kind !== "ally" && <button className="btn btn--ghost btn--sm" onClick={() => convertir(a)}>Convertir en aliado</button>}
                <button className="btn btn--ghost btn--sm" onClick={() => openReq(a)}>Solicitar precio al aliado</button>
                {allows("commissions.configurar") && <button className="btn btn--ghost btn--sm" onClick={() => setDeal({ id: a.id, name: a.name })}>Acuerdo económico</button>}
                <button className="btn btn--ghost btn--sm" onClick={() => openAlly(a)}>Editar</button>
                <button className="btn btn--ghost btn--sm" onClick={() => removeAlly(a)} aria-label={`Quitar ${a.name}`}><Icon d={I.x} size={14} /></button>
              </div>
            )}
          </div>
          {a.scope && <p className="ally__scope">{a.scope}</p>}
          {a.scope_lines.length > 0 && <div className="ally__chips">{a.scope_lines.map((l) => <span key={l}>{l}</span>)}</div>}
          {Object.keys(a.contacts || {}).length > 0 && (
            <dl className="ally__contacts">
              {Object.entries(a.contacts).map(([k, c]) => (
                <div key={k}><dt>{CONTACT_FUNCS[k] || k}</dt><dd>{[c.name, c.phone, c.email].filter(Boolean).join(" · ")}</dd></div>
              ))}
            </dl>
          )}
          {(a.requests || []).length > 0 && (
            <ul className="ally__reqs">
              {(a.requests || []).map((r) => (
                <RequestItem key={r.id} r={r} a={a} canEdit={canEdit} seesCosts={seesCosts}
                  onEdit={() => openReq(a, r)} onSend={() => sendReq(r, a)} onRemove={() => removeReq(r)} onOffer={() => openOffer(a, r)}
                  onDecide={(act) => openDecision(a, r, act)} onAgreement={() => openAgreement(a, r)} />
              ))}
            </ul>
          )}
        </article>
      ))}

      {form && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>{form.id ? `Editar aliado · ${form.company_name}` : "Nuevo aliado"}</b><span className="muted">Elegí una empresa aliada o creala aquí.</span></div>
          <Field label="Empresa aliada">
            <Lookup
              value={form.company_name}
              placeholder={todos ? "Buscar aliado, cliente o proveedor…" : "Buscar empresa aliada…"}
              fetcher={fetcher}
              onSelect={(it, text) => setForm({ ...form, company: it ? picked.get(it.id) || null : null, company_name: text })}
              onCreate={(text) => setQuick({ name: text, tax_id: "", func: "comercial", contact: "" })}
              createLabel="Crear aliado"
            />
            <button type="button" className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => setTodos((t) => !t)}>{todos ? "Solo empresas aliadas" : "¿No es aliado? Buscar en clientes y proveedores"}</button>
          </Field>
          {quick && (
            <div className="opp-newcust" style={{ margin: "0 0 10px" }}>
              <b>Crear aliado «{quick.name}»</b>
              <div className="grid-3">
                <Field label="Nombre"><input className="input" value={quick.name} onChange={(e) => setQuick({ ...quick, name: e.target.value, existing: null })} /></Field>
                <Field label="Cédula jurídica (opcional)"><input className="input" value={quick.tax_id} onChange={(e) => setQuick({ ...quick, tax_id: e.target.value, existing: null })} /></Field>
                <Field label="Contacto">
                  <select className="select" value={quick.func} onChange={(e) => setQuick({ ...quick, func: e.target.value })}>{Object.entries(CONTACT_FUNCS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  <input className="input" placeholder="Nombre del contacto" value={quick.contact} onChange={(e) => setQuick({ ...quick, contact: e.target.value })} />
                </Field>
              </div>
              {quick.existing && (
                <p className="muted" style={{ margin: "4px 0" }}>Ya existe: <b>{quick.existing.name}</b>{" "}
                  <button type="button" className="btn btn--soft btn--sm" onClick={usarExistente}>Usar este</button>
                </p>
              )}
              <div className="ally__form-foot">
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setQuick(null)}>Cancelar</button>
                <button type="button" className="btn btn--crimson btn--sm" disabled={quick.name.trim().length < 2} onClick={crearAliado}>Crear aliado</button>
              </div>
            </div>
          )}
          {/* A2: relaciones concretas en vez de un "Rol" general (quién le paga a Crimson va en "Relaciones de la oportunidad") */}
          <div className="ally__pick" style={{ maxHeight: "none" }}>
            <label><input type="checkbox" checked={form.is_origin} onChange={(e) => setForm({ ...form, is_origin: e.target.checked })} />Originó la oportunidad (aliado comercial)</label>
            <label><input type="checkbox" checked={form.is_executor} onChange={(e) => setForm({ ...form, is_executor: e.target.checked })} />Responsable de ejecución <span className="meta">&nbsp;(si nadie lo es, ejecuta Crimson)</span></label>
          </div>
          {form.company?.kind === "ally" && (
            <label className="pf-bool" title="El aliado verá esta oportunidad, su alcance y lo que le pidamos">
              <input type="checkbox" checked={form.visible} onChange={(e) => setForm({ ...form, visible: e.target.checked })} />Visible en el portal del aliado
              <span className="meta" style={{ marginLeft: 6 }}>El aliado verá esta oportunidad, su alcance y lo que le pidamos.</span>
            </label>
          )}
          <Field label="Alcance asignado (resumen)" hint="El detalle por partida (quién suministra, quién ejecuta y su estado) va en «Alcances y responsables»."><textarea className="textarea" style={{ minHeight: 56 }} value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })} placeholder="Tendido y fusión de fibra entre bodegas…" /></Field>
          {lines && lines.length > 0 && (
            <Field label="Partidas de la cotización (opcional)">
              <div className="ally__pick">
                {lines.map((l) => (
                  <label key={l}><input type="checkbox" checked={form.scope_lines.includes(l)} onChange={(e) => setForm({ ...form, scope_lines: e.target.checked ? [...form.scope_lines, l] : form.scope_lines.filter((x) => x !== l) })} />{l}</label>
                ))}
              </div>
            </Field>
          )}
          <details open={form.company?.kind !== "ally"}>
          <summary className="meta" style={{ cursor: "pointer", margin: "6px 0" }}>{form.company?.kind === "ally" ? "Contactos del aliado · editar solo para esta oportunidad" : "Contactos por función"}</summary>
          <div className="ally__contact-grid">
            {Object.entries(CONTACT_FUNCS).map(([k, label]) => {
              const c = form.contacts[k] || {};
              const set = (patch: Contact) => setForm({ ...form, contacts: { ...form.contacts, [k]: { ...c, ...patch } } });
              return (
                <fieldset key={k}>
                  <legend>Contacto {label.toLowerCase()}</legend>
                  <input className="input" placeholder="Nombre" value={c.name || ""} onChange={(e) => set({ name: e.target.value })} />
                  <input className="input" type="email" placeholder="Correo" value={c.email || ""} onChange={(e) => set({ email: e.target.value })} />
                  <input className="input" type="tel" placeholder="Teléfono" value={c.phone || ""} onChange={(e) => set({ phone: e.target.value })} />
                </fieldset>
              );
            })}
          </div>
          </details>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setForm(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={!form.company || busy} onClick={saveAlly}>Guardar aliado</button>
          </div>
        </div>
      )}

      {req && (
        <div className="opp-newcust">
          <div className="opp-newcust__head">
            <b>{req.id ? "Solicitud de precio" : "Solicitar precio al aliado"}</b>
            <span className="muted">Es la petición: sin precio ni vigencia. El aliado completa su oferta después.</span>
          </div>
          <div className="ally-dest"><span className="meta">Para</span><b>{req.ally}</b><span className="meta">{req.ally_portal ? "La ve en su portal de aliados y le llega un aviso al enviarla." : "No tiene portal: se le envía y responde por fuera."}</span></div>
          <Field label="¿Qué se pide?"><input className="input" autoFocus maxLength={300} value={req.what} onChange={(e) => setReq({ ...req, what: e.target.value })} placeholder="Precio de instalación de 8 cámaras" /></Field>
          <Field label="Alcance"><textarea className="textarea" style={{ minHeight: 56 }} value={req.scope} onChange={(e) => setReq({ ...req, scope: e.target.value })} placeholder="Qué incluye el trabajo, dónde y en qué condiciones…" /></Field>
          <Field label="Partidas (cantidades y unidades)">
            <div className="req-lines">
              {req.lines.map((l, i) => {
                const set = (patch: Partial<FormLine>) => setReq({ ...req, lines: req.lines.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
                return (
                  <div className="req-line" key={i}>
                    <input className="input" aria-label="Concepto" placeholder="Concepto" value={l.concept} onChange={(e) => set({ concept: e.target.value })} />
                    <input className="input input--mono" aria-label="Cantidad" inputMode="decimal" placeholder="Cant." value={l.quantity} onChange={(e) => set({ quantity: e.target.value })} />
                    <input className="input" aria-label="Unidad" maxLength={12} placeholder="Unidad" value={l.unit} onChange={(e) => set({ unit: e.target.value })} />
                    <button type="button" className="btn btn--ghost btn--sm" aria-label="Quitar partida" onClick={() => setReq({ ...req, lines: req.lines.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button>
                  </div>
                );
              })}
              <div className="ally__chips">
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setReq({ ...req, lines: [...req.lines, { concept: "", quantity: "", unit: "", scope_item_id: null }] })}><Icon d={I.plus} size={14} />Partida</button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={loadScopeLines}>Cargar sus alcances asignados</button>
              </div>
            </div>
          </Field>
          <div className="grid-2">
            <Field label="Sitio"><input className="input" maxLength={300} value={req.site} onChange={(e) => setReq({ ...req, site: e.target.value })} /></Field>
            <Field label="Fecha límite para responder" hint="Sin respuesta para esa fecha queda «Vencida»."><input className="input" type="date" value={req.due_date} onChange={(e) => setReq({ ...req, due_date: e.target.value })} /></Field>
          </div>
          <div className="grid-2">
            <Field label="Inclusiones"><textarea className="textarea" style={{ minHeight: 48 }} value={req.inclusions} onChange={(e) => setReq({ ...req, inclusions: e.target.value })} placeholder="Incluye transporte, canaletas…" /></Field>
            <Field label="Exclusiones"><textarea className="textarea" style={{ minHeight: 48 }} value={req.req_exclusions} onChange={(e) => setReq({ ...req, req_exclusions: e.target.value })} placeholder="No incluye obra civil ni permisos…" /></Field>
          </div>
          <Field label="Adjuntos" hint="Planos, fotos o especificaciones para el aliado (PDF o imagen, máx. 10 MB).">
            <div className="ally__chips">
              {req.attachments.map((x) => (
                <span key={x.id}><a href={x.url} target="_blank" rel="noreferrer">{x.filename}</a><button type="button" className="ally__chip-x" aria-label={`Quitar ${x.filename}`} onClick={() => setReq({ ...req, attachments: req.attachments.filter((y) => y.id !== x.id) })}>×</button></span>
              ))}
              <label className="btn btn--ghost btn--sm"><Icon d={I.upload} size={14} />Adjuntar<input type="file" accept="application/pdf,image/*" hidden onChange={(e) => { attach(e.target.files?.[0], "req"); e.target.value = ""; }} /></label>
            </div>
          </Field>
          <div className="grid-2">
            <Field label="Responsable interno de seguimiento" hint="Quien de Crimson persigue la respuesta.">
              <select className="select" value={req.responsible_id} onChange={(e) => setReq({ ...req, responsible_id: e.target.value })}>
                <option value="">Sin asignar</option>
                {me && !sellers.some((s) => s.id === me.user.id) && <option value={me.user.id}>{me.user.full_name}</option>}
                {/* el responsable actual aunque ya no este en la lista (inactivo): si no, el select diria "Sin asignar" */}
                {req.responsible_id && req.responsible_id !== String(me?.user.id) && !sellers.some((s) => String(s.id) === req.responsible_id) && <option value={req.responsible_id}>{req.responsible || `Usuario ${req.responsible_id}`}</option>}
                {sellers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
            <Field label="Cómo responderá">
              <select className="select" value={req.channel} onChange={(e) => setReq({ ...req, channel: e.target.value })}>
                {Object.entries(CHANNELS).filter(([k]) => k !== "portal" || req.ally_portal).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
          </div>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setReq(null)}>Cancelar</button>
            {(!req.id || req.stored_status === "borrador") && <button className="btn btn--soft btn--sm" disabled={req.what.trim().length < 2 || busy} onClick={() => saveReq(req.id ? null : "borrador")}>Guardar borrador</button>}
            {(!req.id || req.stored_status === "borrador") && <button className="btn btn--crimson btn--sm" disabled={req.what.trim().length < 2 || busy} onClick={() => saveReq("enviado")}>Enviar al aliado</button>}
            {req.id && req.stored_status !== "borrador" && <button className="btn btn--crimson btn--sm" disabled={req.what.trim().length < 2 || busy} onClick={() => saveReq(null)}>Guardar cambios</button>}
          </div>
        </div>
      )}

      {offer && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>Registrar la oferta de {offer.ally}</b><span className="muted">«{offer.what}» · la que llegó por fuera del portal. Es costo: solo la ve quien ve costos.</span></div>
          <div className="tabs" role="radiogroup" aria-label="Forma de la oferta">
            <button type="button" role="radio" aria-checked={offer.mode === "monto"} className={offer.mode === "monto" ? "is-active" : ""} onClick={() => setOffer({ ...offer, mode: "monto" })}>Monto total</button>
            <button type="button" role="radio" aria-checked={offer.mode === "lineas"} className={offer.mode === "lineas" ? "is-active" : ""} onClick={() => setOffer({ ...offer, mode: "lineas" })}>Desglose</button>
          </div>
          {offer.mode === "monto" ? (
            <Field label="Monto (sin IVA)"><input className="input input--mono" inputMode="decimal" value={offer.amount} onChange={(e) => setOffer({ ...offer, amount: e.target.value })} placeholder="0.00" /></Field>
          ) : (
            <div className="req-lines">
              {offer.lines.map((l, i) => {
                const set = (patch: Partial<typeof l>) => setOffer({ ...offer, lines: offer.lines.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
                return (
                  <div className="req-line req-line--price" key={i}>
                    <input className="input" aria-label="Concepto" placeholder="Concepto" value={l.concept} onChange={(e) => set({ concept: e.target.value })} />
                    <input className="input input--mono" aria-label="Cantidad" inputMode="decimal" placeholder="Cant." value={l.quantity} onChange={(e) => set({ quantity: e.target.value })} />
                    <input className="input" aria-label="Unidad" placeholder="Unidad" maxLength={12} value={l.unit} onChange={(e) => set({ unit: e.target.value })} />
                    <input className="input input--mono" aria-label="Precio unitario" inputMode="decimal" placeholder="Precio unit." value={l.unit_price} onChange={(e) => set({ unit_price: e.target.value })} />
                    <button type="button" className="btn btn--ghost btn--sm" aria-label="Quitar línea" onClick={() => setOffer({ ...offer, lines: offer.lines.filter((_, j) => j !== i) })}><Icon d={I.x} size={14} /></button>
                  </div>
                );
              })}
              <div className="ally__chips">
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOffer({ ...offer, lines: [...offer.lines, { concept: "", quantity: "1", unit: "", unit_price: "" }] })}><Icon d={I.plus} size={14} />Línea</button>
                {offerTotal != null && <span className="money">Total {fmtMoney(offerTotal, offer.currency)}</span>}
              </div>
            </div>
          )}
          <div className="grid-3">
            <Field label="Moneda"><select className="select" value={offer.currency} onChange={(e) => setOffer({ ...offer, currency: e.target.value })}><option value="CRC">CRC</option><option value="USD">USD</option></select></Field>
            <Field label="Vigencia de la oferta"><input className="input" type="date" value={offer.valid_until} onChange={(e) => setOffer({ ...offer, valid_until: e.target.value })} /></Field>
            <Field label="Llegó"><select className="select" value={offer.channel} onChange={(e) => setOffer({ ...offer, channel: e.target.value })}>{Object.entries(CHANNELS).filter(([k]) => k !== "portal").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          </div>
          <div className="grid-2">
            <Field label="Condiciones"><textarea className="textarea" style={{ minHeight: 48 }} value={offer.conditions} onChange={(e) => setOffer({ ...offer, conditions: e.target.value })} placeholder="Forma de pago, plazos, garantía…" /></Field>
            <Field label="Exclusiones del aliado"><textarea className="textarea" style={{ minHeight: 48 }} value={offer.exclusions} onChange={(e) => setOffer({ ...offer, exclusions: e.target.value })} /></Field>
          </div>
          <Field label="Adjuntos de la oferta">
            <div className="ally__chips">
              {offer.attachments.map((x) => (
                <span key={x.id}><a href={x.url} target="_blank" rel="noreferrer">{x.filename}</a><button type="button" className="ally__chip-x" aria-label={`Quitar ${x.filename}`} onClick={() => setOffer({ ...offer, attachments: offer.attachments.filter((y) => y.id !== x.id) })}>×</button></span>
              ))}
              <label className="btn btn--ghost btn--sm"><Icon d={I.upload} size={14} />Adjuntar<input type="file" accept="application/pdf,image/*" hidden onChange={(e) => { attach(e.target.files?.[0], "offer"); e.target.value = ""; }} /></label>
            </div>
          </Field>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setOffer(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={busy || (offer.mode === "monto" ? !offer.amount : !offer.lines.some((l) => l.concept.trim() && l.unit_price !== ""))} onClick={saveOffer}>Registrar oferta</button>
          </div>
        </div>
      )}

      {decision && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>{decision.action === "aceptar" ? "Aceptar" : "Rechazar"} la oferta de {decision.ally}</b><span className="muted">«{decision.what}»</span></div>
          {decision.action === "aceptar" && (
            <>
              <p className="muted" style={{ fontSize: 13, margin: 0 }}>Se guarda el acuerdo (precio, moneda, vigencia, condiciones y exclusiones) como versión 1. Los cambios posteriores crean otra versión con quién, cuándo y qué cambió.</p>
              <label className="pf-bool"><input type="checkbox" checked={decision.shared} onChange={(e) => setDecision({ ...decision, shared: e.target.checked })} />Compartir el monto acordado con los demás aliados (afecta el presupuesto común)</label>
            </>
          )}
          <Field label="Nota (opcional)"><input className="input" maxLength={400} value={decision.note} onChange={(e) => setDecision({ ...decision, note: e.target.value })} /></Field>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setDecision(null)}>Cancelar</button>
            <button className={`btn btn--sm ${decision.action === "aceptar" ? "btn--crimson" : "btn--soft"}`} disabled={busy} onClick={saveDecision}>{decision.action === "aceptar" ? "Aceptar oferta" : "Rechazar oferta"}</button>
          </div>
        </div>
      )}

      {agreement && (
        <div className="opp-newcust">
          <div className="opp-newcust__head"><b>Nueva versión del acuerdo con {agreement.ally}</b><span className="muted">Versión vigente: {agreement.current.version}. Solo lo que cambies queda en la versión nueva.</span></div>
          <div className="grid-3">
            <Field label="Precio (sin IVA)"><input className="input input--mono" inputMode="decimal" value={agreement.amount} onChange={(e) => setAgreement({ ...agreement, amount: e.target.value })} /></Field>
            <Field label="Moneda"><select className="select" value={agreement.currency} onChange={(e) => setAgreement({ ...agreement, currency: e.target.value })}><option value="CRC">CRC</option><option value="USD">USD</option></select></Field>
            <Field label="Vigencia"><input className="input" type="date" value={agreement.valid_until} onChange={(e) => setAgreement({ ...agreement, valid_until: e.target.value })} /></Field>
          </div>
          <div className="grid-2">
            <Field label="Condiciones"><textarea className="textarea" style={{ minHeight: 48 }} value={agreement.conditions} onChange={(e) => setAgreement({ ...agreement, conditions: e.target.value })} /></Field>
            <Field label="Exclusiones"><textarea className="textarea" style={{ minHeight: 48 }} value={agreement.exclusions} onChange={(e) => setAgreement({ ...agreement, exclusions: e.target.value })} /></Field>
          </div>
          <label className="pf-bool"><input type="checkbox" checked={agreement.shared} onChange={(e) => setAgreement({ ...agreement, shared: e.target.checked })} />Compartir el monto acordado con los demás aliados (afecta el presupuesto común)</label>
          <Field label="Por qué cambia (opcional)"><input className="input" maxLength={400} value={agreement.note} onChange={(e) => setAgreement({ ...agreement, note: e.target.value })} placeholder="Se amplió a 10 cámaras" /></Field>
          <div className="ally__form-foot">
            <button className="btn btn--ghost btn--sm" onClick={() => setAgreement(null)}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={busy || !agreement.amount} onClick={saveAgreement}>Guardar versión nueva</button>
          </div>
        </div>
      )}
      {deal && <DealTerms participationId={deal.id} name={deal.name} onClose={() => setDeal(null)} />}
    </section>
  );
}

/* Una solicitud: la petición, su entrega (a quién, si la vio, cómo responde), la oferta y el acuerdo con versiones. */
function RequestItem({ r, a, canEdit, seesCosts, onEdit, onSend, onRemove, onOffer, onDecide, onAgreement }: {
  r: CostRequest; a: Ally; canEdit: boolean; seesCosts: boolean; onEdit: () => void; onSend: () => void; onRemove: () => void; onOffer: () => void;
  onDecide: (a: "aceptar" | "rechazar") => void; onAgreement: () => void;
}) {
  const st = r.status;
  const stored = r.stored_status || r.status;
  const dl = r.delivery;
  const versions = (r.agreements || []).slice().sort((x, y) => y.version - x.version);
  const cerrada = stored === "aceptado" || stored === "rechazado";
  return (
    <li className={r.overdue ? "is-late" : r.pending ? "is-pending" : ""}>
      <div className="ally__req-top">
        <b>{r.what}</b>
        <span className={`badge badge--${STATE_BADGE[st] || "info"}`}>{r.status_label || COST_STATES[st] || st}</span>
      </div>
      <div className="meta ally__req-meta">
        <span>Seguimiento: {r.responsible || "sin asignar"}</span>
        <span>{r.due_date ? `Límite ${fmtDate(r.due_date)}` : "Sin fecha límite"}</span>
        {r.source === "aliado" && <span>Enviada por el aliado por iniciativa</span>}
      </div>
      {dl && stored !== "borrador" && r.source !== "aliado" && (
        <div className="meta ally__req-meta">
          <span>Enviada a {dl.to}{dl.sent_at ? ` el ${fmtTs(dl.sent_at)}` : ""}</span>
          <span>{dl.seen_at ? `La vio ${dl.seen_by ? `${dl.seen_by} ` : ""}el ${fmtTs(dl.seen_at)}` : "Todavía no la abre"}</span>
          <span>Responde {dl.channel_label}</span>
          {r.responded_at && <span>Respondida el {fmtTs(r.responded_at)}{r.responded_by ? ` · ${r.responded_by}` : ""}</span>}
        </div>
      )}
      {stored === "borrador" && <p className="meta" style={{ margin: 0 }}>Borrador: {a.name} todavía no la ve.</p>}
      {(r.scope || (r.req_lines || []).length > 0 || r.site || r.inclusions || r.req_exclusions || (r.req_attachments || []).length > 0) && (
        <details className="req-detail">
          <summary className="meta">Lo que se pidió</summary>
          {r.scope && <p className="ally__scope">Alcance: {r.scope}</p>}
          {(r.req_lines || []).length > 0 && (
            <ul className="ally__scope" style={{ margin: "4px 0", paddingLeft: 18 }}>
              {(r.req_lines || []).map((l, i) => <li key={i}>{l.concept}{l.quantity ? ` · ${Number(l.quantity).toLocaleString("es-CR")} ${l.unit || ""}` : ""}</li>)}
            </ul>
          )}
          {r.site && <p className="ally__scope">Sitio: {r.site}</p>}
          {r.inclusions && <p className="ally__scope">Incluye: {r.inclusions}</p>}
          {r.req_exclusions && <p className="ally__scope">No incluye: {r.req_exclusions}</p>}
          {(r.req_attachments || []).length > 0 && <div className="ally__chips">{(r.req_attachments || []).map((x) => <a key={x.id} href={x.url} target="_blank" rel="noreferrer">{x.filename}</a>)}</div>}
        </details>
      )}
      {r.costs_visible && r.amount != null && (
        <div className="req-offer">
          <span className="meta">Oferta del aliado</span>
          <div className="meta ally__req-meta">
            <span className="money">{fmtMoney(r.amount, r.currency)}</span>
            {r.valid_until && <span>Vigente hasta {fmtDate(r.valid_until)}</span>}
          </div>
          {r.conditions && <p className="ally__scope">Condiciones: {r.conditions}</p>}
          {r.exclusions && <p className="ally__scope">Excluye: {r.exclusions}</p>}
          {(r.lines || []).length > 0 && (
            <ul className="ally__scope" style={{ margin: "4px 0", paddingLeft: 18 }}>
              {(r.lines || []).map((l, i) => <li key={i}>{l.concept}: {Number(l.quantity)} {l.unit || ""} × {fmtMoney(l.unit_price, r.currency)}</li>)}
            </ul>
          )}
          {(r.attachments || []).length > 0 && <div className="ally__chips">{(r.attachments || []).map((x) => <a key={x.id} href={x.url} target="_blank" rel="noreferrer">{x.filename}</a>)}</div>}
          {r.decision_note && <p className="meta" style={{ margin: 0 }}>Nota de la decisión: {r.decision_note}</p>}
        </div>
      )}
      {!r.costs_visible && r.has_offer && <p className="meta" style={{ margin: 0 }}>El aliado ya mandó su oferta (el monto lo ve quien ve costos).</p>}
      {versions.length > 0 && (
        <details className="req-detail" open={versions[0].status === "vigente"}>
          <summary className="meta">Acuerdo · versión {versions[0].version} ({versions[0].status}){versions[0].shared ? " · compartido con los aliados" : " · no se comparte"}</summary>
          <ol className="agreement-versions">
            {versions.map((g) => (
              <li key={g.id}>
                <b>v{g.version}</b> <span className={`badge badge--${g.status === "vigente" ? "ok" : "muted"}`}>{g.status}</span>{" "}
                <span className="money">{fmtMoney(g.amount, g.currency)}</span>
                {g.valid_until && <span className="meta"> · vigente hasta {fmtDate(g.valid_until)}</span>}
                <div className="meta">{fmtTs(g.created_at)}{g.created_by ? ` · ${g.created_by}` : ""} · {g.changes.join(" · ") || "—"}{g.note ? ` · ${g.note}` : ""}</div>
              </li>
            ))}
          </ol>
        </details>
      )}
      {canEdit && (seesCosts || !cerrada) && (
        <div className="ally__req-actions">
          {stored === "borrador" && <button className="btn btn--soft btn--sm" onClick={onSend}>Enviar</button>}
          {seesCosts && !cerrada && stored !== "borrador" && <button className="btn btn--ghost btn--sm" onClick={onOffer}>{r.amount != null ? "Corregir oferta" : "Registrar oferta recibida"}</button>}
          {seesCosts && !cerrada && r.amount != null && <button className="btn btn--soft btn--sm" onClick={() => onDecide("aceptar")}>Aceptar</button>}
          {seesCosts && stored !== "borrador" && stored !== "rechazado" && <button className="btn btn--ghost btn--sm" onClick={() => onDecide("rechazar")}>Rechazar</button>}
          {seesCosts && stored === "aceptado" && versions[0]?.status === "vigente" && <button className="btn btn--ghost btn--sm" onClick={onAgreement}>Nueva versión del acuerdo</button>}
          {!cerrada && <button className="btn btn--ghost btn--sm" onClick={onEdit}>Editar petición</button>}
          <button className="btn btn--ghost btn--sm" onClick={onRemove}>Eliminar</button>
        </div>
      )}
    </li>
  );
}

/** A2: relaciones concretas de la oportunidad y transparencia con los aliados (punto 10). */
export function RelationsCard({ oppId, relations, allies, canEdit, seesCosts, onChange }: {
  oppId: number; relations: Relations; allies: Ally[]; canEdit: boolean; seesCosts: boolean; onChange: () => void;
}) {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const send = async (json: Record<string, unknown>, ok: string, path = "relations") => {
    if (busy) return;
    setBusy(true);
    try { await api(`/opportunities/${oppId}/${path}`, { method: "PUT", json }); toast(ok); onChange(); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo guardar", "bad"); }
    finally { setBusy(false); }
  };
  const payerValue = relations.payer.participation_id ? `p:${relations.payer.participation_id}` : relations.payer.is_end_customer ? "end" : "keep";
  const setPayer = (v: string) => {
    if (v === "end") send({ payer: "end_customer" }, "Le paga a Crimson: el cliente final");
    else if (v.startsWith("p:")) send({ payer: "participation", payer_participation_id: Number(v.slice(2)) }, "Quién le paga a Crimson actualizado");
  };
  return (
    <section className="ally-panel" aria-label="Relaciones de la oportunidad">
      <div className="ally-panel__head">
        <b>Relaciones de la oportunidad</b>
        <span className="meta">Una misma empresa puede ser aliado aquí y a la vez quien le paga a Crimson, sin otra ficha.</span>
      </div>
      <div className="grid-2 rel-grid">
        <Field label="Cliente final"><input className="input" readOnly value={relations.end_customer?.name || "—"} title="Quien recibe la solución (se cambia en Editar)" /></Field>
        <Field label="Aliado comercial (quien originó)">
          {canEdit ? (
            <select className="select" disabled={busy} value={relations.origin?.participation_id ?? ""} onChange={(e) => send({ origin_participation_id: e.target.value ? Number(e.target.value) : null }, "Aliado comercial actualizado")}>
              <option value="">Ninguno (la trajo Crimson)</option>
              {allies.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          ) : <input className="input" readOnly value={relations.origin?.name || "Ninguno (la trajo Crimson)"} />}
        </Field>
        <Field label="Responsable de ejecución">
          {canEdit ? (
            <select className="select" disabled={busy} value={relations.executor.participation_id ?? ""} onChange={(e) => send({ executor_participation_id: e.target.value ? Number(e.target.value) : null }, "Responsable de ejecución actualizado")}>
              <option value="">Crimson</option>
              {allies.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          ) : <input className="input" readOnly value={relations.executor.name} />}
        </Field>
        <Field label="Quién le paga a Crimson" hint={relations.billing_line}>
          {canEdit ? (
            <select className="select" disabled={busy} value={payerValue} onChange={(e) => setPayer(e.target.value)}>
              {payerValue === "keep" && <option value="keep">{relations.payer.name || "Sin definir"}</option>}
              {relations.end_customer?.id != null && <option value="end">El cliente final{relations.end_customer?.name ? ` (${relations.end_customer.name})` : ""}</option>}
              {allies.map((a) => <option key={a.id} value={`p:${a.id}`}>{a.name}{a.company_kind === "supplier" ? " (proveedor: no se puede)" : ""}</option>)}
            </select>
          ) : <input className="input" readOnly value={relations.payer.name || "Sin definir"} />}
        </Field>
      </div>
      <div className="rel-transparency">
        <label className="pf-bool">
          <input type="checkbox" checked={relations.open_costs} disabled={!canEdit || !seesCosts || busy} onChange={(e) => send({ open_costs: e.target.checked }, e.target.checked ? "Trabaja con costos abiertos" : "Costos abiertos apagado", "transparency")} />
          Trabajar con costos abiertos
        </label>
        <p className="meta" style={{ margin: 0 }}>
          Por defecto los aliados de la oportunidad ven los aportes, responsables, alcances, avances y los montos acordados que afectan el presupuesto común.
          Con costos abiertos ven además el desglose de la oferta original de los demás participantes. Nunca ven los costos internos ni el margen de Crimson.
          {!seesCosts && " Lo decide quien ve costos."}
        </p>
      </div>
    </section>
  );
}

/** Solicitudes de precio pendientes (Mis pendientes): las mismas filas que cuenta la tarjeta del inicio. */
export function PendingCosts({ rows, onOpen }: { rows: PendingCost[] | null; onOpen: (oppId: number) => void }) {
  if (rows === null || rows.length === 0) return null;
  return (
    <div className="ally-pending">
      <div className="list-head"><b style={{ fontSize: 14 }}>Solicitudes de precio a aliados</b><span className="muted" style={{ fontSize: 13 }}>Borradores sin enviar, enviadas sin respuesta o respondidas sin decidir.</span></div>
      <table className="table table--stack">
        <thead><tr><th>Límite</th><th>Qué se pide</th><th>Aliado</th><th>Oportunidad</th><th>Seguimiento</th><th>Estado</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => onOpen(r.opportunity_id)}>
            <td data-label="Límite" style={{ whiteSpace: "nowrap", color: r.overdue ? "var(--bad)" : undefined }}>{r.due_date ? fmtDate(r.due_date) : "—"}{r.overdue && <div className="meta" style={{ color: "var(--bad)" }}>Vencida</div>}</td>
            <td data-label="Qué se pide" style={{ fontWeight: 600 }}>{r.what}</td>
            <td data-label="Aliado">{r.ally}{r.delivery?.seen_at && <div className="meta">La vio el {fmtTs(r.delivery.seen_at)}</div>}</td>
            <td data-label="Oportunidad"><span className="mono muted">{r.opportunity_number}</span> · {r.opportunity_title}</td>
            <td data-label="Seguimiento" className="muted">{r.responsible || "—"}</td>
            <td data-label="Estado"><span className={`badge badge--${STATE_BADGE[r.status] || "info"}`}>{r.status_label || COST_STATES[r.status] || r.status}</span></td>
            <td className="num stack-actions"><button className="btn btn--ghost btn--sm" onClick={(e) => { e.stopPropagation(); onOpen(r.opportunity_id); }}>Abrir</button></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
