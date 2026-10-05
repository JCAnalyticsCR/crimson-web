/* Bienvenida al entrar: confirma el acceso, muestra el rol y lo que ese rol puede (y no puede) hacer,
   con atajos a lo que mas usa. Se muestra una vez por inicio de sesion. */
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { I, Icon } from "../ui/components";
import { useSession } from "./session";
import { roleMeta } from "./roles";

const SHORTCUTS: { need: string; to: string; label: string; icon: string }[] = [
  { need: "sales.crear", to: "/cotizaciones/nueva", label: "Nueva cotización", icon: I.quote },
  { need: "sales.crear", to: "/pos", label: "Punto de venta", icon: I.wallet },
  { need: "crm.ver", to: "/clientes", label: "Clientes", icon: I.customers },
  { need: "inventory.ver", to: "/inventario", label: "Inventarios", icon: I.inventory },
  { need: "catalog.editar", to: "/productos", label: "Productos", icon: I.products },
  { need: "reports.ver", to: "/reportes", label: "Reportes", icon: I.reports },
  { need: "accounting.ver", to: "/contabilidad", label: "Contabilidad", icon: I.accounting },
  { need: "settings.configurar", to: "/ajustes", label: "Ajustes", icon: I.settings },
];

export default function RoleWelcome({ onClose }: { onClose: () => void }) {
  const { me, role, allows } = useSession();
  const nav = useNavigate();
  const rm = roleMeta(role);
  const first = (me?.user.full_name || "").split(" ")[0];
  const shortcuts = SHORTCUTS.filter((s) => allows(s.need)).slice(0, 4);
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === "Escape" && onClose(); window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k); }, [onClose]);

  return (
    <div className="welcome" role="dialog" aria-modal="true" aria-label="Bienvenida" onClick={onClose}>
      <div className="welcome__card" onClick={(e) => e.stopPropagation()} style={{ ["--tone" as string]: rm.tone }}>
        <div className="welcome__scan" aria-hidden="true" />
        <div className="welcome__head">
          <span className="meta welcome__ok"><i className="rec-dot" />Acceso concedido · {new Date().toLocaleTimeString("es-CR", { hour12: false, timeZone: "America/Costa_Rica" })}</span>
          <div className="welcome__badge"><span>{rm.glyph}</span></div>
          <h2 className="welcome__title">Hola, {first}.</h2>
          <p className="welcome__role">Entraste como <b>{rm.label}</b> en {me?.tenant.name}.</p>
        </div>
        {/* Andres: "explica de mas". Solo lo que la persona puede hacer, en una lista corta; lo que no puede
            lo descubre el sistema sin necesidad de leerlo aqui. */}
        <ul className="welcome__can">{rm.can.slice(0, 4).map((c) => <li key={c}><Icon d={I.check} size={14} />{c}</li>)}</ul>
        <div className="welcome__actions">
          {shortcuts.map((s) => <button key={s.to} className="btn btn--soft btn--sm" onClick={() => { onClose(); nav(s.to); }}><Icon d={s.icon} size={15} />{s.label}</button>)}
          <button className="btn btn--crimson" onClick={onClose} style={{ marginLeft: "auto" }}>Empezar<Icon d={I.arrow} size={15} /></button>
        </div>
      </div>
    </div>
  );
}
