/* Layout del portal del cliente: su propio menu (nunca el del panel interno). En el telefono las secciones
   van en una barra fija abajo, al alcance del pulgar; en escritorio, arriba. */
import { Suspense } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";
import "./cliente.css";

type Item = { to: string; label: string; short: string; icon: string; need?: string; end?: boolean };
const ITEMS: Item[] = [
  { to: "/", label: "Inicio", short: "Inicio", icon: I.home, end: true },
  { to: "/mis-tickets", label: "Mis tickets", short: "Tickets", icon: I.life },
  { to: "/mis-equipos", label: "Mis equipos", short: "Equipos", icon: I.shield },
  { to: "/documentos", label: "Documentos", short: "Documentos", icon: I.invoice, need: "portal.documentos" },
  { to: "/mi-empresa", label: "Usuarios de mi empresa", short: "Usuarios", icon: I.team, need: "portal.usuarios" },
];

export default function ClientShell() {
  const { me, allows, logout, theme, toggleTheme } = useSession();
  const nav = useNavigate();
  const items = ITEMS.filter((it) => allows(it.need));
  const salir = async () => { try { await logout(); } finally { nav("/login", { replace: true }); } };

  return (
    <div className="cp">
      <header className="cp-top">
        <img src="/logo.png" alt="Crimson Consulting" />
        <div className="cp-top__who">
          <b>{me?.user.full_name}</b>
          <span>Portal de clientes · {me?.tenant.name}</span>
        </div>
        <nav className="cp-nav" aria-label="Secciones">
          {items.map((it) => (
            <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => (isActive ? "is-active" : "")}><Icon d={it.icon} size={16} />{it.label}</NavLink>
          ))}
        </nav>
        <button className="btn btn--ghost btn--icon" onClick={toggleTheme} title="Modo claro u oscuro" aria-label="Cambiar modo claro u oscuro"><Icon d={theme === "dark" ? I.sun : I.moon} /></button>
        <button className="btn btn--ghost btn--icon" onClick={() => nav("/mi-cuenta")} title="Mi cuenta" aria-label="Mi cuenta"><Icon d={I.settings} /></button>
        <button className="btn btn--ghost btn--icon" onClick={salir} title="Cerrar sesión" aria-label="Cerrar sesión"><Icon d={I.logout} /></button>
      </header>
      <main className="cp-main">
        <Suspense fallback={<div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>}>
          <Outlet />
        </Suspense>
      </main>
      <nav className="cp-tabs" aria-label="Secciones">
        {items.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => (isActive ? "is-active" : "")}><Icon d={it.icon} size={20} /><span>{it.short}</span></NavLink>
        ))}
      </nav>
    </div>
  );
}
