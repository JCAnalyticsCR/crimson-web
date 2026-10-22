/* Contabilidad en tres partes (R9): Facturas (los papeles: lo que llega y lo que respondemos), Contable (el dinero:
   gastos, recurrentes, categorias, proveedores, bancos) y Tributario (lo que se declara). Orden = el camino de un
   comprobante. Cada parte tiene sus sub-pestanas y el contenido va en <Outlet />. */
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../../lib/api";
import { MODE_BAND, type HaciendaState, type Mode } from "./hacienda";
import "./accounting.css";

const PARTS = [
  { key: "facturas", to: "/contabilidad/facturas", label: "Facturas", hint: "Lo que llega de los proveedores y lo que respondemos a Hacienda" },
  { key: "contable", to: "/contabilidad/contable", label: "Contable", hint: "El dinero: gastos, recurrentes, categorías, proveedores y bancos" },
  { key: "tributario", to: "/contabilidad/tributario", label: "Tributario", hint: "Lo que se declara: IVA del mes, plazos, rechazos y reportes" },
  { key: "quickbooks", to: "/contabilidad/quickbooks", label: "QuickBooks", hint: "Asientos de diario listos para importar en QuickBooks Online" },
] as const;

const SUBS: Record<string, { to: string; label: string; end?: boolean }[]> = {
  facturas: [
    { to: "/contabilidad/facturas", label: "Por revisar", end: true },
    { to: "/contabilidad/facturas/recibidas", label: "Recibidas" },
    { to: "/contabilidad/facturas/correos", label: "Correos" },
  ],
  contable: [
    { to: "/contabilidad/contable", label: "Gastos", end: true },
    { to: "/contabilidad/contable/recurrentes", label: "Recurrentes" },
    { to: "/contabilidad/contable/categorias", label: "Categorías" },
    { to: "/contabilidad/contable/proveedores", label: "Proveedores" },
    { to: "/contabilidad/contable/bancos", label: "Bancos" },
  ],
  tributario: [{ to: "/contabilidad/tributario", label: "Panel del mes", end: true }],
  quickbooks: [{ to: "/contabilidad/quickbooks", label: "Asientos QBO", end: true }],
};

export default function AccountingHub() {
  const loc = useLocation();
  const [mode, setMode] = useState<Mode | null>(null);
  useEffect(() => { api<HaciendaState>("/settings/hacienda").then((s) => setMode(s.mode)).catch(() => setMode(null)); }, []);
  const part = loc.pathname.startsWith("/contabilidad/contable") ? "contable" : loc.pathname.startsWith("/contabilidad/tributario") ? "tributario" : loc.pathname.startsWith("/contabilidad/quickbooks") ? "quickbooks" : "facturas";
  const band = mode ? MODE_BAND[mode] : undefined;
  return (
    <>
      <div className="page-head">
        <div><div className="meta">07 · Contabilidad</div><h1 className="h1">Contabilidad</h1></div>
      </div>
      {band && <div className="acc-band" role="status">{band}</div>}
      <nav className="acc-parts" aria-label="Partes de contabilidad">
        {PARTS.map((p) => (
          <NavLink key={p.key} to={p.to} className={() => `acc-part ${part === p.key ? "is-active" : ""}`}>
            <b>{p.label}</b><span>{p.hint}</span>
          </NavLink>
        ))}
      </nav>
      <nav className="acc-sub" aria-label="Secciones">
        {SUBS[part].map((s) => <NavLink key={s.to} to={s.to} end={s.end} className={({ isActive }) => (isActive ? "is-active" : "")}>{s.label}</NavLink>)}
      </nav>
      <Outlet />
    </>
  );
}
