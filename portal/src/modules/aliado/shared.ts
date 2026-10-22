/* Tipos y textos del portal de aliados. Todo lo que llega aquí ya viene filtrado por el servidor (solo lo que la
   empresa aliada tiene EN COMÚN con Crimson, sin costos ni montos de Crimson): el portal no decide qué es de quién. */
import { useSession } from "../../app/session";
import { ApiError, type AllyCompanyMe } from "../../lib/api";

/** Quién mira el portal: el aliado real o el admin en "Ver como este aliado" (solo lectura, datos reales). */
export function useAlly() {
  const { me, allyPreview, role, allows } = useSession();
  const preview = !!allyPreview;
  const company: AllyCompanyMe | null = preview
    ? {
        id: allyPreview!.company_id, name: allyPreview!.name || "Aliado", state: allyPreview!.state || "activo",
        suspended_reason: allyPreview!.suspended_reason ?? null, features: allyPreview!.features || { ganancias: true },
      }
    : me?.ally_company ?? null;
  return { preview, readOnly: preview, isTech: role === "tecnico_aliado", role, company, can: (a: string) => allows(`aliado.${a}`), personName: allyPreview?.user_name ?? null };
}

export type Req = {
  id: number; participation_id: number; opportunity_id: number; what: string; due_date: string | null; status: string; source: "crimson" | "aliado";
  amount: string | null; currency: string; valid_until: string | null; exclusions: string | null;
  lines: { concept: string; quantity: string; unit: string | null; unit_price: string }[]; attachments: { id: number; url: string; filename: string }[];
  responded_at: string | null; responsible: string | null; can_respond: boolean; created_at: string;
  opportunity?: { id: number; number: string; title: string } | null; role?: string;
};
export type OppRow = {
  id: number; number: string; title: string; kind: string; status: string; solution: string | null; customer: string | null; end_customer: string | null;
  site: string | null; crimson_owner: string | null; my_roles: string[]; pending_requests: number; project: { id: number; number: string; status: string } | null; created_at: string;
  /** propuesta por su empresa (21/10, ítem 16): pendiente | aprobada | rechazada; null si la abrió Crimson */
  approval?: "pendiente" | "aprobada" | "rechazada" | null; approval_label?: string | null; proposed_by_me?: boolean;
};
export type Contact = { name?: string | null; email?: string | null; phone?: string | null };
export type OppFull = OppRow & {
  contact: { name: string | null; phone: string | null };
  participations: { id: number; role: string; scope: string | null; scope_lines: string[]; contacts: Record<string, Contact> }[];
  requests: Req[]; surveys: { id: number; number: string; kind: string; status: string; review_status: string | null }[]; can_survey: boolean;
};
export type ProjectRow = {
  id: number; number: string; name: string; status: string; customer: string | null; end_customer: string | null; site: string | null;
  start_date: string | null; end_date: string | null; delivered_at: string | null; orders_total: number; orders_done: number; my_roles: string[];
};
export type Earnings = { moneda?: string; totales: Record<string, string | number>; movimientos?: { concepto?: string; monto?: string; estado?: string; fecha?: string }[] } | null;
export type ProjectFull = ProjectRow & {
  scope: string | null; my_scope_lines: string[]; orders: { id: number; number: string; title: string; kind: string; status: string; scheduled_at: string | null; technician: string | null }[];
  requests: Req[]; lo_que_recibo: Earnings; assets_count: number; my_participations: { id: number; role: string }[];
};
export type Obs = { id: number; round: number; point_code: string | null; text: string; created_by: string; created_at: string; resolved: boolean; resolved_at: string | null; resolved_by: string | null; resolution: string | null };
export type Point = { id?: number; code: string; kind?: string | null; label: string | null; data: Record<string, unknown>; data_status: Record<string, string> | null; photos: string[]; notes: string | null };
export type Item = { id?: number; product_id: number | null; name: string; quantity: string | number; unit: string; note: string | null; kind: string | null };
export type SurveyRow = {
  id: number; number: string; kind: string; kind_label: string; status: string; customer: string | null; site: string | null; opportunity: { id: number; number: string; title: string; status: string } | null;
  technician_id: number | null; technician: string | null; visit_date: string | null; points_count: number; created_at: string; sent_at: string | null;
  review_status: string | null; review_label: string | null; open_observations: number; observations: Obs[]; pending_review: { label: string } | null; can_edit: boolean;
  photos: string[]; notes: string | null; contact: Record<string, string> | null; received_by: string | null; labor: Record<string, { label: string; people: number; days: string }>;
  /** solo los de su equipo (los técnicos de Crimson de la visita no salen al portal; la API los conserva) */
  visit_tech_ids: number[]; visit_techs: string[];
};
export type SurveyFull = SurveyRow & { common: Record<string, unknown> | null; points: Point[]; items: Item[] };
export type Material = { id: number; product_id: number | null; name: string; quantity: string; unit: string; planned: string };
export type OrderRow = {
  id: number; number: string; title: string; kind: string; status: string; site: string | null; scheduled_at: string | null; customer: string | null; project: string | null;
  technician_id: number | null; technician: string | null; helpers: number[]; arrived_at: string | null; started_at: string | null; finished_at: string | null; hours: number | null;
  tasks: { text: string; done: boolean }[]; photos: string[]; notes: string | null; customer_signature: string | null;
  crew: { id: number; name: string | null; role: string }[]; running: { user_id: number; user: string | null; started_at: string }[]; can_act: boolean; materials?: Material[];
  /** tiempo de su equipo corriendo de más (se olvidaron de terminar la jornada) */
  stale?: boolean;
};
export type TicketRow = {
  id: number; number: string; subject: string; kind: string; priority: string; status: string; level: number; customer: string | null; project: string | null;
  asset: { name: string; model: string | null; serial: string | null; location: string | null } | null; assigned_to: number | null; assigned: string | null;
  due_at: string | null; resolve_due_at: string | null; first_reply_at: string | null; resolved_at: string | null; sla_vencido: boolean; resolucion_vencida: boolean; fuera_de_tiempo: boolean;
  created_at: string; can_act: boolean;
};
export type TicketNote = { id: number; body: string; author: string; team: "crimson" | "aliado"; internal: boolean; created_at: string; photos: string[] };
export type TicketFull = TicketRow & { description: string | null; contact: { name: string | null; phone: string | null }; photos: string[]; solution: string | null; hours: string; notes: TicketNote[] };
export type Tech = { id: number; name: string; role: string; active: boolean };
export type MyDay = { ordenes: OrderRow[]; levantamientos: SurveyRow[]; tickets: TicketRow[] };

