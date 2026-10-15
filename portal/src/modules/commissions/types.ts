/* Tipos del modulo de comisiones (economia). Montos como string (Decimal de la API). */

export type Entry = {
  id: number; user_id: number | null; user: string | null; invoice: string | null; invoice_id: number | null;
  base: string; base_amount: string | null; percent: string | null; amount: string; currency: string; earned_on: string; status: string; paid_on: string | null;
  beneficiary: "vendedor" | "aliado"; beneficiary_name: string | null; ally_company_id: number | null;
  kind: "comision" | "bono" | "pago_acordado" | "ajuste"; trigger: string; description: string | null;
  project_id: number | null; project: string | null; quote_id: number | null; quote: string | null; participation_id: number | null;
  amount_crc: string; hold_reason: string | null; approved_at: string | null; approved_by: string | null;
  paid_ref: string | null; void_reason: string | null; reverses_id: number | null; can_approve: boolean;
};
export type Ledger = { rows: Entry[]; totals: { en_espera: number; pendiente: string; aprobada: string; pagada: string }; by_currency: Record<string, Record<string, string>>; total_rows: number };

export type Rule = {
  id: number; user_id: number | null; user: string | null; name: string; base: string; percent: string | null; active: boolean; notes: string | null;
  beneficiary: "vendedor" | "aliado"; ally_company_id: number | null; ally: string | null; participation_id: number | null; project: string | null;
  kind: "comision" | "bono_fin" | "bono_anticipo"; trigger: string; deal_kind: string | null; solution: string | null;
  fixed_amount: string | null; fixed_currency: string | null; committed_date: string | null; early_days: number | null;
  valid_from: string | null; valid_to: string | null; summary: string; used: boolean;
};

export type Config = {
  default_trigger_vendedor: string; default_trigger_aliado: string; default_base_vendedor: string; default_base_aliado: string;
  ally_payout_trigger: string; general_rule_roles: string[]; show_margin_base: boolean; auto_approve: boolean;
};

export type Beneficiaries = { users: { id: number; name: string; role: string; role_label: string; general: boolean }[]; allies: { id: number; name: string }[] };

export type Summary = {
  user: { id: number; name: string | null }; periodo: { from: string; to: string };
  ganado: { total: string; en_espera: number; pendiente: string; aprobada: string; pagada: string }; pagado: string;
  por_cobrar: { total: string; facturas: number; estimado: boolean }; en_curso: { total: string; proyectos: number; estimado: boolean };
  si_aceptan: { total: string; cotizaciones: number }; embudo: { total: string; oportunidades: number; estimado: boolean };
  historial: { mes: string; ganado: string; pagado: string }[]; reglas: { id: number; summary: string; kind: string; general: boolean }[];
  moneda: string; actualizado: string;
};

export type InboxGroup = { key: string; beneficiary: string; name: string; count: number; total_crc: string; oldest: string; has_adjustments: boolean; entries: Entry[] };
export type Inbox = { en_espera: Entry[]; por_aprobar: InboxGroup[]; por_pagar: InboxGroup[]; totals: { en_espera: number; por_aprobar_crc: string; por_pagar_crc: string } };

