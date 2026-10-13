/* Entry del banco de pruebas: VipGate con una tienda falsa (mismas clases que la vitrina real) cargada en lazy.
   Variantes por query:
     (nada)        HashRouter envolviendo a VipGate  -> navega con el navigate() del router (NavBridge)
     ?afuera=1     VipGate fuera, HashRouter adentro -> respaldo por location.hash
     ?navegador=1  VipGate fuera, BrowserRouter adentro -> respaldo por pushState + popstate (sin recargar) */
import { lazy, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import "../../../ui/tokens.css"; // solo por las @font-face del portal (sin fuentes externas)
import { VipGate } from "../index";

const loadStore = () => import("./FakeStore");
const FakeStore = lazy(loadStore);
const q = new URLSearchParams(location.search);
const BASE = "/src/demo/vip/preview.html";

let app;
if (q.has("navegador")) {
  app = (
    <VipGate guestName="Andrés" productPath="/p/camara-domo-ds-2cd1143-12" router="browser" basename={BASE} preload={loadStore}>
      <BrowserRouter basename={BASE}><FakeStore /></BrowserRouter>
    </VipGate>
  );
} else if (q.has("afuera")) {
  app = (
    <VipGate guestName="Andrés" productPath="/p/camara-domo-ds-2cd1143-12" router="hash" preload={loadStore}>
      <HashRouter><FakeStore /></HashRouter>
    </VipGate>
  );
} else {
  app = (
    <HashRouter>
      <VipGate guestName="Andrés" productPath="/p/camara-domo-ds-2cd1143-12" preload={loadStore}>
        <FakeStore />
      </VipGate>
    </HashRouter>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode>{app}</StrictMode>);
