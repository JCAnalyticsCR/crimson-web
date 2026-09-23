import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { I, Icon } from "../ui/components";
import { useSession } from "./session";
import GlobalSearch from "./GlobalSearch";
import { ROLES, roleMeta } from "./roles";
import RoleWelcome from "./RoleWelcome";
import UserMenu from "./UserMenu";

/* Cada entrada declara que permiso necesita ("modulo.accion", varias con |). El menu se arma con los permisos del rol. */
type NavItem = { to: string; label: string; icon: string; end?: boolean; need?: string };
export const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Operación", items: [
    { to: "/", label: "Inicio", icon: I.home, end: true, need: "dashboard.ver" },
    { to: "/cotizaciones", label: "Cotizaciones", icon: I.quote, need: "sales.ver" },
    { to: "/facturas", label: "Facturas", icon: I.invoice, need: "sales.ver" },
    { to: "/pos", label: "Punto de venta", icon: I.wallet, need: "sales.crear" },
    { to: "/recurrencias", label: "Recurrencias", icon: I.refresh, need: "sales.recurrencias" },
    { to: "/pagos", label: "Pagos", icon: I.pay, need: "payments.ver" },
    { to: "/ordenes", label: "Órdenes", icon: I.box, need: "sales.ver" },
    { to: "/eventos", label: "Eventos", icon: I.ticket, need: "events.ver" },
  ]},
  { group: "Campo", items: [
    { to: "/oportunidades", label: "Oportunidades", icon: I.trend, need: "crm_pipeline.ver" },
    { to: "/levantamientos", label: "Levantamientos", icon: I.quote, need: "field.ver" },
    { to: "/ordenes-trabajo", label: "Órdenes de trabajo", icon: I.check, need: "field.ver" },
    { to: "/proyectos", label: "Proyectos", icon: I.box, need: "projects.ver" },
    { to: "/activos", label: "Activos del cliente", icon: I.inventory, need: "assets.ver" },
    { to: "/compras", label: "Solicitudes de compra", icon: I.upload, need: "purchases.ver" },
  ]},
  { group: "Catálogo", items: [
    { to: "/clientes", label: "Clientes", icon: I.customers, need: "crm.ver" },
    { to: "/productos", label: "Productos & Servicios", icon: I.products, need: "catalog.ver" },
    { to: "/cupones", label: "Cupones", icon: I.wallet, need: "catalog.editar" },
    { to: "/inventario", label: "Inventarios", icon: I.inventory, need: "inventory.ver" },
    { to: "/mi-tienda", label: "Mi Tienda", icon: I.store, need: "settings.configurar" },
  ]},
  { group: "Control", items: [
    { to: "/reportes", label: "Reportes", icon: I.reports, need: "reports.ver" },
    { to: "/contabilidad", label: "Contabilidad", icon: I.accounting, need: "accounting.ver" },
    { to: "/recepcion", label: "Recepción XML", icon: I.bell, need: "accounting.ver" },
    { to: "/conciliacion", label: "Conciliación", icon: I.bank, need: "accounting.ver" },
    { to: "/planillas", label: "Planillas", icon: I.team, need: "payroll.ver" },
    { to: "/importar", label: "Importar datos", icon: I.upload, need: "settings.configurar" },
    { to: "/ajustes", label: "Ajustes", icon: I.settings },
  ]},
];

export default function AppShell() {
  const { me, logout, theme, toggleTheme, allows, role, viewAs, setViewAs } = useSession();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const [clock, setClock] = useState("");
  const [welcome, setWelcome] = useState(() => { try { return sessionStorage.getItem("crimson-welcome") === "1"; } catch { return false; } });

  useEffect(() => {
    const t = () => setClock(new Date().toLocaleTimeString("es-CR", { hour12: false, timeZone: "America/Costa_Rica" }));
    t(); const id = setInterval(t, 1000); return () => clearInterval(id);
  }, []);
  useEffect(() => { setOpen(false); }, [loc.pathname]);

  const initials = (me?.user.full_name || "?").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();
  const rm = roleMeta(role);
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((it) => allows(it.need)) })).filter((g) => g.items.length);
  const closeWelcome = () => { setWelcome(false); try { sessionStorage.removeItem("crimson-welcome"); } catch { /* privado */ } };

  return (
    <div className="shell">
      <aside className={`nav ${open ? "is-open" : ""}`}>
        <div className="nav__brand">
          <img src="/logo.png" alt="Crimson Consulting" />
        </div>
        <button className="nav__tenant" onClick={() => nav("/ajustes?tab=cuenta")} title="Mi cuenta">
          <div style={{ minWidth: 0 }}><b>{me?.tenant.name}</b><span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><i style={{ width: 7, height: 7, borderRadius: 99, background: rm.tone, display: "inline-block" }} />{rm.label}{viewAs ? " · vista previa" : ""}</span></div>
          <Icon d={I.arrow} size={14} />
        </button>
        {groups.map((g) => (
          <div className="nav__group" key={g.group}>
            <div className="nav__label">{g.group}</div>
            {g.items.map((it) => (
              <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => `nav__link ${isActive ? "is-active" : ""}`}>
                <Icon d={it.icon} /><span>{it.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
        <div className="nav__foot">
          <span className="nav__rec"><i className="rec-dot" />EN VIVO · {clock}</span>
          <div className="nav__user">
            <span className="avatar" style={{ boxShadow: `0 0 0 2px ${rm.tone}` }}>{initials}</span>
            <div style={{ minWidth: 0 }}><b>{me?.user.full_name}</b><span>{me?.user.email}</span></div>
            <button className="x" onClick={async () => { await logout(); nav("/login", { replace: true }); }} title="Cerrar sesión" aria-label="Cerrar sesión" style={{ marginLeft: "auto", color: "var(--nav-muted)" }}><Icon d={I.logout} /></button>
          </div>
        </div>
      </aside>
      {open && <div className="nav-scrim" onClick={() => setOpen(false)} />}

      <div className="main">
        {viewAs && (
          <div className="preview-bar">
            <span><b>Vista previa como {roleMeta(viewAs).label}.</b> Así ve el panel esa persona; tus permisos reales no cambian.</span>
            <button className="btn btn--sm" onClick={() => setViewAs(null)}>Salir de la vista previa</button>
          </div>
        )}
        <header className="topbar">
          <button className="btn btn--ghost btn--icon" onClick={() => setOpen((o) => !o)} aria-label="Menú" style={{ display: "none" }} id="burger"><Icon d={I.menu} /></button>
          <GlobalSearch />
          <div className="topbar__right">
            {me?.role === "admin" && (
              <label className="viewas" title="Previsualizar el panel con otro rol">
                <span className="meta">Ver como</span>
                <select value={viewAs || "admin"} onChange={(e) => setViewAs(e.target.value === "admin" ? null : e.target.value)}>
                  {Object.entries(ROLES).filter(([k]) => k !== "soporte").map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
                </select>
              </label>
            )}
            <button className="btn btn--ghost btn--icon" onClick={toggleTheme} title="Modo claro/oscuro"><Icon d={theme === "dark" ? I.sun : I.moon} /></button>
            {allows("sales.crear") && <button className="btn btn--crimson" onClick={() => nav("/cotizaciones/nueva")}><Icon d={I.plus} /><span className="hide-sm">Nueva cotización</span></button>}
            <UserMenu />
          </div>
        </header>
        <main className="content">
          <Suspense fallback={<div style={{ minHeight: "50vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      {welcome && me && <RoleWelcome onClose={closeWelcome} />}
      <style>{`@media (max-width:900px){#burger{display:inline-flex!important}}`}</style>
    </div>
  );
}
