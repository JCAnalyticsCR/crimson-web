/* Tipos y textos del portal del cliente. Todo lo que llega aqui ya viene filtrado por el servidor
   (solo lo de SU empresa, sin notas internas ni costos): el portal no decide que es de quien. */

export type TicketRow = {
  id: number; number: string; subject: string; kind: string; priority: string; status: string; abierto: boolean;
  asset: string | null; mine: boolean; origin: "yo" | "empresa" | "crimson"; created_at: string; updated_at: string; respuesta_antes_de: string | null; resolved_at: string | null;
};
export type Message = { id: number; body: string; photos: string[]; author: string; team: boolean; created_at: string };
export type TicketFull = TicketRow & { description: string | null; photos: string[]; solution: string | null; opened_by: string; can_comment: boolean; messages: Message[] };
export type Visit = { id: number; name: string; kind: string; every_months: number; next_date: string | null; last_done: string | null; scope: string | null };
export type Home = { customer: { name: string }; company: string; role: string; open_tickets: TicketRow[]; open_count: number; next_visit: Visit | null; assets_count: number };
export type Asset = { id: number; name: string; model: string | null; serial: string | null; location: string | null; installed_at: string | null; warranty_until: string | null; in_warranty: boolean; status: string; photos: string[] };

// estados internos dichos como los entiende un cliente
export const ESTADO: Record<string, { label: string; tone: "ok" | "warn" | "bad" | "info" | "muted" }> = {
  nuevo: { label: "Recibido", tone: "info" },
  asignado: { label: "Asignado a un técnico", tone: "info" },
  en_proceso: { label: "En atención", tone: "warn" },
  esperando_cliente: { label: "Esperando su respuesta", tone: "bad" },
  resuelto: { label: "Resuelto", tone: "ok" },
  cerrado: { label: "Cerrado", tone: "muted" },
};
export const PRIORIDAD: Record<string, string> = { baja: "Baja", media: "Media", alta: "Alta", critica: "Crítica" };

export const fmtWhen = (s: string | null | undefined) => {
  if (!s) return "—";
  const d = new Date(/([zZ]|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`);
  return d.toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true, timeZone: "America/Costa_Rica" });
};
