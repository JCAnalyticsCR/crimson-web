/* Entrada de la DEMOSTRACION ESTATICA de la tienda (build aparte: vite.demo.config.ts -> docs/demo-tienda/).
   Monta SOLO la vitrina real (modules/store/PublicStore) en modo dominio propio, con la API simulada en el
   navegador (mockApi) y el marco de demo (DemoShell). No toca el build normal del portal (main.tsx).

   Rutas: HashRouter (crimsoncr.com/demo-tienda/#/catalogo). GitHub Pages no reescribe rutas y el 404.html del
   sitio es el de la landing; con el hash cualquier enlace profundo y cualquier recarga funcionan sin servidor. */
import { lazy, StrictMode, Suspense, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Route, Routes } from "react-router-dom";
import "../ui/base.css";
import "../ui/access.css";
import "../ui/field.css";
import "./demo.css";
import DemoGate from "./DemoGate";
import DemoShell from "./DemoShell";
import { installMockApi } from "./mockApi";

installMockApi();

const PublicStore = lazy(() => import("../modules/store/PublicStore"));
const FX_REF = 512; // el mismo de portal/scripts/demo_snapshot.py

// Candado + bienvenida VIP (portal/src/demo/vip, otro frente de trabajo). Si ese modulo existe se usa VipGate;
// si no, queda el candado simple de DemoGate. glob en vez de import fijo: el build no se rompe si falta.
type GateMod = { VipGate?: ComponentType<{ children: ReactNode }>; default?: ComponentType<{ children: ReactNode }> };
const vip = Object.values(import.meta.glob<GateMod>("./vip/index.{ts,tsx}", { eager: true }))[0];
const Gate: ComponentType<{ children: ReactNode }> = vip?.VipGate || vip?.default || DemoGate;

const Spin = () => <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", background: "#f6f2ee" }}><span className="spinner" /></div>;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Gate>
      <HashRouter>
        <DemoShell fx={FX_REF}>
          <Suspense fallback={<Spin />}>
            <Routes>
              <Route path="/*" element={<PublicStore slug="crimson" hostMode />} />
            </Routes>
          </Suspense>
        </DemoShell>
      </HashRouter>
    </Gate>
  </StrictMode>,
);
