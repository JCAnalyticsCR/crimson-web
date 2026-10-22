/* "Qué ve cada rol" (Ajustes -> Usuarios): la matriz se genera del código — secciones del menú (nav.ts) x roles
   (roles.ts) con los permisos que manda la API (/auth/roles, ya con "Quién ve costos"). Andrés preguntó si
   "Técnico", "Catálogo" o "Administración" eran roles (22:30-22:45): aquí se ve que son grupos del menú y qué
   sección le sale a cada rol. Nunca queda desactualizada como un texto escrito a mano. */
import { Fragment, type CSSProperties, type ReactNode } from "react";
import { NAV } from "./nav";
import { ROLES, canAny, customRoleCodes, inRoleMenu, isExternalRole, roleMeta } from "./roles";
import { useSession } from "./session";
import { ALLY_ITEMS } from "../modules/aliado/portalNav";

const MONEY: { label: string; need: string }[] = [
  { label: "Ve precio de venta", need: "catalog.precios" },
  { label: "Ve costos y márgenes", need: "catalog.costos" },
  { label: "Ve lo de toda la empresa", need: "sales.ver_todo" },
];

const yes = <span style={{ color: "var(--ok)", fontWeight: 700 }} aria-label="sí">✓</span>;
const no = <span className="muted" aria-label="no">—</span>;

export default function RoleMatrix() {
  const { matrix } = useSession();
  // mismo orden que Ajustes y "Ver como"; sin soporte ni roles de cliente o aliado (tienen su propio portal)
  const roles = [...Object.keys(ROLES), ...customRoleCodes()].filter((r) => r !== "soporte" && !isExternalRole(r) && matrix[r]);
  if (!roles.length) return <p className="muted" style={{ fontSize: 13, margin: 0 }}>Cargando la matriz de roles…</p>;

  const cell = { padding: "6px 10px", textAlign: "center" as const };
  const first = { padding: "6px 12px", position: "sticky" as const, left: 0, background: "var(--surface)", zIndex: 1, whiteSpace: "nowrap" as const };
  return (
    // en el teléfono la tabla se desplaza sola; la página no
    <div style={{ overflowX: "auto", maxWidth: "100%", WebkitOverflowScrolling: "touch" }}>
      <table className="table" style={{ fontSize: 12.5, minWidth: 140 + roles.length * 92 }}>
        <thead>
          <tr>
            <th style={first}>Sección</th>
            {roles.map((r) => <th key={r} style={{ ...cell, whiteSpace: "normal", lineHeight: 1.25 }} title={roleMeta(r).summary}>{roleMeta(r).label}</th>)}
          </tr>
        </thead>
        <tbody>
          {NAV.map((g) => (
            <Fragment key={g.group}>
              <tr><td colSpan={roles.length + 1} className="meta" style={{ padding: "10px 12px 4px", background: "var(--bg-2)" }}>{g.group}</td></tr>
              {g.items.map((it) => (
                <tr key={it.to}>
                  <td style={first}>{it.label}</td>
                  {roles.map((r) => <td key={r} style={cell}>{canAny(matrix[r], it.need) && inRoleMenu(r, it.to) ? yes : no}</td>)}
                </tr>
              ))}
            </Fragment>
          ))}
          <tr><td colSpan={roles.length + 1} className="meta" style={{ padding: "10px 12px 4px", background: "var(--bg-2)" }}>Dinero</td></tr>
          {MONEY.map((m) => (
            <tr key={m.need}>
              <td style={first}>{m.label}</td>
              {roles.map((r) => <td key={r} style={cell}>{canAny(matrix[r], m.need) ? yes : no}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <AllyMatrix cell={cell} first={first} />
    </div>
  );
}

/* Los dos roles de empresa aliada no entran al panel: usan el portal de aliados. Se asignan desde Administración ->
   Aliados -> ficha -> Usuarios (o el aliado comercial invita a sus técnicos desde «Mi equipo»).
   Reunión 2 (21/10, pedido 20): la tabla dice también QUÉ ve cada uno dentro de su portal (DETALLE). */
const DETALLE: { label: string; aliado: ReactNode; tecnico: ReactNode }[] = [
  { label: "Oportunidades en común: etapa, próxima acción, su alcance y los seguimientos que Crimson comparta", aliado: "✓", tecnico: "Solo la lista en su inicio" },
  { label: "Ejecución del proyecto en común (órdenes y técnicos, sin precios)", aliado: "✓", tecnico: "Solo sus órdenes" },
  { label: "Levantamientos", aliado: "Los de su empresa", tecnico: "Los asignados a él" },
  { label: "Monto total de la cotización", aliado: "Solo si se habilita en la ficha del aliado", tecnico: "—" },
  { label: "Precios del catálogo", aliado: "Precio de aliado", tecnico: "—" },
];
function AllyMatrix({ cell, first }: { cell: CSSProperties; first: CSSProperties }) {
  const { matrix } = useSession();
  const roles = (["aliado", "tecnico_aliado"] as const).filter((r) => matrix[r]);
  if (!roles.length) return null;
  const tiene = (r: string, need?: string) => !need || canAny(matrix[r], `aliado.${need}`);
  return (
    <>
      <h4 className="h3" style={{ margin: "20px 0 6px" }}>Portal de aliados</h4>
      <p className="meta" style={{ margin: "0 0 8px" }}>Solo ven lo de su propia empresa. Se dan de alta en Administración → Aliados → la ficha del aliado → Usuarios; el aliado comercial además invita a sus técnicos desde «Mi equipo».</p>
      <table className="table" style={{ fontSize: 12.5, minWidth: 140 + roles.length * 110 }}>
        <thead><tr><th style={first}>Sección del portal</th>{roles.map((r) => <th key={r} style={{ ...cell, whiteSpace: "normal" }} title={ROLES[r].summary}>{ROLES[r].label}</th>)}</tr></thead>
        <tbody>
          {ALLY_ITEMS.map((it) => (
            <tr key={it.to}>
              <td style={first}>{it.to === "/" ? "Inicio (técnico: «Hoy», sus próximas órdenes y las oportunidades en común)" : it.label}</td>
              {roles.map((r) => {
                // el técnico entra solo por «Hoy»: ve levantamientos, órdenes y tickets asignados a él, nada más
                const ok = r === "tecnico_aliado" ? it.to === "/" || (["levantamientos", "ordenes", "tickets"].includes(it.need || "") && tiene(r, it.need)) : tiene(r, it.need);
                return <td key={r} style={cell}>{ok ? yes : no}</td>;
              })}
            </tr>
          ))}
          <tr><td colSpan={roles.length + 1} className="meta" style={{ padding: "10px 12px 4px", background: "var(--bg-2)" }}>Qué ve dentro de su portal</td></tr>
          {DETALLE.map((x) => (
            <tr key={x.label}>
              <td style={{ ...first, whiteSpace: "normal", maxWidth: 320 }}>{x.label}</td>
              {roles.map((r) => {
                const v = r === "aliado" ? x.aliado : x.tecnico;
                return <td key={r} style={{ ...cell, fontSize: 12 }}>{v === "✓" ? yes : v === "—" ? no : v}</td>;
              })}
            </tr>
          ))}
          <tr><td style={first}>Ve costos o márgenes de Crimson</td>{roles.map((r) => <td key={r} style={cell}>{no}</td>)}</tr>
        </tbody>
      </table>
    </>
  );
}
