/* Por que no se puede escribir aqui (Andres 16:16: "algo pasó porque ahora no puedo hacer nada. Solo lo puedo ver").
   Estaba en vista previa como Vendedor y la pantalla escondia los botones sin decir nada. Ahora se explica, en orden:
   el estado del registro (cerrado, en la papelera…), la vista previa de otro rol, el rol real y, con el permiso pero
   sin ser del registro, el alcance ("esta orden es de otro tecnico"). Si no, no se dibuja nada. Lo usan el detalle
   del ticket y el de la orden de trabajo. */
import type { ReactNode } from "react";
import { roleMeta } from "../app/roles";
import { useSession } from "../app/session";
import { I, Icon } from "./components";

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function ReadOnlyNotice(props: {
  /** permiso que haria falta, p. ej. "support_desk.seguir|support_desk.editar" */
  need: string;
  /** "este ticket", "esta orden de trabajo" */
  what: string;
  /** "responderlo, resolverlo ni reasignarlo" */
  action: string;
  /** motivo por estado del registro (tiene prioridad): "cerrado el 13 oct", "en la papelera"… */
  state?: string | null;
  /** boton "Reabrir" cuando el motivo es el estado y hay permiso */
  onReopen?: () => void;
  /** el rol tiene el permiso pero el registro no es suyo: "Esta orden es de Ana: solo el equipo de la orden…" */
  scope?: string | null;
}) {
  const { allows, viewAs, setViewAs, role } = useSession();
  const box = { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" as const, border: "1px solid var(--info)", background: "var(--info-soft)", borderRadius: 10, padding: "10px 12px", fontSize: 13, marginBottom: 14 };
  const rol = roleMeta(role).label;
  let text: ReactNode = null;
  let btn: ReactNode = null;
  if (props.state) {
    text = `${capital(props.what)} está ${props.state}.`;
    if (props.onReopen) btn = <button type="button" className="btn btn--soft btn--sm" onClick={props.onReopen}>Reabrir</button>;
  } else if (!allows(props.need) && viewAs) {
    text = <><b>Vista previa como {rol}</b>: este rol puede ver {props.what} pero no {props.action}. Tus permisos reales no cambian.</>;
    btn = <button type="button" className="btn btn--soft btn--sm" onClick={() => setViewAs(null)}>Salir de la vista previa</button>;
  } else if (!allows(props.need)) {
    text = `Tu rol (${rol}) puede ver ${props.what} pero no ${props.action}. Si lo necesitás, pedíselo a un administrador.`;
  } else if (props.scope && viewAs) {
    text = <><b>Vista previa como {rol}</b>: {props.scope} Tus permisos reales no cambian.</>;
    btn = <button type="button" className="btn btn--soft btn--sm" onClick={() => setViewAs(null)}>Salir de la vista previa</button>;
  } else if (props.scope) {
    text = props.scope;
  } else {
    return null;
  }
  return (
    <div role="note" style={box}>
      <span style={{ color: "var(--info)", display: "inline-flex" }}><Icon d={I.lock} size={16} /></span>
      <span style={{ flex: "1 1 220px", minWidth: 0 }}>{text}</span>
      {btn}
    </div>
  );
}