export type AllyMonth = { mes: string; devengado: string; pagado: string; recibido: string; acumulado: string; proyectos_completados: number; mensualidad?: string; neto?: string };
export type AllyProjectRow = {
  project_id: number; number: string; name: string; status: string; cliente: string | null;
  lo_que_pusiste: { what: string; amount: string | null; currency: string; status: string }[];
  pago_acordado: string; comision: string; bonos: string; ajustes: string; total: string; pagado: string; por_recibir: string; proyectado: string;
  committed_date: string | null; bono_anticipo_posible: { monto: string; moneda: string; fecha: string; posible: boolean } | null; completed_at: string | null;
};
export type AllyEarningsData = {
  moneda: string; empresa?: { id: number; name: string | null };
  totales: {
    recibido: string; por_recibir: string; en_revision?: number; proyectado: string; devengado: string; proyectos?: number;
    promedio_por_proyecto?: string | null; ingreso_por_hora?: string | null; horas?: number; dias_promedio_pago?: number | null; mensualidad?: string; neto?: string;
  };
  por_mes: AllyMonth[]; proyectos: AllyProjectRow[]; nota?: string;
};
export type AllyMovement = { id: number; earned_on: string; kind: string; kind_label: string; description: string; amount: string; currency: string; status: string; paid_on: string | null; project: string | null; hold_reason: string | null };
export type AllyProjectDetail = {
  project_id: number; project: { id: number; number: string; name: string; status: string; cliente: string | null; completed_at: string | null } | null;
  moneda: string; totales: { recibido: string; por_recibir: string; proyectado: string; devengado: string };
  lo_que_pusiste: { what: string; amount: string | null; currency: string; status: string }[]; movimientos: AllyMovement[];
  proyectado: { pago_acordado: string; comision: string; bonos: string; total: string };
  bono_anticipo: { monto: string; moneda: string; fecha: string; posible: boolean } | null;
};

export type NumbersRow = {
  mes?: string; cobrado_sin_iva: string; facturado_sin_iva: string; proyectos_cerrados: number; proyectos_sin_costos: number;
  venta_proyectos: string; costo_directo: string; utilidad_bruta: string; comisiones_vendedores: string; comisiones_aliados: string; pagos_aliados: string;
  utilidad_despues_comisiones: string; margen_neto: number;
};
export type Numbers = {
  periodo: { from: string; to: string }; meses: NumbersRow[]; totales: NumbersRow;
  por_vendedor: { user_id: number; name: string | null; facturado_sin_iva: string; cobrado_sin_iva: string; comisiones: string; proyectos_cerrados: number; utilidad_bruta_proyectos: string }[];
  por_aliado: { ally_company_id: number; name: string | null; proyectos: number; venta_proyectos: string; pagado_al_aliado: string; comisiones_bonos: string; utilidad_crimson: string; margen: number }[];
  moneda: string;
};

export type Deal = {
  participation: { id: number; name: string; role: string; project_id: number | null; opportunity_id: number | null; ally_company_id: number | null };
  pago_acordado: { id: number; what: string; amount: string; currency: string; status: string }[];
  commission: { rule_id: number; base: string; percent: string | null; fixed_amount: string | null; currency: string; trigger: string } | null;
  bonus_complete: { rule_id: number; amount: string | null; percent: string | null; currency: string } | null;
  bonus_early: { rule_id: number; amount: string | null; percent: string | null; currency: string; committed_date: string; early_days: number } | null;
  general_rule: { id: number; summary: string } | null;
  projection: { pago_acordado: string; comision: string; bonos: string; bono_anticipo: string; total: string; currency: string; base_price: string };
  movimientos: Entry[];
};

export const ESTADO: Record<string, { label: string; tone: string }> = {
  en_espera: { label: "En espera", tone: "muted" }, pendiente: { label: "Pendiente", tone: "warn" }, aprobada: { label: "Aprobada", tone: "info" },
  pagada: { label: "Pagada", tone: "ok" }, anulada: { label: "Anulada", tone: "muted" },
};
export const KIND: Record<string, string> = { comision: "Comisión", bono: "Bono", pago_acordado: "Pago acordado", ajuste: "Ajuste" };
export const TRIGGER: Record<string, string> = {
  cobro: "Al cobrar (proporcional a cada pago)", cotizacion_aceptada: "Cuando el cliente acepta la cotización", proyecto_completado: "Cuando se completa el proyecto", manual: "Manual",
};
export const SOLUTION: Record<string, string> = {
  cctv: "CCTV", redes: "Redes / WiFi", acceso: "Control de acceso", asistencia: "Tiempo y asistencia", ups: "Respaldo eléctrico",
  cableado: "Cableado estructurado", anpr: "ANPR / barreras", otro: "Otro",
};
export const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
export const mesLbl = (k: string) => { const [y, m] = k.split("-"); return `${MESES[Number(m) - 1] || m} ${y.slice(2)}`; };
export const errText = (e: unknown) => (e instanceof Error ? e.message : "Error");
