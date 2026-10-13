/* DEMOSTRACION ESTATICA: marco alrededor de la vitrina real.
   - Franja fija "VISTA PREVIA" (las piezas fijas/pegajosas de la tienda se corren su alto: demo.css).
   - Aviso de demostracion cuando algo "escribiria" (lo dispara mockApi con DEMO_EVENT).
   - WhatsApp y enlaces externos de compra NO se abren: se muestra el aviso (nunca se manda nada al numero real).
   - Anclas internas (#comprar, #mi-kit, #contenido) funcionan con HashRouter: se desplaza sin tocar la ruta.
   - Guardia de <head>: noindex,nofollow siempre, sin canonica ni JSON-LD (la vitrina en modo dominio los pondria).
   - Pie discreto con el credito de diseno y desarrollo. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { DEMO_EVENT, DEMO_TEXT, notifyDemo, type DemoNotice } from "./mockApi";

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
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setN(null);
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

export default function DemoShell({ fx, children }: { fx: number; children: ReactNode }) {
  useHeadGuard();
  useLinkGuard();
  useEffect(() => {
    document.documentElement.classList.add("demo-on");
    return () => document.documentElement.classList.remove("demo-on");
  }, []);
  return (
    <>
      <div className="demo-ribbon" role="note"><b>Vista previa</b><span aria-hidden="true"> · </span><span>demostración sin pedidos reales</span></div>
      {children}
      <footer className="demo-credit">
        <span>Vista previa no funcional: catálogo público de referencia, precios en colones con IVA a un tipo de cambio fijo de ₡{fx} por dólar. No se procesan pedidos, pagos ni solicitudes.</span>
        <span>Diseño y desarrollo: <a href="https://jcanalytic.com" target="_blank" rel="noopener">JC Analytics</a></span>
      </footer>
      <Notice />
    </>
  );
}
