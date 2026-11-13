/* Tipos de /bank-books (saldos, libro, estados de cuenta y conciliacion). Montos como texto: Decimal del API. */

export type BookAccount = {
  id: number; name: string; bank: string | null; currency: string; number: string | null; kind: "banco" | "caja"; active: boolean;
  opening_balance: string; opening_date: string | null;
  book_balance: string; bank_balance: string | null; bank_reported_balance: string | null; last_statement_date: string | null;
  book_balance_at_statement: string | null; difference: string | null;
  pending_lines: number; unmatched_entries: number; cash_default: boolean;
};

export type EntryType = "payment" | "expense" | "supplier_payment" | "movement";

export type BookEntry = {
  type: EntryType; id: number; date: string; amount: string; label: string; document: string | null; counterparty: string | null;
  reference: string | null; to: string | null; bank_account_id: number | null; currency: string;
  balance?: string; line_id?: number | null;
};

export type Ledger = {
  account: BookAccount; opening_balance: string; opening_date: string | null; period_start_balance: string; closing_balance: string;
  inflows: string; outflows: string; entries: BookEntry[]; warnings: string[];
};

export type StatementLine = {
  id: number; date: string; description: string; reference: string | null; amount: string; balance: string | null;
  status: "pendiente" | "conciliado" | "ignorado"; matched_by: "auto" | "manual" | null; matched_type: EntryType | null; matched_id: number | null;
  match: BookEntry | null; diff: string | null; import_id: number | null;
};

export type Candidate = BookEntry & { diff: string; days: number; ref: boolean; exact: boolean; unassigned: boolean };

export type Reconciliation = {
  as_of: string; opening_balance: string; opening_date: string | null;
  bank_balance: string; bank_reported_balance: string | null; last_statement_date: string | null; book_balance: string; difference: string;
  pending_bank: { count: number; credits: string; debits: string; total: string; items: StatementLine[] };
  pending_book: { count: number; inflows: string; outflows: string; total: string; items: BookEntry[] };
  matched_count: number; ignored_count: number; unexplained: string; reconciled: boolean; warnings: string[];
};

export type StatementImport = { id: number; filename: string; date_from: string | null; date_to: string | null; rows: number; added: number; duplicates: number; closing_balance: string | null; created_at: string };

export type Mapping = {
  header_row: number; date: number | null; description: number | null; reference: number | null; amount: number | null;
  credit: number | null; debit: number | null; balance: number | null; type: number | null; date_order: "dmy" | "mdy" | "ymd";
};

export type Preview = {
  mapping: Mapping; columns: { index: number; header: string; samples: (string | null)[] }[]; header_rows: (string | null)[][];
  rows: { row: number; date: string; amount: string; description: string; reference: string | null; balance: string | null; duplicate: boolean; before_cutoff: boolean }[];
  rows_truncated: boolean; errors: { row: number; error: string }[]; skipped: number; total: number; new: number; duplicates: number; before_cutoff: number;
  credits: string; debits: string; date_from: string | null; date_to: string | null; closing_balance: string | null; statement_opening: string | null;
  continuity: { date: string; file: string; crimson: string; ok: boolean } | null;
};

export const TYPE_LABEL: Record<EntryType, string> = { payment: "Cobro", expense: "Gasto", supplier_payment: "Pago a proveedor", movement: "Movimiento" };
export const MOVEMENT_KINDS: [string, string][] = [["deposito", "Depósito"], ["interes", "Intereses"], ["cargo", "Cargo bancario"], ["ajuste", "Ajuste"], ["otro", "Otro"]];
export const DEFAULT_CUTOFF = "2026-11-01"; // la empresa arranco en Crimson el 1 de noviembre de 2026

export const num = (s: string | null | undefined) => Number(s ?? 0);
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Error");
