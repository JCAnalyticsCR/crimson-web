import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./ui/base.css";
import "./ui/access.css";
import "./ui/field.css";
import StoreHost, { isStoreHost } from "./modules/store/StoreHost";
/* O2-01d: el panel (sesion, login, menu, cascaron y sus rutas) va en su propio trozo. La tienda en dominio propio solo
   descarga StoreHost y la vitrina; el panel lo pide aqui, de inmediato, solo fuera de la tienda. */
const PanelApp = lazy(() => import("./app/PanelApp"));

const Spin = () => <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;

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
        <Suspense fallback={<Spin />}>
          <PanelApp />
        </Suspense>
      )}
    </BrowserRouter>
  </StrictMode>,
);
