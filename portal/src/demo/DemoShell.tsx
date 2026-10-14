/* DEMOSTRACION ESTATICA: marco alrededor de la vitrina real y de las pantallas de propuestas.
   - Franja fija de 40 px: «Vista previa» con el texto de cada zona (tienda, propuestas, maquetas, Detrás de escena,
     rentabilidad), el interruptor «Ver propuestas» con sus chips (capa sobre la tienda real) y «Lo que viene».
   - Capa de propuestas (marcadores + fichas), control del pedido de ejemplo y parche del aviso de correo.
   - Aviso de demostracion cuando algo "escribiria" (lo dispara mockApi con DEMO_EVENT).
   - WhatsApp y enlaces externos NO se abren: se muestra el aviso (nunca se manda nada al numero real).
   - Anclas internas (#comprar, #mi-kit, #contenido) funcionan con HashRouter: se desplaza sin tocar la ruta.
   - Guardia de <head>: noindex,nofollow siempre, sin canonica ni JSON-LD (la vitrina en modo dominio los pondria).
   - Pie discreto con el credito de diseno y desarrollo y los enlaces «Más allá de la vitrina». */
import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { DEMO_EVENT, DEMO_TEXT, notifyDemo, type DemoNotice } from "./mockApi";
import { CLAVES, ETIQUETAS, type Etiqueta } from "./propuestas/registro";
import { almacen } from "./propuestas/_comun/pedidos";
import CapaPropuestas, { ControlesCapa } from "./propuestas/_capa/CapaPropuestas";
import { esRutaDeTienda } from "./propuestas/_capa/rutas";
import { getCapa, useCapa } from "./propuestas/_capa/estado";
import { instalarParcheCorreo } from "./propuestas/_capa/parcheCorreo";

const ControlDemo = lazy(() => import("./propuestas/_capa/ControlDemo"));

const SIN_ETIQUETAS: Etiqueta[] = [];
const ALLOWED_HOSTS = ["jcanalytic.com", "www.jcanalytic.com", "crimsoncr.com", "www.crimsoncr.com"];

function useHeadGuard() {
  useEffect(() => {
    const fix = () => {
      let m = document.head.querySelector<HTMLMetaElement>('meta[name="robots"]');
      if (!m) { m = document.createElement("meta"); m.name = "robots"; document.head.appendChild(m); }
      if (m.content !== "noindex,nofollow") m.content = "noindex,nofollow";
      document.head.querySelectorAll('link[rel="canonical"], #sh-ld, meta[property="og:url"]').forEach((el) => el.remove());
    };
    fix();
    const mo = new MutationObserver(fix);
    mo.observe(document.head, { childList: true, subtree: true, attributes: true, attributeFilter: ["content"] });
    return () => mo.disconnect();
  }, []);
}

function useLinkGuard() {
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (e.defaultPrevented) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a) return;
      const href = a.getAttribute("href") || "";
      // ancla dentro de la pagina: con HashRouter el "#" es la ruta, asi que se desplaza a mano
      if (href.startsWith("#") && !href.startsWith("#/")) {
        e.preventDefault();
        const el = document.getElementById(decodeURIComponent(href.slice(1)));
        if (el) {
          el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
          if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
          el.focus({ preventScroll: true });
        }
        return;
      }
      let url: URL;
      try { url = new URL(a.href, location.href); } catch { return; }
      if (!/^https?:$/.test(url.protocol)) return;
      const wa = /(^|\.)wa\.me$|(^|\.)whatsapp\.com$/.test(url.hostname);
      if (wa) {
        e.preventDefault();
        notifyDemo({ title: "WhatsApp", text: `En la tienda real este botón abre WhatsApp con el mensaje listo para Crimson. ${DEMO_TEXT} Desde la vista previa no se envía nada.` });
        return;
      }
      if (url.origin !== location.origin && !ALLOWED_HOSTS.includes(url.hostname)) {
        e.preventDefault();
        notifyDemo({ title: "Enlace externo", text: `En la tienda real este enlace abre ${url.hostname}. En la vista previa los enlaces externos están desactivados.` });
      }
    };
    document.addEventListener("click", onClick, true);
    document.addEventListener("auxclick", onClick, true);
    return () => { document.removeEventListener("click", onClick, true); document.removeEventListener("auxclick", onClick, true); };
  }, []);
}

