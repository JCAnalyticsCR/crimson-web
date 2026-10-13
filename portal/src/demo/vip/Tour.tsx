/* Recorrido guiado opcional, encima de la tienda ya montada.
   - Boton flotante "Guia" (se puede ocultar con su x) que abre un panel de 9 pasos: de la vitrina a los numeros
     (tienda real, «Mi pedido» de ejemplo, Detrás de escena, Lo que viene y ¿Es rentable?).
   - Un paso puede preparar algo ANTES de navegar (`antes`): el de pago siembra 2 camaras de ejemplo si el carrito
     esta vacio, para que el pago se vea completo (y lo avisa con una nota).
   - Cada paso navega por URL con goTo() (sirve con BrowserRouter o HashRouter) y senala un elemento real de la
     vitrina con un anillo (clase .vip-spot), sin tapar nada: el anillo es outline, no una capa encima.
   - Movil: el panel se acopla ARRIBA, justo debajo de la cabecera de la tienda, porque abajo viven los botones
     principales (barra "Agregar al carrito" de la ficha, acciones del checkout, bandeja del comparador).
   - Pantalla de trabajo: el panel no se anima; solo se mueve lo que el usuario toca. */
import { useCallback, useEffect, useRef, useState } from "react";
import { EVENTOS } from "../propuestas/registro";
import { sembrarCarritoSiVacio } from "../propuestas/_capa/carrito";
import { goTo, toRouterPath } from "./nav";
import { KEY, ss } from "./pin";

type Step = {
  id: string;
  title: string;
  body: string;
  path: string | (() => string);
  target: string[];
  /** Texto de respaldo si el elemento no aparece (p. ej. carrito vacio en el paso de pago). */
  missing?: string;
  /** Preparacion antes de navegar; devuelve una nota informativa o null. */
  antes?: () => Promise<string | null>;
};

export const tourSteps = (productPath: string): Step[] => [
  {
    id: "portada", title: "La portada de Crimson",
    body: "Vitrina con la marca de Crimson: categorías, kits listos y lo más pedido, con precios y existencias del inventario real.",
    path: "/", target: ["#vt-title", ".vt-hero", ".sh-hero", "main h1"],
  },
  {
    id: "buscar", title: "Catálogo con búsqueda",
    body: "Buscamos “DS-2CD”: resultados al instante, con filtros y disponibilidad en cada producto.",
    path: "/buscar?q=ds-2cd", target: [".sh-search", "input[type=search]"],
  },
  {
    id: "ficha", title: "La ficha de producto",
    body: "Fotos, especificaciones y existencias. Y el acceso que vende servicio: “¿Lo instalamos nosotros?” cotiza la instalación con este producto. Tocá «Ver propuestas» en la franja de arriba para ver qué más se podría sumar aquí.",
    path: () => firstProductPath() || productPath, target: [".vt-install"],
    missing: "Abrí cualquier producto: “¿Lo instalamos nosotros?” está junto al botón de compra.",
  },
  {
    id: "kit", title: "Arma tu sistema",
    body: "Cuatro preguntas y sale un kit con productos reales, con precio y disponibilidad, listo para el carrito.",
    path: "/arma-tu-sistema", target: [".vt-q", ".vt-kitb", "main h1"],
  },
  {
    id: "pago", title: "Carrito y pago",
    body: "SINPE Móvil con comprobante, tarjeta y factura electrónica automática. Es una vista previa: nada se cobra.",
    path: "/?v=pagar", target: [".ck-steps"], antes: sembrarCarritoSiVacio,
    missing: "Tu navegador no deja guardar el carrito en esta vista: agregá un producto y volvé a este paso.",
  },
  {
    id: "pedido", title: "Mi pedido",
    body: "Así sigue el cliente su pedido con el enlace del correo. Con el control «Pedido de ejemplo» recorrés cada estado, de «Pendiente de pago» a «Entregado».",
    path: "/?pedido=ejemplo-enviado", target: [".ck-track", "#contenido"],
  },
  {
    id: "detras", title: "Detrás de escena",
    body: "Así llega cada pedido a Crimson: comprobante privado, confirmar pago, factura y despacho. Ya existe; aquí con datos de ejemplo. Si hiciste un pedido de prueba, aparece de primero.",
    path: "/detras-de-escena/pedidos", target: ["#ges-pedidos", "main h1"],
  },
  {
    id: "viene", title: "Lo que viene",
    body: "Diecisiete propuestas en cinco etapas, ninguna incluida todavía. Seis tienen maqueta para tocar; marcá «Me interesa» en las que querás.",
    path: "/propuestas", target: ["#lqv-etapas", "main h1"],
  },
  {
    id: "numeros", title: "¿Es rentable?",
    body: "Un modelo a la vista: cada número es un ejemplo que podés cambiar por el tuyo. Arranca en el escenario conservador.",
    path: "/rentabilidad", target: ["#rnt-resultado", "main h1"],
  },
];

