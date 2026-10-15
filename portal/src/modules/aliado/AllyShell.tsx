/* Layout del portal de aliados (patrón del portal del cliente): su propio menú, nunca el panel interno. En el
   teléfono, barra fija abajo con lo que más se usa (Inicio · Proyectos · Campo · Soporte · Más); en escritorio,
   arriba. El técnico del aliado solo tiene "Hoy" y "Mi cuenta". "Ver como este aliado" (admin) dibuja este mismo
   portal con los datos reales de la empresa, en solo lectura, con una franja que lo dice. */
import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useSession } from "../../app/session";
import { setSignPath } from "../../lib/media";
import { I, Icon } from "../../ui/components";
import { useAlly } from "./shared";
import "../cliente/cliente.css";
import "./aliado.css";

type Item = { to: string; label: string; short: string; icon: string; need?: string; end?: boolean; tab?: boolean; feature?: "ganancias" };
const ITEMS: Item[] = [
  { to: "/", label: "Inicio", short: "Inicio", icon: I.home, end: true, tab: true },
  { to: "/oportunidades", label: "Oportunidades", short: "Oport.", icon: I.trend, need: "oportunidades" },
  { to: "/proyectos", label: "Proyectos", short: "Proyectos", icon: I.box, need: "proyectos", tab: true },
  { to: "/levantamientos", label: "Levantamientos", short: "Campo", icon: I.ruler, need: "levantamientos", tab: true },
  { to: "/ordenes", label: "Órdenes", short: "Órdenes", icon: I.check, need: "ordenes" },
  { to: "/soporte", label: "Soporte", short: "Soporte", icon: I.life, need: "tickets", tab: true },
  { to: "/mantenimientos", label: "Mantenimientos", short: "Mant.", icon: I.refresh, need: "mantenimientos" },
  { to: "/activos", label: "Activos", short: "Activos", icon: I.shield, need: "activos" },
  { to: "/catalogo", label: "Catálogo", short: "Catálogo", icon: I.products, need: "catalogo" },
  { to: "/inventario", label: "Inventario", short: "Inventario", icon: I.inventory, need: "inventario" },
  { to: "/ganancias", label: "Mis ganancias", short: "Ganancias", icon: I.wallet, need: "ganancias", feature: "ganancias" },
  { to: "/suscripcion", label: "Mi suscripción", short: "Suscripción", icon: I.invoice, need: "suscripcion" },
  { to: "/equipo", label: "Mi equipo", short: "Equipo", icon: I.team, need: "usuarios" },
];

export default function AllyShell() {
  const { me, logout, theme, toggleTheme, setViewAs } = useSession();
  const { company, preview, isTech, can, personName } = useAlly();
  const nav = useNavigate();
  const [mas, setMas] = useState(false);
  useEffect(() => { setSignPath("/aliado/media/firmar"); return () => setSignPath(null); }, []);
  const items = isTech
    ? [{ to: "/", label: "Hoy", short: "Hoy", icon: I.clock, end: true, tab: true } as Item, { to: "/mi-cuenta", label: "Mi cuenta", short: "Cuenta", icon: I.settings, tab: true } as Item]
    : ITEMS.filter((it) => (!it.need || can(it.need)) && (!it.feature || company?.features?.[it.feature] !== false));
  const tabs = isTech ? items : items.filter((it) => it.tab).slice(0, 4);
  const salir = async () => { try { await logout(); } finally { nav("/login", { replace: true }); } };
  const salirVista = () => { setViewAs(null); nav("/aliados", { replace: true }); };

  return (
    <div className="cp al">
      {preview && (
        <div className="al-preview" role="status">
          <Icon d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z|M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" size={16} />
          <span>Vista previa del aliado <b>{company?.name}</b>{personName ? ` · ${personName}` : ""}: ves exactamente sus datos · solo lectura</span>
          <button type="button" className="btn btn--sm" onClick={salirVista}>Salir</button>
        </div>
      )}
      <header className="cp-top">
        <img src="/logo.png" alt="Crimson Consulting" />
        <div className="cp-top__who">
          <b>{preview ? personName || company?.name : me?.user.full_name}</b>
          <span>Portal de aliados · {company?.name || me?.tenant.name}</span>
        </div>
        <nav className="cp-nav" aria-label="Secciones">
          {items.map((it) => (
            <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => (isActive ? "is-active" : "")}><Icon d={it.icon} size={16} />{it.label}</NavLink>
          ))}
        </nav>
        <button className="btn btn--ghost btn--icon" onClick={toggleTheme} title="Modo claro u oscuro" aria-label="Cambiar modo claro u oscuro"><Icon d={theme === "dark" ? I.sun : I.moon} /></button>
        {!preview && <button className="btn btn--ghost btn--icon" onClick={() => nav("/mi-cuenta")} title="Mi cuenta" aria-label="Mi cuenta"><Icon d={I.settings} /></button>}
        {!preview && <button className="btn btn--ghost btn--icon" onClick={salir} title="Cerrar sesión" aria-label="Cerrar sesión"><Icon d={I.logout} /></button>}
      </header>
      {company?.state === "suspendido" && (
        <div className="al-band" role="alert">
          El acceso de {company.name} está suspendido.{company.suspended_reason ? ` ${company.suspended_reason}` : ""}{" "}
          {can("suscripcion") && <NavLink to="/suscripcion">Ver mi suscripción</NavLink>}
        </div>
      )}
      <main className="cp-main">
        <Suspense fallback={<div style={{ minHeight: "40vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>}>
          <Outlet />
        </Suspense>
      </main>
      <nav className="cp-tabs" aria-label="Secciones">
        {tabs.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.end} onClick={() => setMas(false)} className={({ isActive }) => (isActive ? "is-active" : "")}><Icon d={it.icon} size={20} /><span>{it.short}</span></NavLink>
        ))}
        {!isTech && (
          <button type="button" className={mas ? "is-active" : ""} onClick={() => setMas((m) => !m)} aria-expanded={mas}><Icon d={I.menu} size={20} /><span>Más</span></button>
        )}
      </nav>
      {mas && !isTech && (
        <div className="al-more" role="menu">
          {items.filter((it) => !tabs.includes(it)).map((it) => (
            <NavLink key={it.to} to={it.to} onClick={() => setMas(false)}><Icon d={it.icon} size={16} />{it.label}</NavLink>
          ))}
          {!preview && <NavLink to="/mi-cuenta" onClick={() => setMas(false)}><Icon d={I.settings} size={16} />Mi cuenta</NavLink>}
        </div>
      )}
    </div>
  );
}
