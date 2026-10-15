import { lazy, StrictMode, Suspense, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./ui/base.css";
import "./ui/access.css";
import "./ui/field.css";
import { SessionProvider, useSession } from "./app/session";
import AppShell from "./app/AppShell";
import Guard from "./app/Guard";
import Login from "./modules/auth/Login";
import { isClientRole } from "./app/roles";
import StoreHost, { isStoreHost } from "./modules/store/StoreHost";
const Dashboard = lazy(() => import("./modules/dashboard/Dashboard"));
const DocList = lazy(() => import("./modules/sales/DocList"));
const DocEditor = lazy(() => import("./modules/sales/DocEditor"));
const InvoiceDetail = lazy(() => import("./modules/sales/InvoiceDetail"));
const Customers = lazy(() => import("./modules/crm/Customers"));
const Products = lazy(() => import("./modules/catalog/Products"));
const CabysBulk = lazy(() => import("./modules/catalog/CabysBulk"));
const Settings = lazy(() => import("./modules/Settings"));
const PayPage = lazy(() => import("./modules/pay/PayPage"));
const Payments = lazy(() => import("./modules/payments/Payments"));
const Inventory = lazy(() => import("./modules/inventory/Inventory"));
// Contabilidad en tres partes (Facturas, Contable, Tributario): hub con rutas anidadas
const AccountingHub = lazy(() => import("./modules/accounting/AccountingHub"));
const ReceptionInbox = lazy(() => import("./modules/accounting/facturas/Inbox"));
const ReceptionDetail = lazy(() => import("./modules/accounting/facturas/ReceptionDetail"));
const MailLog = lazy(() => import("./modules/accounting/facturas/MailLog"));
const Expenses = lazy(() => import("./modules/accounting/contable/Expenses"));
const Categories = lazy(() => import("./modules/accounting/contable/Categories"));
const Suppliers = lazy(() => import("./modules/accounting/contable/Suppliers"));
const Banks = lazy(() => import("./modules/accounting/contable/Banks"));
const TaxPanel = lazy(() => import("./modules/accounting/tributario/TaxPanel"));
const Reports = lazy(() => import("./modules/reports/Reports"));
const Recurrences = lazy(() => import("./modules/sales/Recurrences"));
const Invite = lazy(() => import("./modules/auth/Invite"));
const Store = lazy(() => import("./modules/store/Store"));
const PublicStore = lazy(() => import("./modules/store/PublicStore"));
const Orders = lazy(() => import("./modules/store/Orders"));
const Coupons = lazy(() => import("./modules/catalog/Coupons"));
const CustomerDetail = lazy(() => import("./modules/crm/CustomerDetail"));
const Pos = lazy(() => import("./modules/pos/Pos"));
const PublicEvent = lazy(() => import("./modules/events/PublicEvent"));
const TicketPage = lazy(() => import("./modules/events/TicketPage"));
const Payroll = lazy(() => import("./modules/payroll/Payroll"));
const Banking = lazy(() => import("./modules/accounting/Banking"));
const Importer = lazy(() => import("./modules/tools/Importer"));
const Opportunities = lazy(() => import("./modules/crm/Opportunities"));
const Surveys = lazy(() => import("./modules/field/Surveys"));
const WorkOrders = lazy(() => import("./modules/field/WorkOrders"));
const Projects = lazy(() => import("./modules/projects/Projects"));
const Assets = lazy(() => import("./modules/projects/Assets"));
const Purchases = lazy(() => import("./modules/projects/Purchases"));
const Tickets = lazy(() => import("./modules/support/Tickets"));
const Contracts = lazy(() => import("./modules/support/Contracts"));
const Commissions = lazy(() => import("./modules/support/Commissions"));
const Archive = lazy(() => import("./modules/tools/Archive"));
const AccessRequests = lazy(() => import("./modules/clientaccess/AccessRequests"));
const Vehicles = lazy(() => import("./modules/vehicles/Vehicles"));
const Approvals = lazy(() => import("./modules/approvals/Approvals"));
const RequestAccess = lazy(() => import("./modules/auth/RequestAccess"));
const ResetPassword = lazy(() => import("./modules/auth/ResetPassword"));
const ForgotPassword = lazy(() => import("./modules/auth/ForgotPassword"));
// Portal del cliente: su propio layout y su propio menu (nunca el panel interno)
const ClientShell = lazy(() => import("./modules/cliente/ClientShell"));
const ClientHome = lazy(() => import("./modules/cliente/ClientHome"));
const ClientTickets = lazy(() => import("./modules/cliente/ClientTickets"));
const ClientTicket = lazy(() => import("./modules/cliente/ClientTicket"));
const ClientNewTicket = lazy(() => import("./modules/cliente/ClientNewTicket"));
const ClientAssets = lazy(() => import("./modules/cliente/ClientAssets"));
const ClientDocs = lazy(() => import("./modules/cliente/ClientDocs"));
const ClientUsers = lazy(() => import("./modules/cliente/ClientUsers"));
const ClientAccount = lazy(() => import("./modules/cliente/ClientAccount"));

function Private() {
  const { me, loading } = useSession();
  if (loading) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  if (!me) return <Login />;
  return isClientRole(me.role) ? <Suspense fallback={<Loading />}><ClientShell /></Suspense> : <AppShell />;
}

/** /login: pantalla de acceso (el boton "Ingresar" de la landing apunta aqui). Con sesion activa entra directo. */
function LoginRoute() {
  const { me, loading } = useSession();
  if (loading) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  return me ? <Navigate to="/" replace /> : <Login />;
}

const g = (need: string, el: ReactElement) => <Guard need={need}>{el}</Guard>;

const Loading = () => <div style={{ minHeight: "60vh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;

function App() {
  const { me } = useSession();
  const cliente = isClientRole(me?.role);
  return (
    <Suspense fallback={<Loading />}>
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/pagar/:token" element={<PayPage />} />
      <Route path="/invitacion/:token" element={<Invite />} />
      <Route path="/restablecer/:token" element={<ResetPassword />} />
      <Route path="/recuperar" element={<ForgotPassword />} />
      <Route path="/tienda/:slug/*" element={<PublicStore />} />
      {/* Eventos salio del panel (13/10, 02:26); la pagina publica del evento y las entradas vendidas siguen */}
      <Route path="/eventos/:slug/:event" element={<PublicEvent />} />
      <Route path="/entrada/:code" element={<TicketPage />} />
      <Route path="/solicitar-acceso" element={<RequestAccess />} />
      {cliente ? (
        <Route element={<Private />}>
          <Route path="/" element={<ClientHome />} />
          <Route path="/mis-tickets" element={<ClientTickets />} />
          <Route path="/mis-tickets/nuevo" element={<ClientNewTicket />} />
          <Route path="/mis-tickets/:id" element={<ClientTicket />} />
          <Route path="/mis-equipos" element={<ClientAssets />} />
          <Route path="/documentos" element={g("portal.documentos", <ClientDocs />)} />
          <Route path="/mi-empresa" element={g("portal.usuarios", <ClientUsers />)} />
          <Route path="/mi-cuenta" element={<ClientAccount />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      ) : (
      <Route element={<Private />}>
        <Route path="/" element={g("dashboard.ver", <Dashboard />)} />
        <Route path="/cotizaciones" element={g("sales.ver", <DocList kind="quotes" />)} />
        <Route path="/cotizaciones/:id" element={g("sales.ver", <DocEditor kind="quote" />)} />
        <Route path="/facturas" element={g("sales.ver", <DocList kind="invoices" />)} />
        <Route path="/facturas/nueva" element={g("sales.crear", <DocEditor kind="invoice" />)} />
        <Route path="/facturas/:id" element={g("sales.ver", <InvoiceDetail />)} />
        <Route path="/pagos" element={g("payments.ver", <Payments />)} />
        <Route path="/recurrencias" element={g("sales.recurrencias", <Recurrences />)} />
        <Route path="/clientes" element={g("crm.ver", <Customers />)} />
        <Route path="/clientes/:id" element={g("crm.ver", <CustomerDetail />)} />
        <Route path="/pos" element={g("sales.crear", <Pos />)} />
        <Route path="/planillas" element={g("payroll.ver", <Payroll />)} />
        <Route path="/conciliacion" element={<Navigate to="/contabilidad/contable/bancos" replace />} />
        <Route path="/importar" element={g("settings.configurar|accounting.crear", <Importer />)} />
        <Route path="/archivo" element={g("field.editar|projects.editar|crm_pipeline.editar|crm.editar|catalog.editar|support_desk.editar|assets.editar|purchases.crear", <Archive />)} />
        <Route path="/oportunidades" element={g("crm_pipeline.ver", <Opportunities />)} />
        <Route path="/levantamientos" element={g("field.ver", <Surveys />)} />
        <Route path="/ordenes-trabajo" element={g("field.ver", <WorkOrders />)} />
        <Route path="/proyectos" element={g("projects.ver", <Projects />)} />
        <Route path="/proyectos/:id" element={g("projects.ver", <Projects />)} />
        <Route path="/activos" element={g("assets.ver", <Assets />)} />
        <Route path="/compras" element={g("purchases.ver", <Purchases />)} />
        <Route path="/tickets" element={g("support_desk.ver", <Tickets />)} />
        <Route path="/mantenimientos" element={g("support_desk.ver", <Contracts />)} />
        <Route path="/comisiones" element={g("commissions.ver", <Commissions />)} />
        <Route path="/productos" element={g("catalog.ver", <Products />)} />
        <Route path="/productos/cabys" element={g("catalog.editar", <CabysBulk />)} />
        <Route path="/inventario" element={g("inventory.ver", <Inventory />)} />
        <Route path="/mi-tienda" element={g("settings.configurar", <Store />)} />
        <Route path="/ordenes" element={g("sales.ver", <Orders />)} />
        <Route path="/recepcion" element={<Navigate to="/contabilidad/facturas" replace />} />
        <Route path="/cupones" element={g("catalog.editar", <Coupons />)} />
        <Route path="/reportes" element={g("reports.ver", <Reports />)} />
        <Route path="/contabilidad" element={g("accounting.ver", <AccountingHub />)}>
          <Route index element={<Navigate to="/contabilidad/facturas" replace />} />
          <Route path="facturas" element={<ReceptionInbox />} />
          <Route path="facturas/recibidas" element={<ReceptionInbox all />} />
          <Route path="facturas/correos" element={<MailLog />} />
          <Route path="facturas/:id" element={<ReceptionDetail />} />
          <Route path="contable" element={<Expenses />} />
          <Route path="contable/recurrentes" element={<Recurrences kind="gasto" />} />
          <Route path="contable/categorias" element={<Categories />} />
          <Route path="contable/proveedores" element={<Suppliers />} />
          <Route path="contable/bancos" element={<Banks />} />
          <Route path="contable/bancos/:id/conciliacion" element={<Banking />} />
          <Route path="tributario" element={<TaxPanel />} />
        </Route>
        <Route path="/ajustes" element={<Settings />} />
        <Route path="/accesos-clientes" element={g("portal_clientes.ver", <AccessRequests />)} />
        <Route path="/vehiculos" element={g("vehicles.ver", <Vehicles />)} />
        <Route path="/aprobaciones" element={g("approvals.ver", <Approvals />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      )}
    </Routes>
    </Suspense>
  );
}

if (!isStoreHost()) {
  // el panel nunca se indexa (la cabecera X-Robots-Tag de nginx dice lo mismo; esto cubre el servidor de desarrollo)
  const m = document.createElement("meta");
  m.name = "robots";
  m.content = "noindex";
  document.head.appendChild(m);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      {/* Dominio de la tienda (tienda.crimsoncr.com): solo la vitrina, sin sesion ni panel */}
      {isStoreHost() ? (
        <StoreHost />
      ) : (
        <SessionProvider>
          <App />
        </SessionProvider>
      )}
    </BrowserRouter>
  </StrictMode>,
);
