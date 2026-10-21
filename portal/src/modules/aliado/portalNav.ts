/* Menu del portal de aliados: lo usan el portal (AllyShell) y "Que ve cada rol" en Ajustes -> Usuarios (RoleMatrix).
   `need` es la accion del modulo "aliado" que exige cada seccion. */
import { I } from "../../ui/components";

export type Item = { to: string; label: string; short: string; icon: string; need?: string; end?: boolean; tab?: boolean; feature?: "ganancias" };
export const ALLY_ITEMS: Item[] = [
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
