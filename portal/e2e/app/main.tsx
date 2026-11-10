/* Entrada de la tienda para las pruebas de Playwright (build aparte: vite.e2e.config.ts; no toca el build normal ni
   la demo). Monta lo mismo que monta src/main.tsx en el dominio de la tienda (StrictMode > BrowserRouter >
   StoreHost > PublicStore en modo dominio propio, con las mismas hojas base), pero con el API simulado en el
   navegador y un catalogo ficticio (api.ts, datos.ts). Sin candado, sin marco de demo, sin nada de terceros. */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "../../src/ui/base.css";
import "../../src/ui/access.css";
import "../../src/ui/field.css";
import StoreHost from "../../src/modules/store/StoreHost";
import { instalarApiE2e } from "./api";

instalarApiE2e();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <StoreHost />
    </BrowserRouter>
  </StrictMode>,
);
