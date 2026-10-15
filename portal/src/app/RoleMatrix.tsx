/* "Qué ve cada rol" (Ajustes -> Usuarios): la matriz se genera del código — secciones del menú (nav.ts) x roles
   (roles.ts) con los permisos que manda la API (/auth/roles, ya con "Quién ve costos"). Andrés preguntó si
   "Técnico", "Catálogo" o "Administración" eran roles (22:30-22:45): aquí se ve que son grupos del menú y qué
   sección le sale a cada rol. Nunca queda desactualizada como un texto escrito a mano. */
import { Fragment } from "react";
import { NAV } from "./nav";
import { ROLES, canAny, inRoleMenu, isExternalRole } from "./roles";
import { useSession } from "./session";

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
  const roles = Object.keys(ROLES).filter((r) => r !== "soporte" && !isExternalRole(r) && matrix[r]);
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
            {roles.map((r) => <th key={r} style={{ ...cell, whiteSpace: "normal", lineHeight: 1.25 }} title={ROLES[r].summary}>{ROLES[r].label}</th>)}
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
      <p className="meta" style={{ margin: "8px 0 0" }}>Aliado comercial y Técnico de aliado usan el portal de aliados (Administración → Aliados).</p>
    </div>
  );
}
