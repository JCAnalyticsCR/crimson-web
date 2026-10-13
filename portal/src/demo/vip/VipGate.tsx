/* Experiencia VIP de entrada a la demo de la tienda: acceso con codigo -> bienvenida personal -> tienda (+ guia).

   Uso (main-demo.tsx):
     const Tienda = lazy(() => import("./TiendaDemo"));            // la tienda SIEMPRE en lazy
     <HashRouter>  (o BrowserRouter basename="/demo-tienda")
       <VipGate guestName="Andrés" productPath="/p/camara-domo-12" preload={() => import("./TiendaDemo")}>
         <Tienda />
       </VipGate>
     </HashRouter>

   Rendimiento: los hijos no se montan hasta pasar el acceso y la bienvenida, asi que un hijo lazy no descarga
   nada antes de tiempo. `preload` (opcional) adelanta esa descarga en cuanto el codigo es correcto, mientras se
   lee la bienvenida. Sin fuentes externas: usa las familias que ya carga el portal (con respaldo de sistema).

   El candado es DISUASIVO (ver pin.ts): no protege datos, solo evita que la demo se vea "en frio". */
import { Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import { useInRouterContext, useNavigate } from "react-router-dom";
import { configureNav, goTo, registerNavigate, type RouterMode } from "./nav";
import { KEY, ss } from "./pin";
import PinGate from "./PinGate";
import Welcome from "./Welcome";
import Tour from "./Tour";
import "./vip.css";

type Phase = "gate" | "welcome" | "store";

export type VipGateProps = {
  children: ReactNode;
  /** Nombre en la bienvenida. */
  guestName?: string;
  /** Ruta de una ficha real para la tarjeta "¿Lo instalamos nosotros?" y como respaldo de la guia. */
  productPath?: string;
  /** Basename del router si VipGate queda FUERA de el (por defecto, el base de Vite). */
  basename?: string;
  /** Fuerza el modo de navegacion si VipGate queda fuera del router (por defecto se detecta). */
  router?: RouterMode;
  /** Adelanta la descarga de la tienda apenas el codigo es correcto. */
  preload?: () => Promise<unknown>;
  /** Mostrar el boton flotante "Guia". */
  tour?: boolean;
};

/** Dentro de un router: registra su navigate() para que goTo() lo use (respeta basename y HashRouter). */
function NavBridge() {
  const navigate = useNavigate();
  useEffect(() => {
    registerNavigate((to) => navigate(to));
    return () => registerNavigate(null);
  }, [navigate]);
  return null;
}

const initialPhase = (): Phase => (ss.get(KEY.ok) !== "1" ? "gate" : ss.get(KEY.welcomed) !== "1" ? "welcome" : "store");

export function VipGate({ children, guestName = "Andrés", productPath = "/buscar?q=ds-2cd", basename, router, preload, tour = true }: VipGateProps) {
  const inRouter = useInRouterContext();
  const [phase, setPhase] = useState<Phase>(initialPhase);
  const [tourNow, setTourNow] = useState(false);
  configureNav({ mode: router, basename });

  // Titulo de la pestana mientras dure la antesala (la tienda pone el suyo al montar).
  useEffect(() => {
    if (phase === "store") return;
    const prev = document.title;
    document.title = phase === "gate" ? "Crimson · Tienda — Vista previa privada" : "Bienvenida · Crimson Tienda";
    return () => { document.title = prev; };
  }, [phase]);

  // Las pantallas de antesala arrancan arriba (si se venia scrolleado de otra vista).
  useEffect(() => { if (phase !== "store") window.scrollTo(0, 0); }, [phase]);

  const unlock = useCallback(() => {
    if (preload) {
      const run = () => { preload().catch(() => { /* se reintenta al montar */ }); };
      if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(run, { timeout: 1200 });
      else setTimeout(run, 200); // Safari < 17 no tiene requestIdleCallback
    }
    setPhase(ss.get(KEY.welcomed) === "1" ? "store" : "welcome");
  }, [preload]);

  const enter = useCallback((path?: string, withTour = false) => {
    ss.set(KEY.welcomed, "1");
    if (path) goTo(path);
    setTourNow(withTour);
    setPhase("store");
    window.scrollTo(0, 0);
  }, []);

  return (
    <>
      {inRouter && <NavBridge />}
      {phase === "gate" && <PinGate onUnlock={unlock} />}
      {phase === "welcome" && <Welcome guestName={guestName} productPath={productPath} onEnter={enter} />}
      {phase === "store" && (
        <>
          <Suspense fallback={<StoreLoading />}>{children}</Suspense>
          {tour && <Tour productPath={productPath} autoStart={tourNow} />}
        </>
      )}
    </>
  );
}

function StoreLoading() {
  return (
    <div className="vip vip-loading" role="status">
      <span className="vip-loading__dot" aria-hidden="true" />
      <span>Abriendo la tienda…</span>
    </div>
  );
}

export default VipGate;