/** Primer enlace a una ficha que haya pintado la tienda (resultados de busqueda, portada…). */
function firstProductPath(): string | null {
  const a = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href*="/p/"]')).find((el) => !el.closest(".vip, .cap") && el.getClientRects().length > 0);
  return a ? toRouterPath(a.href) : null;
}

const currentPath = () => toRouterPath(location.href);
const visible = (el: Element) => el.getClientRects().length > 0 && !el.closest(".vip, .cap");
const findTarget = (sel: string[]) => {
  for (const s of sel) {
    const el = Array.from(document.querySelectorAll(s)).find(visible);
    if (el) return el as HTMLElement;
  }
  return null;
};
const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

type TourState = { step: number; open: boolean };
const load = (): TourState => {
  try { const j = JSON.parse(ss.get(KEY.tour) || "null"); if (j && typeof j.step === "number") return j; } catch { /* sin estado */ }
  return { step: 0, open: false };
};

export default function Tour({ productPath, autoStart }: { productPath: string; autoStart: boolean }) {
  const steps = useRef(tourSteps(productPath)).current;
  const [st, setSt] = useState<TourState>(() => (autoStart ? { step: 0, open: true } : load()));
  const [off, setOff] = useState(() => ss.get(KEY.guideOff) === "1");
  const [miss, setMiss] = useState<string | null>(null);
  const [nota, setNota] = useState<string | null>(null);
  const panel = useRef<HTMLElement>(null);
  const spotRef = useRef<HTMLElement | null>(null);
  const placeRef = useRef<() => void>(() => {});
  const step = steps[Math.min(st.step, steps.length - 1)];

  useEffect(() => { ss.set(KEY.tour, JSON.stringify(st)); }, [st]);

  // La capa de propuestas (movil) pide cerrar la guia para no taparse entre si.
  useEffect(() => {
    const on = () => setSt((s) => (s.open ? { ...s, open: false } : s));
    window.addEventListener(EVENTOS.tourCerrar, on);
    return () => window.removeEventListener(EVENTOS.tourCerrar, on);
  }, []);

  // Preparar (si el paso lo pide) + navegar + senalar el elemento del paso.
  useEffect(() => {
    if (!st.open) return;
    setMiss(null);
    setNota(null);
    let cancelado = false;
    let spot: HTMLElement | null = null;
    let done = false;
    let scrollT = 0;
    let t0 = 0;
    let giveUp = 0;
    let mo: MutationObserver | null = null;
    const mark = (el: HTMLElement) => {
      done = true;
      spot = el;
      spotRef.current = el;
      el.classList.add("vip-spot");
      placeRef.current();
      // la tienda hace scrollTo(0) al cambiar de ruta; el nuestro va despues (timeout y no rAF: corre aunque la pestana no pinte)
      scrollT = window.setTimeout(() => el.scrollIntoView({ block: "center", behavior: calm() ? "auto" : "smooth" }), 40);
    };
    // El objetivo principal gana; los de respaldo ("main h1") solo despues de 700 ms, porque al cambiar de ruta la
    // pantalla anterior sigue en el DOM unos instantes y su h1 se marcaria por error. Si el elemento senalado
    // desaparece (la ruta termino de cambiar), se vuelve a buscar.
    let inicio = 0;
    let respaldo = 0;
    const tryFind = () => {
      if (cancelado) return;
      if (done && spot && (spot as HTMLElement).isConnected) return;
      if (done) { (spot as HTMLElement | null)?.classList.remove("vip-spot"); done = false; spot = null; }
      const principal = findTarget(step.target.slice(0, 1));
      const el = principal || (performance.now() - inicio > 700 ? findTarget(step.target.slice(1)) : null);
      if (el) { mark(el); return; }
      if (step.target.length > 1 && !respaldo) respaldo = window.setTimeout(() => { respaldo = 0; tryFind(); }, 760);
    };
    const arrancar = (n: string | null) => {
      if (cancelado) return;
      if (n) setNota(n);
      const path = typeof step.path === "function" ? step.path() : step.path;
      if (currentPath() !== path) goTo(path);
      inicio = performance.now();
      mo = new MutationObserver(tryFind);
      mo.observe(document.body, { childList: true, subtree: true });
      t0 = window.setTimeout(tryFind, 60); // tras el render de la ruta nueva
      giveUp = window.setTimeout(() => { if (!done && step.missing) setMiss(step.missing); }, 6000);
    };
    if (step.antes) step.antes().then(arrancar, () => arrancar(null));
    else arrancar(null);
    return () => {
      cancelado = true;
      mo?.disconnect();
      window.clearTimeout(t0);
      window.clearTimeout(giveUp);
      window.clearTimeout(scrollT);
      window.clearTimeout(respaldo);
      (spot as HTMLElement | null)?.classList.remove("vip-spot");
      spotRef.current = null;
    };
  }, [st.open, st.step]); // eslint-disable-line react-hooks/exhaustive-deps

  // Movil/tableta (<900px): el panel se acopla ARRIBA, bajo la cabecera de la tienda. Si asi taparia el elemento
  // senalado (p. ej. el titular de la portada, pegado a la cabecera), baja al pie, por encima de cualquier barra
  // fija de la tienda (compra, checkout, comparador, aviso). Medicion con rAF solo mientras el panel esta abierto.
  useEffect(() => {
    if (!st.open) return;
    let raf = 0;
    let tm = 0;
    const narrow = matchMedia("(max-width: 899px)");
    const place = () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(tm);
      raf = 0;
      const el = panel.current;
      if (!el) return;
      if (!narrow.matches) { el.removeAttribute("data-dock"); return; }
      // tope: lo mas bajo entre la franja de la demo, la cabecera de la tienda y la barra de propuestas
      let tope = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--demo-h")) || 0;
      for (const sel of [".sh-head", "[data-pp-nav]"]) {
        const b = document.querySelector(sel)?.getBoundingClientRect().bottom || 0;
        if (b > tope) tope = b;
      }
      const top = Math.max(0, Math.min(220, Math.round(tope)));
      el.style.setProperty("--vip-top", `${top}px`);
      const spot = spotRef.current?.getBoundingClientRect();
      const panelBottom = top + 8 + el.offsetHeight + 8;
      const covers = !!spot && spot.height > 0 && spot.top < panelBottom && spot.bottom > top;
      if (!covers) { el.setAttribute("data-dock", "top"); return; }
      let lift = 0;
      for (const bar of Array.from(document.querySelectorAll(".vt-stickybuy, .ck-actions, .vt-cmptray, .vt-consent, [data-pp-barra], .cap-control"))) {
        const r = bar.getBoundingClientRect();
        if (r.height && r.bottom >= innerHeight - 4 && r.top < innerHeight) lift = Math.max(lift, Math.round(innerHeight - r.top));
      }
      el.style.setProperty("--vip-bottom", `${lift}px`);
      el.setAttribute("data-dock", "bottom");
    };
    // rAF para ir al ritmo del scroll; el timeout es red de seguridad si el navegador no pinta (pestana oculta)
    const req = () => { if (!raf) { raf = requestAnimationFrame(place); tm = window.setTimeout(place, 120); } };
    placeRef.current = req;
    place();
    const ro = new ResizeObserver(req);
    const head = document.querySelector(".sh-head") || document.querySelector("[data-pp-nav]");
    if (head) ro.observe(head);
    if (panel.current) ro.observe(panel.current);
    window.addEventListener("scroll", req, { passive: true });
    window.addEventListener("resize", req);
    const late = window.setTimeout(req, 400); // la cabecera nace cuando carga la tienda
    return () => { placeRef.current = () => {}; cancelAnimationFrame(raf); window.clearTimeout(tm); ro.disconnect(); window.removeEventListener("scroll", req); window.removeEventListener("resize", req); window.clearTimeout(late); };
  }, [st.open, st.step]);

  const close = useCallback(() => setSt((s) => ({ ...s, open: false })), []);
  useEffect(() => {
    if (!st.open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [st.open, close]);

  if (!st.open) {
    if (off) return null;
    const inProgress = st.step > 0;
    return (
      <div className="vip vip-guide">
        <button type="button" className="vip-guide__open" onClick={() => setSt((s) => ({ ...s, open: true }))}
          aria-label={inProgress ? `Retomar la guía, paso ${st.step + 1} de ${steps.length}` : "Abrir la guía de la tienda"}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></svg>
          <span>Guía{inProgress ? <small> {st.step + 1}/{steps.length}</small> : null}</span>
        </button>
        <button type="button" className="vip-guide__x" aria-label="Ocultar el botón de guía" onClick={() => { ss.set(KEY.guideOff, "1"); setOff(true); }}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>
        </button>
      </div>
    );
  }

  const last = st.step >= steps.length - 1;
  return (
    <aside className="vip vip-tour" ref={panel} aria-label="Recorrido guiado">
      {/* Una grilla: en movil la navegacion sube a la fila del encabezado para que el panel ocupe ~120 px. */}
      <span className="vip-tour__n">
        <span className="vip-tour__long">Guía · paso {st.step + 1} de {steps.length}</span>
        <span className="vip-tour__short" aria-hidden="true">Guía {st.step + 1}/{steps.length}</span>
      </span>
      <span className="vip-tour__dots" aria-hidden="true">{steps.map((s, i) => <i key={s.id} data-on={i <= st.step ? "1" : undefined} />)}</span>
      <button type="button" className="vip-tour__x" onClick={close} aria-label="Cerrar la guía (podés retomarla después)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>
      </button>
      <div className="vip-tour__txt" aria-live="polite" aria-atomic="true">
        <b>{step.title}</b>
        <p>{step.body}</p>
        {nota && <p className="vip-tour__nota">{nota}</p>}
        {miss && <p className="vip-tour__miss">{miss}</p>}
      </div>
      <div className="vip-tour__nav">
        <button type="button" className="vip-btn vip-btn--ghost vip-btn--sm vip-tour__prev" disabled={st.step === 0} aria-label="Paso anterior"
          onClick={() => setSt((s) => ({ ...s, step: Math.max(0, s.step - 1) }))}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 12H5M11 6l-6 6 6 6" /></svg><span className="vip-tour__lbl">Anterior</span>
        </button>
        <button type="button" className="vip-btn vip-btn--crimson vip-btn--sm"
          onClick={() => setSt((s) => (last ? { step: 0, open: false } : { ...s, step: s.step + 1 }))}>
          {last ? "Terminar" : "Siguiente"}
        </button>
      </div>
    </aside>
  );
}
