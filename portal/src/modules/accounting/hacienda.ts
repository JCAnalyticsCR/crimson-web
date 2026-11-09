/* Tipos y etiquetas compartidos de Contabilidad (recepcion de comprobantes, Hacienda, gastos). */

export type Mode = "ninguno" | "simulado" | "pruebas" | "produccion";

export const MODE_LABEL: Record<Mode, string> = {
  ninguno: "Desactivada",
  simulado: "Simulado",
  pruebas: "Pruebas de Hacienda",
  produccion: "Producción",
};

/** Banda bajo el encabezado cuando lo que se responde o emite no tiene validez fiscal. */
export const MODE_BAND: Partial<Record<Mode, string>> = {
  ninguno: "Modo simulado · nada se envía a Hacienda",
  simulado: "Modo simulado · nada se envía a Hacienda",
  pruebas: "Ambiente de pruebas de Hacienda · sin validez fiscal",
};

export const DOC_TYPES: Record<string, string> = {
  FE: "Factura", TE: "Tiquete", NC: "Nota de crédito", ND: "Nota de débito", FEC: "Factura de compra", FEE: "Exportación", REP: "Recibo de pago",
};

export type IvaCondition = "credito" | "credito_parcial" | "bienes_capital" | "no_credito" | "proporcional";

/* «Condición del Impuesto (IVA)» con el texto de Hacienda, como la muestra Fygaro («Genera Crédito IVA»); el codigo
   (01 a 05) queda como dato secundario. Mismas palabras que services/reception_state.IVA_LABEL. */
export const IVA_CONDITIONS: { key: IvaCondition; code: string; label: string; hint: string }[] = [
  { key: "credito", code: "01", label: "Genera crédito IVA", hint: "Código 01: el IVA se acredita completo" },
  { key: "credito_parcial", code: "02", label: "Genera crédito parcial del IVA", hint: "Código 02: indique cuánto se acredita" },
  { key: "bienes_capital", code: "03", label: "Bienes de capital", hint: "Código 03: bienes de capital" },
  { key: "no_credito", code: "04", label: "Gasto corriente no genera crédito", hint: "Código 04: el IVA pasa a ser gasto" },
  { key: "proporcional", code: "05", label: "Proporcionalidad", hint: "Código 05: se aplica la prorrata del periodo" },
];
export const ivaLabel = (k: string | null | undefined) => IVA_CONDITIONS.find((c) => c.key === k)?.label ?? (k || "—");
export const ivaCode = (k: string | null | undefined) => IVA_CONDITIONS.find((c) => c.key === k)?.code ?? null;

/* Los nombres de Fygaro en un solo lugar: si Andrés confirma que en Fygaro significan otra cosa (p. ej. que la
   «Bandeja de entrada» es la lista de correos), se cambian aquí sin rehacer pantallas. */
export const FYGARO = {
  bandeja: "Bandeja de entrada",
  recepcion: "Recepción de facturas",
  correos: "Correos",
  ficha: "Recepción de factura electrónica",
  emisor: "Información del emisor",
  estado: "Estado",
  accion: "Acción realizada",
  notificacion: "Notificación a Hacienda",
  gasto: "Información del gasto",
  clave: "Clave del documento",
  impuestos: "Total de impuestos",
  total: "Total",
  condicion: "Condición del impuesto (IVA)",
  actividad: "Código de actividad",
  acreditar: "Total impuesto por acreditar",
  aplicable: "Total de gasto aplicable",
  xml: "Documentos XML",
  bajarXml: "Descargar XML",
  /* la respuesta de Hacienda a SU confirmación (pregunta abierta a Andrés: ¿o el acuse que trae el proveedor?) */
  bajarRespuesta: "Descargar respuesta XML",
  bajarTodos: "Descargar todos",
} as const;

/** Estado en palabras (services/reception_state): el portal solo pinta label y tone. */
export type Tone = "ok" | "warn" | "bad" | "info" | "muted";
export type Estado = { key: string; label: string; tone: Tone; hint: string | null; motivo: string | null; codigo: string | null; modo: string | null; vence: string | null; alerta: string | null };

export const ACTIONS: { key: "aceptada" | "parcial" | "rechazada"; label: string }[] = [
  { key: "aceptada", label: "Aceptar" },
  { key: "parcial", label: "Aceptar parcialmente" },
  { key: "rechazada", label: "Rechazar" },
];
export const ACTION_LABEL: Record<string, string> = { aceptada: "Aceptada", parcial: "Parcial", rechazada: "Rechazada" };

