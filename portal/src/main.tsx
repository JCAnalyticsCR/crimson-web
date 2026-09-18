import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./ui/base.css";
import { SessionProvider, useSession } from "./app/session";
import AppShell from "./app/AppShell";
import Login from "./modules/auth/Login";
import Dashboard from "./modules/dashboard/Dashboard";
import DocList from "./modules/sales/DocList";
import DocEditor from "./modules/sales/DocEditor";
import InvoiceDetail from "./modules/sales/InvoiceDetail";
import Customers from "./modules/crm/Customers";
import Products from "./modules/catalog/Products";
import Settings from "./modules/Settings";
import PayPage from "./modules/pay/PayPage";
import Payments from "./modules/payments/Payments";
import Inventory from "./modules/inventory/Inventory";
import Accounting from "./modules/accounting/Accounting";
import Reports from "./modules/reports/Reports";
import Recurrences from "./modules/sales/Recurrences";
import Invite from "./modules/auth/Invite";
import { Empty } from "./ui/components";

function Soon({ name }: { name: string }) {
  return <><div className="page-head"><div><div className="meta">Próximamente</div><h1 className="h1">{name}</h1></div></div><Empty title={`${name} llega en la siguiente fase`} hint="Ya está en el plan maestro; el modelo de datos lo contempla." /></>;
}

function Private() {
  const { me, loading } = useSession();
  if (loading) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  return me ? <AppShell /> : <Login />;
}

function App() {
  return (
    <Routes>
      <Route path="/pagar/:token" element={<PayPage />} />
      <Route path="/invitacion/:token" element={<Invite />} />
      <Route element={<Private />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/cotizaciones" element={<DocList kind="quotes" />} />
        <Route path="/cotizaciones/:id" element={<DocEditor kind="quote" />} />
        <Route path="/facturas" element={<DocList kind="invoices" />} />
        <Route path="/facturas/nueva" element={<DocEditor kind="invoice" />} />
        <Route path="/facturas/:id" element={<InvoiceDetail />} />
        <Route path="/pagos" element={<Payments />} />
        <Route path="/recurrencias" element={<Recurrences />} />
        <Route path="/clientes" element={<Customers />} />
        <Route path="/productos" element={<Products />} />
        <Route path="/inventario" element={<Inventory />} />
        <Route path="/tienda" element={<Soon name="Mi Tienda" />} />
        <Route path="/reportes" element={<Reports />} />
        <Route path="/contabilidad" element={<Accounting />} />
        <Route path="/ajustes" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
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