export const ROL_PARTICIPACION: Record<string, string> = {
  contratante: "Contratante", referido: "Referido", subcontratista: "Subcontratista", suministro: "Suministro", configuracion: "Configuración", software: "Software",
};
export const ESTADO_OPP: Record<string, string> = {
  nuevo: "Nueva", contactado: "Contactado", requiere_visita: "Requiere visita", levantamiento: "En levantamiento", cotizando: "Cotizando", enviada: "Cotización enviada",
  negociacion: "Negociación", ganada: "Ganada", perdida: "Perdida",
};
export const ESTADO_PRECIO: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  solicitado: { label: "Crimson espera su precio", tone: "warn" },
  recibido: { label: "Enviado · en revisión de Crimson", tone: "info" },
  aprobado: { label: "Aprobado", tone: "ok" },
  rechazado: { label: "No aprobado", tone: "muted" },
};
export const TONO_APROBACION: Record<string, "ok" | "warn" | "muted"> = { pendiente: "warn", aprobada: "ok", rechazada: "muted" };
export const ESTADO_TICKET: Record<string, string> = {
  nuevo: "Nuevo", asignado: "Asignado", en_proceso: "En proceso", esperando_cliente: "Esperando al cliente", resuelto: "Resuelto", cerrado: "Cerrado",
};
export const PRIORIDAD: Record<string, string> = { baja: "Baja", media: "Media", alta: "Alta", critica: "Crítica" };
export const TIPO_TICKET: Record<string, string> = { soporte: "Soporte", garantia: "Garantía", mantenimiento: "Mantenimiento", visita: "Visita", consulta: "Consulta", instalacion: "Instalación" };

/** 404 del portal = no existe o ya no está en común; 403 = el mensaje de la API (rol o empresa suspendida). */
export const errorText = (e: unknown) =>
  e instanceof ApiError && e.status === 404 ? "No encontrado o ya no está en común con Crimson." : e instanceof Error ? e.message : "No se pudo cargar";

export const fmtWhen = (s: string | null | undefined) => {
  if (!s) return "—";
  const d = new Date(/([zZ]|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`);
  return d.toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true, timeZone: "America/Costa_Rica" });
};
