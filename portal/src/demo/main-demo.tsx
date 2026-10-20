/* Entrada de la DEMOSTRACION ESTATICA de la tienda (build aparte: vite.demo.config.ts -> docs/demo-tienda/).
   Monta la vitrina real (modules/store/PublicStore) en modo dominio propio, con la API simulada en el navegador
   (mockApi) y el marco de demo (DemoShell). No toca el build normal del portal (main.tsx).

   Rutas: HashRouter (crimsoncr.com/demo-tienda/#/catalogo). GitHub Pages no reescribe rutas y el 404.html del
   sitio es el de la landing; con el hash cualquier enlace profundo y cualquier recarga funcionan sin servidor.

   Propuestas: cada carpeta propuestas/<equipo>/index.tsx exporta { rutas, etiquetas } y se enlaza SOLO por ruta
   (/propuestas…, /detras-de-escena…, /rentabilidad). Las carpetas que empiezan con "_" no son modulos. */
import { lazy, StrictMode, Suspense, useEffect, useState, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import "../ui/base.css";
import "../ui/access.css";
import "../ui/field.css";
import "./demo.css";
import DemoGate from "./DemoGate";
import DemoShell from "./DemoShell";
import { installMockApi } from "./mockApi";
import { EVENTOS, type Etiqueta } from "./propuestas/registro";

installMockApi();

const PublicStore = lazy(() => loadStore());
const FX_REF = 512; // el mismo de portal/scripts/demo_snapshot.py

// Candado + bienvenida VIP (portal/src/demo/vip). Si ese modulo existe se usa VipGate; si no, queda el candado
// simple de DemoGate. glob en vez de import fijo: el build no se rompe si falta.
type GateProps = { children: ReactNode; guestName?: string; productPath?: string; router?: "hash" | "browser"; preload?: () => Promise<unknown> };
type GateMod = { VipGate?: ComponentType<GateProps>; default?: ComponentType<GateProps> };
const vip = Object.values(import.meta.glob<GateMod>("./vip/index.{ts,tsx}", { eager: true }))[0];
const Gate: ComponentType<GateProps> = vip?.VipGate || vip?.default || DemoGate;
const loadStore = () => import("../modules/store/PublicStore");

// ---------- modulos de propuestas (eager: cada index.tsx es liviano y sus pantallas van en lazy)
type RutaModulo = { path: string; element: ReactNode };
type ModuloPropuesta = { rutas?: RutaModulo[]; etiquetas?: Etiqueta[] };
const modulos = Object.entries(import.meta.glob<ModuloPropuesta>(["./propuestas/*/index.tsx", "!./propuestas/_*/**"], { eager: true }))
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([, m]) => m);
const PREFIJO = /^\/(propuestas|detras-de-escena|rentabilidad)(\/|$)/;
const vistas = new Set<string>();
const rutasExtra = modulos.flatMap((m) => m.rutas ?? []).filter((r) => {
  const dup = vistas.has(r.path);
  vistas.add(r.path);
  const ok = !dup && PREFIJO.test(r.path) && !r.path.includes("*");
  if (!ok && import.meta.env.DEV) console.warn("[demo] ruta descartada:", r.path);
  return ok;
});
const etiquetasExtra = modulos.flatMap((m) => m.etiquetas ?? []);
const hay = (p: string) => rutasExtra.some((r) => r.path === p);

/** Mejora 3: PublicStore lee el carrito solo al montar; el evento la vuelve a montar para que relea localStorage. */
function TiendaConLlave() {
  const [n, setN] = useState(0);
  useEffect(() => {
    const on = () => setN((x) => x + 1);
    window.addEventListener(EVENTOS.recargarTienda, on);
    return () => window.removeEventListener(EVENTOS.recargarTienda, on);
  }, []);
  return <PublicStore key={n} slug="crimson" hostMode />;
}

/** Seccion que todavia no esta en esta vista previa (un modulo de otro equipo aun sin integrar). */
function NoDisponible() {
  useEffect(() => { window.scrollTo(0, 0); document.getElementById("contenido")?.focus({ preventScroll: true }); }, []);
  return (
    <main id="contenido" tabIndex={-1} className="demo-pendiente">
      <span className="demo-pendiente__ceja">Vista previa</span>
      <h1>Esta pantalla todavía no está en esta vista previa</h1>
      <p>Se suma en la próxima actualización de la demostración.</p>
      <span className="demo-pendiente__acc"><Link to="/propuestas">Ver lo que viene</Link><Link to="/">Volver a la tienda</Link></span>
    </main>
  );
}

const Spin = () => <div className="demo-spin" role="status" aria-label="Cargando"><span className="spinner" /></div>;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Gate guestName="Andrés" productPath="/p/camara-bullet-hikvision-ds-2cd2043g2-li2u-2-8mm-4mp-acusense-smart-68" router="hash" preload={loadStore}>
      <HashRouter>
        <DemoShell fx={FX_REF} etiquetasExtra={etiquetasExtra}>
          <Suspense fallback={<Spin />}>
            <Routes>
              {rutasExtra.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}
              <Route path="/propuestas/*" element={hay("/propuestas") ? <Navigate to="/propuestas" replace /> : <NoDisponible />} />
              <Route path="/detras-de-escena/*" element={hay("/detras-de-escena") ? <Navigate to="/detras-de-escena" replace /> : <NoDisponible />} />
              {!hay("/rentabilidad") && <Route path="/rentabilidad" element={<NoDisponible />} />}
              <Route path="/*" element={<TiendaConLlave />} />
            </Routes>
          </Suspense>
        </DemoShell>
      </HashRouter>
    </Gate>
  </StrictMode>,
);