function Notice() {
  const [n, setN] = useState<DemoNotice | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const on = (e: Event) => setN((e as CustomEvent<DemoNotice>).detail);
    window.addEventListener(DEMO_EVENT, on);
    return () => window.removeEventListener(DEMO_EVENT, on);
  }, []);
  useEffect(() => {
    if (!n) return;
    const prev = document.activeElement as HTMLElement | null;
    btn.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); setN(null); } };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [n]);
  if (!n) return null;
  return (
    <div className="demo-notice" role="dialog" aria-modal="true" aria-labelledby="demo-notice-t" onClick={() => setN(null)}>
      <div className="demo-notice__card" onClick={(e) => e.stopPropagation()}>
        <span className="demo-notice__eyebrow">Vista previa · demostración</span>
        <h2 id="demo-notice-t">{n.title}</h2>
        <p>{n.text}</p>
        <button ref={btn} type="button" className="demo-notice__btn" onClick={() => setN(null)}>Entendido</button>
      </div>
    </div>
  );
}

/** Texto de la franja segun la zona. */
function textoFranja(pathname: string): string {
  if (pathname === "/propuestas") return "propuestas: nada de esto está incluido aún";
  if (pathname.startsWith("/propuestas/")) return "maqueta de propuesta, no incluida aún";
  if (pathname.startsWith("/detras-de-escena")) return "pantallas que ya existen, con datos de ejemplo";
  if (pathname.startsWith("/rentabilidad")) return "modelo con supuestos de ejemplo";
  return "demostración sin pedidos reales";
}

export default function DemoShell({ fx, etiquetasExtra = SIN_ETIQUETAS, children }: { fx: number; etiquetasExtra?: Etiqueta[]; children: ReactNode }) {
  useHeadGuard();
  useLinkGuard();
  const loc = useLocation();
  const etiquetas = useMemo(() => [...ETIQUETAS, ...etiquetasExtra], [etiquetasExtra]);
  const tienda = esRutaDeTienda(loc.pathname);
  const capa = useCapa();
  const conChips = capa.activa && (tienda || loc.pathname.startsWith("/detras-de-escena"));
  const sp = new URLSearchParams(loc.search);
  const conControl = sp.has("pedido") || sp.get("v") === "mi-pedido";
  const tituloPedido = tienda && conControl;

  useEffect(() => {
    document.documentElement.classList.add("demo-on");
    const quitar = instalarParcheCorreo();
    return () => { document.documentElement.classList.remove("demo-on"); quitar(); };
  }, []);
  // «← Tienda» de las maquetas vuelve a la ultima pantalla de tienda visitada
  useEffect(() => { if (tienda) almacen.sesion.set(CLAVES.ultimaRuta, `${loc.pathname}${loc.search}`); }, [tienda, loc.pathname, loc.search]);

  // De una maqueta, Detrás de escena o ¿Es rentable? a la tienda: la tienda arranca arriba (si no, se abria a media
  // pagina, p. ej. la portada con el titular recien cambiado en Mi Tienda). «Ver dónde va» (foco pendiente) centra solo.
  const rutaPrevia = useRef(loc.pathname);
  useLayoutEffect(() => {
    const antes = rutaPrevia.current;
    rutaPrevia.current = loc.pathname;
    if (antes !== loc.pathname && !esRutaDeTienda(antes) && esRutaDeTienda(loc.pathname) && !getCapa().foco) window.scrollTo(0, 0);
  }, [loc.pathname]);

  // «Mi pedido» (OrderStatus no pone titulo propio): la pestana no hereda el de la pantalla anterior
  useEffect(() => {
    if (!tituloPedido) return;
    const quiero = "Mi pedido · Tienda Crimson (vista previa)";
    const poner = () => { if (document.title !== quiero) document.title = quiero; };
    poner();
    const mo = new MutationObserver(poner);
    mo.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => mo.disconnect();
  }, [tituloPedido]);

  return (
    <>
      <div className="demo-ribbon" data-zona={tienda ? "tienda" : "propuestas"} data-chips={conChips ? "" : undefined}>
        <span className="demo-ribbon__txt" role="note"><b>Vista previa</b><span className="demo-ribbon__largo"><span aria-hidden="true"> · </span>{textoFranja(loc.pathname)}</span></span>
        <span className="demo-ribbon__ctl"><ControlesCapa /><span id="demo-guia" className="demo-ribbon__guia" /></span>
      </div>
      {children}
      <footer className="demo-credit">
        <span>Vista previa no funcional: catálogo público de referencia, precios en colones con IVA a un tipo de cambio fijo de ₡{fx} por dólar. No se procesan pedidos, pagos ni solicitudes.</span>
        <span className="demo-credit__mas">Más allá de la vitrina: <Link to="/detras-de-escena">Detrás de escena</Link><span aria-hidden="true"> · </span><Link to="/propuestas">Lo que viene</Link><span aria-hidden="true"> · </span><Link to="/rentabilidad">¿Es rentable?</Link></span>
        <span>Diseño y desarrollo: <a href="https://jcanalytic.com" target="_blank" rel="noopener">JC Analytics</a></span>
      </footer>
      <CapaPropuestas etiquetas={etiquetas} />
      {tienda && conControl && <Suspense fallback={null}><ControlDemo /></Suspense>}
      <Notice />
    </>
  );
}
