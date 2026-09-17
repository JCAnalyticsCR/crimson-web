import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { I, Icon } from "../ui/components";
import { useSession } from "./session";

type NavItem = { to: string; label: string; icon: string; end?: boolean; soon?: boolean };
const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Operación", items: [
    { to: "/", label: "Inicio", icon: I.home, end: true },
    { to: "/cotizaciones", label: "Cotizaciones", icon: I.quote },
    { to: "/facturas", label: "Facturas", icon: I.invoice },
    { to: "/pagos", label: "Pagos", icon: I.pay },
  ]},
  { group: "Catálogo", items: [
    { to: "/clientes", label: "Clientes", icon: I.customers },
    { to: "/productos", label: "Productos & Servicios", icon: I.products },
    { to: "/inventario", label: "Inventarios", icon: I.inventory, soon: true },
    { to: "/tienda", label: "Mi Tienda", icon: I.store, soon: true },
  ]},
  { group: "Control", items: [
    { to: "/reportes", label: "Reportes", icon: I.reports, soon: true },
    { to: "/contabilidad", label: "Contabilidad", icon: I.accounting, soon: true },
    { to: "/ajustes", label: "Ajustes", icon: I.settings },
  ]},
];

export default function AppShell() {
  const { me, logout, theme, toggleTheme } = useSession();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const [clock, setClock] = useState("");

  useEffect(() => {
    const t = () => setClock(new Date().toLocaleTimeString("es-CR", { hour12: false, timeZone: "America/Costa_Rica" }));
    t(); const id = setInterval(t, 1000); return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "k") { e.preventDefault(); document.getElementById("gsearch")?.focus(); } };
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, []);

  const initials = (me?.user.full_name || "?").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="shell">
      <aside className={`nav ${open ? "is-open" : ""}`}>
        <div className="nav__brand">
          <img src="/logo.png" alt="Crimson Consulting" />
        </div>
        <button className="nav__tenant" onClick={() => nav("/ajustes")} title="Cambiar de empresa">
          <div><b>{me?.tenant.name}</b><span>{me?.role} · plan {me?.tenant.plan}</span></div>
          <Icon d={I.arrow} size={14} />
        </button>
        {NAV.map((g) => (
          <div className="nav__group" key={g.group}>
            <div className="nav__label">{g.group}</div>
            {g.items.map((it) => (
              <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => `nav__link ${isActive ? "is-active" : ""}`} onClick={() => setOpen(false)}>
                <Icon d={it.icon} /><span>{it.label}</span>{it.soon && <span className="pill" style={{ background: "rgba(255,255,255,.12)" }}>pronto</span>}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="nav__foot">
          <span className="nav__rec"><i className="rec-dot" />EN VIVO · {clock}</span>
          <div className="nav__user">
            <span className="avatar">{initials}</span>
            <div style={{ minWidth: 0 }}><b>{me?.user.full_name}</b><span>{me?.user.email}</span></div>
            <button className="x" onClick={logout} title="Cerrar sesión" style={{ marginLeft: "auto", color: "var(--nav-muted)" }}><Icon d={I.logout} /></button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="btn btn--ghost btn--icon" onClick={() => setOpen((o) => !o)} aria-label="Menú" style={{ display: "none" }} id="burger"><Icon d={I.menu} /></button>
          <form className="search" onSubmit={(e) => { e.preventDefault(); if (q.trim()) nav(`/facturas?q=${encodeURIComponent(q.trim())}`); }}>
            <Icon d={I.search} size={16} />
            <input id="gsearch" placeholder="Buscar facturas, cotizaciones, clientes…" value={q} onChange={(e) => setQ(e.target.value)} />
            <kbd>Ctrl K</kbd>
          </form>
          <div className="topbar__right">
            <button className="btn btn--ghost btn--icon" onClick={toggleTheme} title="Modo claro/oscuro"><Icon d={theme === "dark" ? I.sun : I.moon} /></button>
            <button className="btn btn--ghost btn--icon" title="Notificaciones"><Icon d={I.bell} /></button>
            <button className="btn btn--crimson" onClick={() => nav("/cotizaciones/nueva")}><Icon d={I.plus} />Nueva cotización</button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      <style>{`@media (max-width:900px){#burger{display:inline-flex!important}}`}</style>
    </div>
  );
}