export type Reception = {
  id: number; clave: string; consecutive: string | null; doc_type: string; issuer_name: string | null; issuer_id: string | null; issuer_email: string | null;
  issue_date: string | null; currency: string; fx_rate: string; subtotal: string; tax_total: string; iva_total: string; other_charges: string; total: string; total_crc: string;
  status: string; action: string | null; iva_condition: string | null; activity_code: string | null; issuer_activity_code: string | null;
  credit_amount: string | null; applicable_amount: string | null; hacienda_status: string; supplier_hacienda_status: string; hacienda_detail?: string | null; supplier_hacienda_detail_short?: string | null;
  deadline: string | null; business_days_left: number | null; has_pdf: boolean; has_supplier_response: boolean; has_response: boolean; has_hacienda_answer: boolean;
  expense_id: number | null; receiver_ok: boolean | null; origin: string; created_at: string;
  /** «Acción realizada» y «Notificación a Hacienda» como en Fygaro; hacienda_mode = modo del último intento */
  accion: Estado; notificacion: Estado; hacienda_mode: string | null;
  /* la clave coherente con su XML y avalada por el sello de Hacienda en el acuse del proveedor (services/identificador) */
  identificador?: { estado: string; tone: Tone; label: string; detalle: string | null; bloquea: boolean };
  /** solo en la lista "por revisar": la respuesta sugerida (reunion 2, pedido 4) */
  proposal?: Proposal | null;
};

/** Respuesta preparada para aprobar con un clic (services/reception_proposal.py). ready=false: se responde a mano. */
export type Proposal = {
  action: "aceptada" | "parcial" | "rechazada"; iva_condition: string; activity_code: string | null; create_expense: boolean; link_expense_id: number | null;
  category_id: number | null; category: string | null; detail: string | null; reference_id: number | null; source: "proveedor" | "predeterminado" | "nota_credito";
  ready: boolean; reasons: string[]; blockers: string[];
  /** huella de lo que se envia: si cambio entre la vista y el clic, el servidor no envia nada (409) */
  fingerprint?: string;
};

export type ResponseAttempt = {
  id: number; attempt: number; message_code: string; iva_condition: string | null; activity_code: string | null; credit_amount: string | null; applicable_amount: string | null;
  detail: string | null; consecutive: string; mode: string; status: string; hacienda_detail: string | null; sent_at: string | null; resolved_at: string | null;
  attempts: number; last_error: string | null; emitted_at: string; created_at: string;
};

export type Candidate = { id: number; date: string; description: string; total: string; currency: string; supplier: string | null; recurrence_id: number | null };

export type ReceptionDetail = Reception & {
  lines: { number: string | null; cabys: string | null; detail: string | null; quantity: string; unit: string | null; price: string; subtotal: string; tax_code: string | null; tax_rate: string | null; tax: string; total: string }[];
  reference_clave: string | null; schema_version: string | null; receiver_id: string | null;
  message: { id: number; sender: string | null; subject: string | null; received_at: string | null } | null;
  responses: ResponseAttempt[]; discarded_reason: string | null; response_detail: string | null; mode: Mode;
  can_respond: boolean; can_retry: boolean; can_reopen: boolean; can_send: boolean; can_refresh: boolean; can_expense: boolean;
  /** «Enviar en Producción»: la misma decisión guardada en Pruebas; pending_production = su último intento es de Pruebas */
  can_send_production: boolean; pending_production: boolean;
  warnings: string[]; expense_candidates: Candidate[]; proposal: Proposal | null;
  supplier_hacienda_detail: string | null;
};

export type IntakeResult = {
  nuevos: number[]; duplicados: { clave: string; id: number }[]; adjuntos: { kind: string; id: number }[];
  ignorados: { name: string; reason: string }[]; errores: { name: string; error: string }[]; sin_asignar?: { name: string; media_id: number }[];
  summary?: { name: string; kind: string; result: string | null; clave: string | null; received_document_id: number | null; error: string | null; media_id?: number }[];
};

export type InboxStatus = { source: "env" | "ajustes" | "ninguno"; mailbox: string | null; enabled: boolean; last_run_at: string | null; last_result: { messages: number; nuevos: number; duplicados: number; errores: string[] } | null };

