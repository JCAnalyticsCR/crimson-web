import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { I, Icon } from "../ui/components";
import { parseTs } from "../lib/api";
import { useSession } from "./session";
import GlobalSearch from "./GlobalSearch";
import { ROLES, customRoleCodes, isClientRole, roleMeta } from "./roles";
import RoleWelcome from "./RoleWelcome";
import UserMenu from "./UserMenu";
import { NuevaVersion } from "./NuevaVersion";
import { NAV } from "./nav";
import PreviewBand from "./PreviewBand";
import AllyPreviewPicker from "./AllyPreviewPicker";
import { useApprovalCount } from "../modules/approvals/useApprovalCount";
import { useReceptionCount } from "../modules/accounting/useReceptionCount";
import { useCompletarCount } from "../modules/inventory/useCompletarCount";

/* El menu vive en nav.ts (lo extienden otros frentes sin tocar este archivo). Se reexporta por compatibilidad. */
export { NAV } from "./nav";


export default function AppShell() {
  const { me, logout, theme, toggleTheme, allows, inMenu, role, viewAs, setViewAs } = useSession();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const aprob = useApprovalCount(); // numero junto a "Aprobaciones"
  const recep = useReceptionCount(); // comprobantes por revisar junto a "Contabilidad"
  const completar = useCompletarCount(); // articulos por completar junto a "Inventarios"
  const [clock, setClock] = useState("");
  const [welcome, setWelcome] = useState(() => { try { return sessionStorage.getItem("crimson-welcome") === "1"; } catch { return false; } });
  // "Ver como" un rol aliado: se elige la empresa (y la persona) y se abre su portal con datos reales, solo lectura
  const [pickAlly, setPickAlly] = useState<"aliado" | "tecnico_aliado" | null>(null);

  useEffect(() => {
    const t = () => setClock(new Date().toLocaleTimeString("es-CR", { hour12: false, timeZone: "America/Costa_Rica" }));
    t(); const id = setInterval(t, 1000); return () => clearInterval(id);
  }, []);
  useEffect(() => { setOpen(false); }, [loc.pathname]);
  // la opcion activa siempre a la vista dentro de la lista desplazable
  useEffect(() => { document.querySelector(".nav__link.is-active")?.scrollIntoView({ block: "nearest" }); }, [loc.pathname]);

  const initials = (me?.user.full_name || "?").split(" ").map((s) => s[0]).slice(0, 2).join("").toUpperCase();
  const rm = roleMeta(role);
  // permisos del rol ∩ su menu explicito (roles.ts); sin lista, solo permisos
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((it) => allows(it.need) && inMenu(it.to)) })).filter((g) => g.items.length);
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
        {/* Solo la lista se desplaza: logo, cuenta y usuario quedan fijos. En una laptop con poca altura
            (o con zoom del navegador) las ultimas opciones quedaban cortadas y no habia forma de bajar. */}
        <div className="nav__scroll">
        {groups.map((g) => (
          <div className="nav__group" key={g.group}>
            <div className="nav__label">{g.group}</div>
            {g.items.map((it) => (
              <NavLink key={it.to} to={it.to} end={it.end} className={({ isActive }) => `nav__link ${isActive ? "is-active" : ""}`}>
                <Icon d={it.icon} /><span>{it.label}</span>
                {it.badge === "approvals" && aprob ? <span className="nav__badge" aria-label={`${aprob} pendientes`}>{aprob > 99 ? "99+" : aprob}</span> : null}
                {it.badge === "completar" && completar ? <span className="nav__badge" style={{ background: "var(--warn)" }} aria-label={`${completar} artículos por completar`} title={`${completar} artículos por completar`}>{completar > 99 ? "99+" : completar}</span> : null}
                {it.badge === "recepcion" && recep ? <span className="nav__badge" style={recep.urgent ? undefined : { background: "var(--warn)" }} aria-label={`${recep.count} comprobantes por revisar`}>{recep.count > 99 ? "99+" : recep.count}</span> : null}
              </NavLink>
            ))}
          </div>
        ))}
        </div>
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
      <NuevaVersion />
      {/* vista previa por rol: banda fija sobre todo (ventanas incluidas), fuera de .main */}
      <PreviewBand />

      <div className="main">
        <header className="topbar">
          <button className="btn btn--ghost btn--icon" onClick={() => setOpen((o) => !o)} aria-label="Menú" style={{ display: "none" }} id="burger"><Icon d={I.menu} /></button>
          <GlobalSearch />
          <div className="topbar__right">
            {me?.role === "admin" && (
              <label className="viewas" title="Previsualizar el panel con otro rol">
                <span className="meta">{viewAs ? "Viendo como" : "Ver como"}</span>
                <select value={viewAs || "admin"} onChange={(e) => { const v = e.target.value; if (v === "aliado" || v === "tecnico_aliado") setPickAlly(v); else setViewAs(v === "admin" ? null : v); }}>
                  {Object.entries(ROLES).filter(([k]) => k !== "soporte" && !isClientRole(k)).map(([k, r]) => <option key={k} value={k}>{r.label}</option>)}
                  {customRoleCodes().map((k) => <option key={k} value={k}>{roleMeta(k).label}</option>)}
                </select>
              </label>
            )}
            <button className="btn btn--ghost btn--icon" onClick={toggleTheme} title="Modo claro/oscuro"><Icon d={theme === "dark" ? I.sun : I.moon} /></button>
            {allows("sales.crear") && <button className="btn btn--crimson" onClick={() => nav("/cotizaciones/nueva")}><Icon d={I.plus} /><span className="hide-sm">Nueva cotización</span></button>}
            <UserMenu />
          </div>
        </header>
        <main className="content">
          {/* 2FA obligatorio para su rol (plan 1.10): aviso durante la gracia; al vencer, main.tsx muestra la pantalla forzada */}
          {me?.twofa?.required && !me.user.totp_enabled && me.twofa.deadline && !viewAs && (
            <div role="status" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", padding: "10px 14px", marginBottom: 14, borderRadius: 10, background: "var(--warn-soft)", border: "1px solid color-mix(in srgb, var(--warn) 45%, transparent)", fontSize: 13.5 }}>
              <span style={{ flex: "1 1 260px" }}><b>Activá la verificación en dos pasos antes del {parseTs(me.twofa.deadline).toLocaleDateString("es-CR", { day: "numeric", month: "long" })}.</b> Tu rol la exige; después de esa fecha el panel solo te deja configurarla.</span>
              <button className="btn btn--crimson btn--sm" onClick={() => nav("/ajustes?tab=cuenta")}>Activar ahora</button>
            </div>
          )}
          <Suspense fallback={<div style={{ minHeight: "50vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      {welcome && me && <RoleWelcome onClose={closeWelcome} />}
      {pickAlly && <AllyPreviewPicker role={pickAlly} onClose={() => setPickAlly(null)} />}
      <style>{`@media (max-width:900px){#burger{display:inline-flex!important}}`}</style>
    </div>
  );
}
