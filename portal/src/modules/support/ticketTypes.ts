/* Tipos y etiquetas de la mesa de soporte (los comparten la bandeja, la tarjeta, el detalle y el tablero). */
import type { Semaforo } from "./ticketTime";

export type Note = { id: number; body: string; internal: boolean; share_ally?: boolean; photos: string[]; user: string; from_client?: boolean; created_at: string };
export type HistoryItem = { at: string; user: string | null; action: string; label: string };
export type LastFollow = { at: string; user: string | null; body: string; internal: boolean; from_client: boolean };
export type Ally = { key: string; name: string; role?: string };
export type Ticket = {
  id: number; number: string; subject: string; kind: string; channel: string | null; level: number; priority: string; status: string;
  customer_id: number | null; customer: string | null; asset_id: number | null; asset: string | null; project_id: number | null; project: string | null;
  work_order_id: number | null; assigned_to: number | null; assigned: string | null; opened_by: number | null; opened_by_name: string | null;
  due_at: string | null; first_reply_at: string | null; resolved_at: string | null; closed_at: string | null; sla_vencido: boolean; resolve_due_at: string | null;
  resolucion_vencida: boolean; fuera_de_tiempo: boolean; contact?: Record<string, string | null>;
  sla: { respuesta_h: number | null; resolucion_h: number | null; origen: string | null; origen_label: string; contrato: string | null } | null;
  hours: string; billable: boolean; amount: string | null; tags: string[]; created_at: string;
  semaforo: Semaforo; grupo: string; next_step: string | null; next_step_at: string | null; next_step_vencido: boolean;
  last_follow: LastFollow | null; last_activity_at: string; allies: Ally[]; archived_at: string | null; trashed_at: string | null;
  description?: string | null; solution?: string | null; photos?: string[]; notes?: Note[]; history?: HistoryItem[];
  /** empresa aliada que lo atiende (asignacion explicita; la ve en su portal de aliados) */
  ally_company_id?: number | null; ally_company?: string | null;
  /** estado de la visita (OT) del ticket, solo en el detalle: "por_programar" = sin tecnico todavia, se destaca */
  work_order_status?: string | null;
};
export type Resumen = { abiertos: number; vencidos: number; por_vencer: number; sin_responsable: number; seguimiento_hoy: number; sin_movimiento: number; asignados_a_mi: number };
export type Meta = {
  kinds: string[]; states: string[]; priorities: string[]; channels: string[]; sla_horas: Record<string, number>;
  agents: { id: number; name: string; role?: string }[]; abiertos: number; fuera_de_tiempo: number; can_see_all: boolean; can_charge?: boolean;
  resumen?: Resumen; allies?: (Ally & { abiertos: number })[]; umbral_ambar?: number; sin_movimiento_h?: number;
};

export const KIND: Record<string, string> = { soporte: "Soporte", garantia: "Garantía", mantenimiento: "Mantenimiento", visita: "Visita", instalacion: "Instalación", consulta: "Consulta" };
export const ESTADO: Record<string, { label: string; tone: string }> = {
  nuevo: { label: "Nuevo", tone: "info" }, asignado: { label: "Asignado", tone: "info" }, en_proceso: { label: "En proceso", tone: "warn" },
  esperando_cliente: { label: "Esperando al cliente", tone: "muted" }, resuelto: { label: "Resuelto", tone: "ok" }, cerrado: { label: "Cerrado", tone: "muted" },
};
export const PRIO: Record<string, string> = { baja: "var(--text-3)", media: "var(--info)", alta: "var(--warn)", critica: "var(--bad)" };
export const PRIO_LABEL: Record<string, string> = { baja: "Baja", media: "Media", alta: "Alta", critica: "Crítica" };
export const ABIERTOS = ["nuevo", "asignado", "en_proceso", "esperando_cliente"];
export const esAbierto = (t: { status: string }) => ABIERTOS.includes(t.status);