export type HaciendaState = {
  mode: Mode; einvoice_provider: string; environment: "pruebas" | "produccion"; can_configure: boolean;
  /* con qué credenciales se emite hoy (null: no se emite a Hacienda) */
  emission_environment?: "pruebas" | "produccion" | null;
  profile: { id_type: string; tax_id: string | null; legal_name: string | null; commercial_name: string | null; email: string | null; phone_country: string; phone: string | null; province: string | null; canton: string | null; district: string | null; district_name: string | null; barrio: string | null; other_signs: string | null };
  activities: { id: number; code: string; description: string | null; is_default: boolean; active: boolean }[];
  credentials: Record<"pruebas" | "produccion", CredBlock>;
  consecutives: { code: string; label: string; group_id: number; branch: string; terminal: string; current: number; next_preview: string; fygaro_max: number | null; test_group_id: number; test_current: number }[];
  consecutives_confirmed_at: string | null;
  production: { allowed_by_server: boolean; enabled_at: string | null; effective: boolean; blocked_reason: string | null; checks: { key: string; ok: boolean; detail: string }[] };
  imap: { source: string; mailbox: string | null; last_run_at: string | null; last_result: unknown };
};
export type TestStep = { label: string; status: "ok" | "falla" | "aviso" | "no_probado"; detail: string };
export type CredBlock = {
  source: "ajustes" | "entorno" | "ninguna"; atv_user_set: boolean;
  /* de qué ambiente parece el usuario ATV (stag = pruebas, prod = producción) y el aviso si no corresponde */
  atv_user_env?: "pruebas" | "produccion" | null; atv_user_warning?: string | null; atv_password_set: boolean; p12_set: boolean; pin_set: boolean; readable: boolean;
  cert: { subject_cn: string | null; issuer_cn: string | null; serial: string | null; not_after: string | null; days_left: number | null; sha256: string | null } | null;
  last_test_at: string | null; last_test_ok: boolean | null; last_test_detail: string | null;
  /** 04/11: lo que revisó la última prueba, paso por paso */
  last_test_steps?: TestStep[] | null;
};

/** «Prueba real con Hacienda» (ambiente de Pruebas): una corrida, sin XML (solo banderas has_*) ni secretos. */
export type TestRunState = "firmado" | "enviado" | "procesando" | "aceptado" | "rechazado" | "error" | "no_concluyente" | "sin_respuesta";
export type TestRun = {
  id: number; state: TestRunState; live: boolean; steps: TestStep[]; clave: string | null; consecutive: string | null; mr_consecutive: string | null;
  cabys: string | null; con_mr: boolean; fe_estado: string | null; fe_detalle: string | null; fe_post_cause: string | null; fe_resent: boolean;
  mr_estado: string | null; mr_detalle: string | null; mr_sent_at?: string | null; fe_llego?: boolean; created_at: string; fe_body_sent_at: string | null; finished_at: string | null; last_check_at: string | null;
  polls: number; has_fe_xml: boolean; has_fe_respuesta: boolean; has_mr_xml: boolean; has_mr_respuesta: boolean; created_by_name: string | null;
  /** ajustes: «Prueba real con Hacienda»; tienda_pruebas: factura de prueba de un pedido de la Tienda de pruebas */
  origin?: "ajustes" | "tienda_pruebas"; amount?: string | null;
};
export type TestRunList = { runs: TestRun[]; can_run: boolean; can_configure: boolean; blocked_reason: string | null; cabys_sugerido: string | null };

/** Plazo: "quedan N días hábiles" / "vence hoy" / "vencido". */
export const plazo = (d: number | null | undefined) => (d === null || d === undefined ? "—" : d < 0 ? "Vencido" : d === 0 ? "Vence hoy" : `Quedan ${d} día${d === 1 ? "" : "s"} hábil${d === 1 ? "" : "es"}`);

/** Calculo en vivo (misma regla que reception_calc.compute): IVA acreditable y gasto aplicable. */
export function calcLive(action: string, cond: IvaCondition, iva: number, total: number, acceptedTotal: number, acceptedTax: number, credit: number | null) {
  if (action === "rechazada") return { credit: null as number | null, applicable: null as number | null };
  const baseIva = action === "parcial" ? Math.min(acceptedTax, iva) : iva;
  const baseTotal = action === "parcial" ? acceptedTotal : total;
  let c: number | null;
  if (cond === "credito") c = baseIva;
  else if (cond === "credito_parcial") c = credit ?? 0;
  else if (cond === "bienes_capital") c = credit ?? baseIva;
  else if (cond === "no_credito") c = 0;
  else c = null;
  return { credit: c, applicable: c === null ? null : baseTotal - c };
}
