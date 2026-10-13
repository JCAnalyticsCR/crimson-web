/* Piezas compartidas del checkout y de "Mi pedido" (vitrina publica). Mismo lenguaje visual que PublicStore. */
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

export const money = (v: string | number, cur = "CRC") =>
  new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, currencyDisplay: "narrowSymbol", minimumFractionDigits: cur === "CRC" ? 0 : 2, maximumFractionDigits: cur === "CRC" ? 0 : 2 }).format(Number(v) || 0);
export const wa = (phone: string, text: string) => `https://wa.me/${phone.replace(/\D/g, "")}?text=${encodeURIComponent(text)}`;
export const local = {
  get(k: string) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { localStorage.setItem(k, v); } catch { /* modo privado */ } },
  del(k: string) { try { localStorage.removeItem(k); } catch { /* modo privado */ } },
};
export const session = {
  get(k: string) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { sessionStorage.setItem(k, v); } catch { /* modo privado */ } },
};
export const fmtWhen = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

export const Ico = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg className="sh-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
export const IC = {
  check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18",
  back: "M15 18l-6-6 6-6",
  next: "M9 6l6 6-6 6",
  wa: "M3 21l1.6-4.7A8.5 8.5 0 1 1 7.7 19.4L3 21zM9 8.5c0 3.5 3 6.5 6.5 6.5l1-1.6-2-1-1 1c-1.2-.5-2.4-1.7-2.9-2.9l1-1-1-2L9 8.5z",
  copy: "M9 9h11v11H9zM5 15V4h11",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  card: "M3 6h18v12H3zM3 10h18M7 15h3",
  truck: "M3 6h11v10H3zM14 9h4l3 3v4h-7M7 19a2 2 0 1 0 0-.1M17 19a2 2 0 1 0 0-.1",
  store: "M3 9l2-5h14l2 5M3 9h18v11H3zM9 20v-6h6v6",
  lock: "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3",
};

export type ApiFn = <T>(path: string, init?: RequestInit) => Promise<{ data: T; total: number | null }>;
export type StoreInfo = { name: string; whatsapp: string; currency: string; legal: { privacy: string; terms: string } };

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  // etiqueta asociada por id (lectores de pantalla y autocompletar del navegador)
  const id = useId();
  const el = isValidElement(children) ? cloneElement(children as ReactElement<{ id?: string; "aria-describedby"?: string }>, { id, ...(hint ? { "aria-describedby": `${id}-h` } : {}) }) : children;
  return <div className="sh-field"><label htmlFor={id}>{label}</label>{el}{hint && <small id={`${id}-h`} className="ck-hint">{hint}</small>}</div>;
}

/** Clave de idempotencia: la misma mientras no cambie lo que se manda (doble clic = mismo pedido). */
export function newKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return `ck-${c.randomUUID()}`;
  return `ck-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
