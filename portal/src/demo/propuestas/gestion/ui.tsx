/* Piezas propias del equipo GESTION (no son de _comun): dialogo, region de tabla, subnavegacion de Detras de
   escena, iconos y formatos de fecha del portal. Todo con prefijo ges-. */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { fecha, hora } from "./comun";

export function useMedia(q: string): boolean {
  const [ok, setOk] = useState(() => typeof matchMedia === "function" && matchMedia(q).matches);
  useEffect(() => {
    const m = matchMedia(q);
    const on = () => setOk(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [q]);
  return ok;
}
export const calma = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/* "13 oct, 10:42" como el portal (Orders.tsx -> when) */
export const cuando = (s: string | null | undefined) => (s ? `${fecha(s)}, ${hora(s)}` : "—");
/* "hoy 20:40" / "mañana 08:10" / "15 oct 08:10" */
export function relativo(s: string) {
  const d = new Date(s);
  const hoy = new Date();
  const dia = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const dif = Math.round((dia(d) - dia(hoy)) / 86_400_000);
  const pre = dif === 0 ? "hoy" : dif === 1 ? "mañana" : dif === -1 ? "ayer" : fecha(d);
  return `${pre} ${hora(d)}`;
}
export const isoDia = (s: string) => {
  const d = new Date(s);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

// ---------------------------------------------------------------- iconos (trazo 2 px, los de la tienda)
export const IC = {
  check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  print: "M7 9V3h10v6M7 17H4v-7h16v7h-3M7 14h10v7H7z",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  wa: "M3 21l1.6-4.7A8.5 8.5 0 1 1 7.7 19.4L3 21z",
  mail: "M3 6h18v12H3zM3 7l9 6 9-6",
  up: "M12 19V5M5 12l7-7 7 7",
  down: "M12 5v14M5 12l7 7 7-7",
  alert: "M12 3l9 16H3zM12 10v4M12 17h.01",
  arrow: "M5 12h14M13 6l6 6-6 6",
  doc: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
  phone: "M8 2h8v20H8zM11 18h2",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 21h4",
  chat: "M4 5h16v11H8l-4 4z",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  cam: "M3 7h4l2-2h6l2 2h4v12H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  box: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10",
  store: "M4 9l1.5-5h13L20 9M4 9h16v2a3 3 0 0 1-5.3 1.9A3 3 0 0 1 12 14a3 3 0 0 1-2.7-1.1A3 3 0 0 1 4 11zM5 13.5V20h14v-6.5",
};
export const Ico = ({ d, size = 16 }: { d: string; size?: number }) => (
  <svg className="ges-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={d} /></svg>
);

// ---------------------------------------------------------------- region de tabla (desliza dentro, nunca la pagina)
export const Region = ({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) => (
  <div className={`ges-region ${className}`} role="region" tabIndex={0} aria-label={label}>{children}</div>
);

// ---------------------------------------------------------------- subnavegacion de Detras de escena
export function SubnavDE({ actual }: { actual: "pedidos" | "inventario" | "mi-tienda" | "indice" }) {
  const items: [typeof actual, string, string][] = [
    ["pedidos", "Pedidos", "/detras-de-escena/pedidos"],
    ["inventario", "Inventario web", "/detras-de-escena/inventario"],
    ["mi-tienda", "Mi Tienda", "/detras-de-escena/mi-tienda"],
  ];
  return (
    <nav className="ges-subnav" aria-label="Pantallas de Detrás de escena">
      <Link to="/detras-de-escena" className="ges-subnav__a" aria-current={actual === "indice" ? "page" : undefined}>Detrás de escena</Link>
      <span aria-hidden="true" className="ges-subnav__sep">/</span>
      {items.map(([k, t, to]) => <Link key={k} to={to} className="ges-subnav__a" aria-current={actual === k ? "page" : undefined}>{t}</Link>)}
    </nav>
  );
}

// ---------------------------------------------------------------- dialogo propio (z 70, sin animacion)
export function Dialogo(p: { titulo: ReactNode; onCerrar: () => void; children: ReactNode; pie?: ReactNode; ancho?: "normal" | "ancho"; descripcion?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const tid = useId();
  const cerrar = useRef(p.onCerrar);
  cerrar.current = p.onCerrar;
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    (el?.querySelector<HTMLElement>("[data-autofocus]") || el?.querySelector<HTMLElement>(".ges-dlg__t"))?.focus({ preventScroll: true });
    const foco = () => Array.from(el?.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [])
      .filter((x) => x.getClientRects().length > 0);
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector(".demo-notice")) return; // el aviso de demostracion (z 120) manda
      if (e.key === "Escape") { e.preventDefault(); cerrar.current(); return; }
      if (e.key !== "Tab" || !el) return;
      const f = foco();
      if (!f.length) return;
      const i = f.indexOf(document.activeElement as HTMLElement);
      if (e.shiftKey && (i <= 0)) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); if (prev && document.contains(prev)) prev.focus({ preventScroll: true }); };
  }, []);
  return (
    <div className="ges-dlg" onMouseDown={(e) => { if (e.target === e.currentTarget) p.onCerrar(); }}>
      <div ref={ref} className="ges-dlg__card" data-ancho={p.ancho || "normal"} role="dialog" aria-modal="true" aria-labelledby={tid}>
        <div className="ges-dlg__head">
          <h2 id={tid} className="ges-dlg__t" tabIndex={-1}>{p.titulo}</h2>
          <button type="button" className="ges-dlg__x" onClick={p.onCerrar} aria-label="Cerrar"><Ico d={IC.x} size={18} /></button>
        </div>
        <div className="ges-dlg__body">{p.children}</div>
        {p.pie && <div className="ges-dlg__foot">{p.pie}</div>}
      </div>
    </div>
  );
}

/* Insignia mono de dato: EJEMPLO / DATO DE CRIMSON / TU DATO / EDITADO */
export const Dato = ({ tipo = "ejemplo" }: { tipo?: "ejemplo" | "crimson" }) => (
  <span className="ges-dato" data-tipo={tipo}>{tipo === "ejemplo" ? "Datos de ejemplo" : "Dato de Crimson"}</span>
);
