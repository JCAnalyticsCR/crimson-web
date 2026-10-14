/* Menu lateral del panel interno. Cada entrada declara que permiso necesita ("modulo.accion", varias con |).
   Lo que ve cada rol = permisos del rol ∩ su lista explicita en ROLES[rol].menu (roles.ts), si la tiene.
   Para agregar una seccion (p. ej. Aprobaciones): una entrada aqui con su `need`; si un rol con lista debe verla,
   sumar la ruta a su `menu` en roles.ts. La API sigue siendo la frontera de seguridad. */
import { I } from "../ui/components";

export type NavItem = { to: string; label: string; icon: string; end?: boolean; need?: string };
export type NavGroup = { group: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  // Las cuatro secciones son las que Andres escribio en sus notas de la reunion del 22/09.
  { group: "Operación", items: [
    { to: "/", label: "Inicio", icon: I.home, end: true, need: "dashboard.ver" },
    { to: "/oportunidades", label: "Oportunidades", icon: I.trend, need: "crm_pipeline.ver" },
    { to: "/cotizaciones", label: "Cotizaciones", icon: I.quote, need: "sales.ver" },
    { to: "/proyectos", label: "Proyectos", icon: I.box, need: "projects.ver" },
    { to: "/ordenes-trabajo", label: "Órdenes de trabajo", icon: I.check, need: "field.ver" },
    { to: "/facturas", label: "Facturas", icon: I.invoice, need: "sales.ver" },
    { to: "/pagos", label: "Pagos", icon: I.pay, need: "payments.ver" },
    { to: "/pos", label: "Punto de venta", icon: I.wallet, need: "sales.crear" },
    { to: "/recurrencias", label: "Recurrencias", icon: I.refresh, need: "sales.recurrencias" },
  ]},
  { group: "Técnico", items: [
    { to: "/levantamientos", label: "Levantamientos", icon: I.ruler, need: "field.ver" },
    { to: "/tickets", label: "Soporte", icon: I.life, need: "support_desk.ver" },
    { to: "/mantenimientos", label: "Mantenimientos", icon: I.refresh, need: "support_desk.ver" },
    { to: "/activos", label: "Activos del cliente", icon: I.shield, need: "assets.ver" },
  ]},
  // Eventos salio del menu (13/10, 02:26: "nosotros no hacemos eventos"). El codigo, los datos y las paginas
  // publicas de eventos y entradas se quedan; el admin conserva la API.
  { group: "Catálogo", items: [
    { to: "/clientes", label: "Clientes", icon: I.customers, need: "crm.ver" },
    { to: "/accesos-clientes", label: "Accesos de clientes", icon: I.shield, need: "portal_clientes.ver" },
    { to: "/productos", label: "Productos & Servicios", icon: I.products, need: "catalog.ver" },
    { to: "/inventario", label: "Inventarios", icon: I.inventory, need: "inventory.ver" },
    { to: "/cupones", label: "Cupones", icon: I.wallet, need: "catalog.editar" },
    { to: "/mi-tienda", label: "Mi Tienda", icon: I.store, need: "settings.configurar" },
    { to: "/ordenes", label: "Pedidos de la tienda", icon: I.box, need: "sales.ver" },
  ]},
  { group: "Administración", items: [
    { to: "/compras", label: "Compras", icon: I.upload, need: "purchases.ver" },
    { to: "/comisiones", label: "Comisiones", icon: I.trend, need: "commissions.ver" },
    { to: "/contabilidad", label: "Contabilidad", icon: I.accounting, need: "accounting.ver" },
    { to: "/recepcion", label: "Recepción XML", icon: I.bell, need: "accounting.ver" },
    { to: "/conciliacion", label: "Conciliación", icon: I.bank, need: "accounting.ver" },
    { to: "/planillas", label: "Planillas", icon: I.team, need: "payroll.ver" },
    { to: "/vehiculos", label: "Vehículos", icon: "M3 13l2-5a2 2 0 0 1 1.9-1.4h10.2A2 2 0 0 1 19 8l2 5v4a1 1 0 0 1-1 1h-1|M3 13v4a1 1 0 0 0 1 1h1|M3 13h18|M9 18h6|M7 18a2 2 0 1 0 0 .1|M17 18a2 2 0 1 0 0 .1", need: "vehicles.ver" },
    { to: "/reportes", label: "Reportes", icon: I.reports, need: "reports.ver" },
    { to: "/importar", label: "Importar datos", icon: I.upload, need: "settings.configurar" },
    // archivados y papelera: lo ve quien puede archivar o mandar algo a la papelera (misma lista que la API)
    { to: "/archivo", label: "Archivo", icon: I.box, need: "field.editar|projects.editar|crm_pipeline.editar|crm.editar|catalog.editar|support_desk.editar|assets.editar|purchases.crear" },
    // sin `need`: todos la ven (Mi cuenta: contraseña y 2FA)
    { to: "/ajustes", label: "Ajustes", icon: I.settings },
  ]},
];
