import { useEffect, type ReactNode } from "react";
import { STATUS } from "../lib/api";

export const Icon = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {d.split("|").map((p, i) => <path key={i} d={p} />)}
  </svg>
);

export const I = {
  home: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  quote: "M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z|M14 3v6h6|M9 13h6M9 17h6",
  invoice: "M5 3h14v18l-3-2-3 2-3-2-3 2z|M9 8h6M9 12h6",
  pay: "M3 7h18v10H3z|M3 11h18|M7 15h3",
  customers: "M9 8a3.2 3.2 0 1 0 0 .1|M3 19c0-3 2.7-5 6-5s6 2 6 5|M17 9a2.5 2.5 0 1 0 0 .1|M17 14c2.5 0 4.5 1.6 4.5 4",
  products: "M12 3l9 5-9 5-9-5z|M3 13l9 5 9-5|M3 17l9 5 9-5",
  inventory: "M4 7l8-4 8 4v10l-8 4-8-4z|M4 7l8 4 8-4|M12 11v10",
  store: "M3 9l2-5h14l2 5|M3 9h18v11H3z|M9 20v-6h6v6",
  reports: "M4 20h16|M7 16v-5|M12 16V8|M17 16v-3",
  accounting: "M4 4h16v16H4z|M8 9h8M8 13h8M8 17h4",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z|M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z|M21 21l-4.3-4.3",
  plus: "M12 5v14|M5 12h14",
  refresh: "M21 12a9 9 0 1 1-2.6-6.4|M21 3v6h-6",
  x: "M18 6L6 18|M6 6l12 12",
  check: "M5 12l5 5 9-10",
  link: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1|M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  whatsapp: "M20 4a10 10 0 0 1-14 14L3 21l3-3A10 10 0 0 1 20 4z|M9 9c0 4 3 6 6 6",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10z|M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M21 13A8 8 0 0 1 11 3a8 8 0 1 0 10 10z",
  arrow: "M5 12h14|M13 6l6 6-6 6",
  menu: "M4 7h16M4 12h16M4 17h16",
  logout: "M10 17l5-5-5-5|M15 12H3|M21 3v18",
  copy: "M9 9h11v11H9z|M5 15V4h11",
  trend: "M3 17l6-6 4 4 8-8|M15 7h6v6",
  bell: "M6 8a6 6 0 0 1 12 0v5l2 3H4l2-3z|M10 20a2 2 0 0 0 4 0",
  box: "M3 7l9-4 9 4v10l-9 4-9-4z",
  wallet: "M3 7h16a2 2 0 0 1 2 2v10H3z|M3 7V5a2 2 0 0 1 2-2h11v4|M16 13h5v4h-5a2 2 0 0 1 0-4z",
  ticket: "M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4z|M14 6v12",
  bank: "M3 10l9-6 9 6|M5 10v8M9.5 10v8M14.5 10v8M19 10v8|M3 20h18",
  team: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z|M2 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5|M16.5 11a2.5 2.5 0 1 0 0-5|M18 14.5c2.3.4 4 2.3 4 5",
  upload: "M12 16V4|M7 9l5-5 5 5|M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  ruler: "M3 15l12-12 6 6-12 12z|M7 11l2 2M10 8l2 2M13 5l2 2",
  shield: "M12 3l8 3v6c0 4.4-3.2 8.1-8 9-4.8-.9-8-4.6-8-9V6z|M9 12l2 2 4-4",
};

export function Badge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "muted" as const };
  return <span className={`badge badge--${s.tone}`}>{s.label}</span>;
}

export function Card({ title, extra, children, flush, className = "" }: { title?: ReactNode; extra?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <section className={`card ${flush ? "card--flush" : ""} ${className}`}>
      {title !== undefined && (
        <header className="card__head">
          <h3 className="h3">{title}</h3>
          {extra}
        </header>
      )}
      <div className="card__body">{children}</div>
    </section>
  );
}

export function Empty({ title = "Nada que mostrar… ¡por ahora!", hint, action }: { title?: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <span className="meta">Sin resultados</span>
      <div className="h3">{title}</div>
      {hint && <p className="muted">{hint}</p>}
      {action}
    </div>
  );
}

export function Modal({ title, onClose, children, foot, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { width: "min(820px,100%)" } : undefined} role="dialog" aria-modal="true">
        <header className="modal__head">
          <h3 className="h2">{title}</h3>
          <button className="x" onClick={onClose} aria-label="Cerrar"><Icon d={I.x} /></button>
        </header>
        <div className="modal__body">{children}</div>
        {foot && <footer className="modal__foot">{foot}</footer>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>{label}</label>
      {children}
      {hint && <small>{hint}</small>}
    </div>
  );
}

export function Spark({ values, color = "#ff5a6e" }: { values: number[]; color?: string }) {
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${40 - (v / max) * 36}`).join(" ");
  return (
    <svg className="kpi__spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      <polygon points={`0,40 ${pts} 100,40`} fill={color} opacity=".12" />
    </svg>
  );
}
