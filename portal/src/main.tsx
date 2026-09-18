import { lazy, StrictMode, Suspense, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./ui/base.css";
import "./ui/access.css";
import { SessionProvider, useSession } from "./app/session";
import AppShell from "./app/AppShell";
import Guard from "./app/Guard";
import Login from "./modules/auth/Login";
const Dashboard = lazy(() => import("./modules/dashboard/Dashboard"));
const DocList = lazy(() => import("./modules/sales/DocList"));
const DocEditor = lazy(() => import("./modules/sales/DocEditor"));
const InvoiceDetail = lazy(() => import("./modules/sales/InvoiceDetail"));
const Customers = lazy(() => import("./modules/crm/Customers"));
const Products = lazy(() => import("./modules/catalog/Products"));
const Settings = lazy(() => import("./modules/Settings"));
const PayPage = lazy(() => import("./modules/pay/PayPage"));
const Payments = lazy(() => import("./modules/payments/Payments"));
const Inventory = lazy(() => import("./modules/inventory/Inventory"));
const Accounting = lazy(() => import("./modules/accounting/Accounting"));
const Reports = lazy(() => import("./modules/reports/Reports"));
const Recurrences = lazy(() => import("./modules/sales/Recurrences"));
const Invite = lazy(() => import("./modules/auth/Invite"));
const Store = lazy(() => import("./modules/store/Store"));
const PublicStore = lazy(() => import("./modules/store/PublicStore"));
const Orders = lazy(() => import("./modules/store/Orders"));
const Reception = lazy(() => import("./modules/accounting/Reception"));
const Coupons = lazy(() => import("./modules/catalog/Coupons"));
const CustomerDetail = lazy(() => import("./modules/crm/CustomerDetail"));
const Pos = lazy(() => import("./modules/pos/Pos"));
const Events = lazy(() => import("./modules/events/Events"));
const EventDetail = lazy(() => import("./modules/events/EventDetail"));
const PublicEvent = lazy(() => import("./modules/events/PublicEvent"));
const TicketPage = lazy(() => import("./modules/events/TicketPage"));
const Payroll = lazy(() => import("./modules/payroll/Payroll"));
const Banking = lazy(() => import("./modules/accounting/Banking"));
const Importer = lazy(() => import("./modules/tools/Importer"));

function Private() {
  const { me, loading } = useSession();
  if (loading) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  return me ? <AppShell /> : <Login />;
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
  return (
    <Suspense fallback={<Loading />}>
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="/pagar/:token" element={<PayPage />} />
      <Route path="/invitacion/:token" element={<Invite />} />
      <Route path="/tienda/:slug" element={<PublicStore />} />
      <Route path="/eventos/:slug/:event" element={<PublicEvent />} />
      <Route path="/entrada/:code" element={<TicketPage />} />
      <Route element={<Private />}>
        <Route path="/" element={g("dashboard.ver", <Dashboard />)} />
        <Route path="/cotizaciones" element={g("sales.ver", <DocList kind="quotes" />)} />
        <Route path="/cotizaciones/:id" element={g("sales.ver", <DocEditor kind="quote" />)} />
        <Route path="/facturas" element={g("sales.ver", <DocList kind="invoices" />)} />
        <Route path="/facturas/nueva" element={g("sales.crear", <DocEditor kind="invoice" />)} />
        <Route path="/facturas/:id" element={g("sales.ver", <InvoiceDetail />)} />
        <Route path="/pagos" element={g("payments.ver", <Payments />)} />
        <Route path="/recurrencias" element={g("sales.crear", <Recurrences />)} />
        <Route path="/clientes" element={g("crm.ver", <Customers />)} />
        <Route path="/clientes/:id" element={g("crm.ver", <CustomerDetail />)} />
        <Route path="/pos" element={g("sales.crear", <Pos />)} />
        <Route path="/eventos" element={g("events.ver", <Events />)} />
        <Route path="/eventos/:id" element={g("events.ver", <EventDetail />)} />
        <Route path="/planillas" element={g("payroll.ver", <Payroll />)} />
        <Route path="/conciliacion" element={g("accounting.ver", <Banking />)} />
        <Route path="/importar" element={g("settings.configurar", <Importer />)} />
        <Route path="/productos" element={g("catalog.ver", <Products />)} />
        <Route path="/inventario" element={g("inventory.ver", <Inventory />)} />
        <Route path="/mi-tienda" element={g("settings.configurar", <Store />)} />
        <Route path="/ordenes" element={g("sales.ver", <Orders />)} />
        <Route path="/recepcion" element={g("accounting.ver", <Reception />)} />
        <Route path="/cupones" element={g("catalog.editar", <Coupons />)} />
        <Route path="/reportes" element={g("reports.ver", <Reports />)} />
        <Route path="/contabilidad" element={g("accounting.ver", <Accounting />)} />
        <Route path="/ajustes" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </Suspense>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
