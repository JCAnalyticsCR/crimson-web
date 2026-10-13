/* Navegacion de la capa VIP sin acoplarse al router de la tienda.

   goTo(path) recibe rutas de la vitrina ("/catalogo", "/buscar?q=ds-2cd", "/?v=pagar") y:
   1. si VipGate quedo DENTRO de un <BrowserRouter>/<HashRouter>, usa su navigate() (registrado por <NavBridge>);
   2. si no hay router alrededor (la tienda trae el suyo adentro), escribe la URL a mano y avisa con popstate,
      que es lo que escuchan BrowserRouter y HashRouter de react-router 7. Asi funciona igual antes de montar la
      tienda (el router lee la URL al nacer) y despues (el popstate lo despierta). */

type Nav = (to: string) => void;
let routerNavigate: Nav | null = null;
export const registerNavigate = (fn: Nav | null) => { routerNavigate = fn; };

export type RouterMode = "browser" | "hash";
let config: { mode?: RouterMode; base: string } = { base: "" };

/** base: el basename del router (p. ej. "/demo-tienda"). Por defecto, el base de Vite. */
export function configureNav(opts: { mode?: RouterMode; basename?: string }) {
  const raw = opts.basename ?? import.meta.env.BASE_URL ?? "/";
  config = { mode: opts.mode, base: raw === "/" ? "" : raw.replace(/\/+$/, "") };
}

const isHash = () => config.mode === "hash" || (config.mode === undefined && location.hash.startsWith("#/"));

export function goTo(path: string) {
  const to = path.startsWith("/") ? path : `/${path}`;
  if (routerNavigate) { routerNavigate(to); return; }
  if (isHash()) {
    location.hash = to; // dispara hashchange + popstate: HashRouter se entera solo
    return;
  }
  // Misma forma de estado que deja react-router (usr/key/idx) para no confundir su contador de historial.
  const prev = (history.state as { idx?: number } | null)?.idx;
  history.pushState({ usr: null, key: Math.random().toString(36).slice(2, 10), idx: typeof prev === "number" ? prev + 1 : 0 }, "", `${config.base}${to}`);
  window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
}

/** Convierte un href que pinto la tienda ("/demo-tienda/p/x-12" o "#/p/x-12") en ruta del router ("/p/x-12"). */
export function toRouterPath(href: string): string | null {
  try {
    const u = new URL(href, location.href);
    if (u.hash.startsWith("#/")) return u.hash.slice(1);
    let p = u.pathname;
    if (config.base && p.startsWith(config.base)) p = p.slice(config.base.length) || "/";
    return `${p}${u.search}`;
  } catch {
    return null;
  }
}
