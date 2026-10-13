/* Capa «Ver propuestas» sobre la tienda REAL (y sobre «Detrás de escena»), sin tocar su codigo ni su diseno:
   - busca el objetivo de cada etiqueta por selector (el primero visible, fuera de .vip y .cap);
   - pinta marcadores fijos en un portal (z 50: quedan bajo el velo del carrito y de los modales de la tienda);
   - contorno al objetivo con una clase (nunca cambia position ni medidas de la tienda);
   - la franja (ControlesCapa) muestra el interruptor y, encendida, un chip por marca: la via de teclado.
   Herramienta, no coreografia: los marcadores aparecen en 200 ms y solo laten al llegar por «Ver dónde va».
   Rendimiento: todas las lecturas (getBoundingClientRect) antes que las escrituras, dentro de un rAF. */
import { useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation } from "react-router-dom";
import { porId, type Etiqueta } from "../registro";
import { coincideAlguna, esRutaDeTienda } from "./rutas";
import { abrirFicha, cerrarFicha, cerrarPista, consumirFoco, setActiva, setExistentes, setMarcas, setPulso, useCapa, getCapa, type Marca } from "./estado";
import FichaEtiqueta from "./FichaEtiqueta";
import "./capa.css";

const calm = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const visible = (el: Element) => el.getClientRects().length > 0 && !el.closest(".vip, .cap");

function buscar(selectores: string[]): HTMLElement | null {
  for (const s of selectores) {
    let lista: NodeListOf<Element>;
    try { lista = document.querySelectorAll(s); } catch { continue; }
    for (const el of Array.from(lista)) if (visible(el)) return el as HTMLElement;
  }
  return null;
}

const ids = new WeakMap<Element, number>();
let sig = 0;
const idDe = (el: Element) => { let n = ids.get(el); if (!n) { n = ++sig; ids.set(el, n); } return n; };
const firma = (m: Marca[]) => m.map((x) => `${x.e.id}@${idDe(x.el)}`).join("|");

/** Borde inferior de lo que tapa arriba: la franja, la cabecera de la tienda y la barra de propuestas. */
export function topeSuperior(): number {
  const demo = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--demo-h")) || 0;
  let tope = demo;
  for (const sel of [".sh-head", "[data-pp-nav]"]) {
    const el = document.querySelector(sel);
    if (el) { const r = el.getBoundingClientRect(); if (r.height && r.bottom > tope && r.top <= demo + 1) tope = r.bottom; }
  }
  return tope;
}

export const etiquetaDeMarca = (m: Marca) => (m.tipo === "propuesta" ? `Propuesta ${m.info.id}: ${m.info.titulo}. Ver qué cambiaría aquí` : `Ya construido: ${m.info.titulo}. Ver qué hace aquí`);

export default function CapaPropuestas({ etiquetas }: { etiquetas: Etiqueta[] }) {
  const loc = useLocation();
  const st = useCapa();
  const botones = useRef(new Map<string, HTMLButtonElement>());
  const ficha = useRef<HTMLDivElement | null>(null);
  const posicion = useRef(new Map<string, { x: number; y: number; w: number; h: number; ok: boolean }>());

  // ---------- candidatas por ruta
  const candidatas = useMemo(() => etiquetas.filter((e) => {
    if (!coincideAlguna(e.rutas, loc.pathname, loc.search)) return false;
    if (!porId(e.propuesta)) { if (import.meta.env.DEV) console.warn("[demo] etiqueta con propuesta desconocida:", e.id); return false; }
    return st.existentes || !e.propuesta.startsWith("H");
  }), [etiquetas, loc.pathname, loc.search, st.existentes]);

  // ---------- resolver objetivos (MutationObserver agrupado en rAF; 6 s tras cada ruta, o mientras este encendida)
  useEffect(() => {
    let raf = 0;
    let tm = 0;
    const resolver = () => {
      cancelAnimationFrame(raf); window.clearTimeout(tm); raf = 0;
      const marcas: Marca[] = [];
      for (const e of candidatas) {
        const el = buscar(e.selector);
        const info = porId(e.propuesta);
        if (el && info) marcas.push({ e, el, tipo: e.propuesta.startsWith("H") ? "existente" : "propuesta", info });
      }
      if (firma(marcas) !== firma(getCapa().marcas)) setMarcas(marcas);
    };
    const pedir = () => { if (!raf) { raf = requestAnimationFrame(resolver); tm = window.setTimeout(resolver, 120); } };
    const mo = new MutationObserver(pedir);
    mo.observe(document.body, { childList: true, subtree: true });
    resolver();
    const respaldo = window.setTimeout(pedir, 120);
    const fin = st.activa ? 0 : window.setTimeout(() => mo.disconnect(), 6000);
    return () => { mo.disconnect(); cancelAnimationFrame(raf); window.clearTimeout(tm); window.clearTimeout(respaldo); window.clearTimeout(fin); };
  }, [candidatas, st.activa]);

  // ---------- cambiar de ruta cierra la ficha
  useEffect(() => { if (getCapa().abierta) cerrarFicha(); }, [loc.pathname, loc.search]);

  // ---------- contorno de los objetivos
  useEffect(() => {
    if (!st.activa) return;
    const puestos = st.marcas.map((m) => { const c = m.tipo === "propuesta" ? "cap-objetivo" : "cap-objetivo--ya"; m.el.classList.add(c); return [m.el, c] as const; });
    return () => puestos.forEach(([el, c]) => el.classList.remove(c));
  }, [st.activa, st.marcas]);

  // ---------- «Ver dónde va»: centrar, latir y abrir en cuanto aparece
  useEffect(() => {
    if (!st.activa || !st.foco) return;
    const m = st.marcas.find((x) => x.e.id === st.foco);
    if (!m) return;
    consumirFoco();
    m.el.scrollIntoView({ block: "center", behavior: calm() ? "auto" : "smooth" });
    if (!calm()) { setPulso(m.e.id); window.setTimeout(() => setPulso(null), 3 * 1400); }
    window.setTimeout(() => abrirFicha(m.e.id), calm() ? 0 : 320);
  }, [st.activa, st.foco, st.marcas]);

  // ---------- colocar marcadores (y la ficha de escritorio) — lecturas primero, escrituras despues
  useEffect(() => {
    if (!st.activa) return;
    let raf = 0;
    let tm = 0;
    const colocar = () => {
      cancelAnimationFrame(raf); window.clearTimeout(tm); raf = 0;
      const W = innerWidth, H = innerHeight;
      const tope = topeSuperior();
      const demo = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--demo-h")) || 0;
      const lect = st.marcas.map((m) => {
        const b = botones.current.get(m.e.id);
        return { m, b, r: m.el.getBoundingClientRect(), w: b?.offsetWidth || 40, h: b?.offsetHeight || 28, enCabecera: !!m.el.closest(".sh-head, [data-pp-nav]") };
      });
      const fichaRect = ficha.current?.getBoundingClientRect() || null;
      // calculo
      const pos = lect.map(({ m, r, w, h, enCabecera }) => {
        const lim = enCabecera ? demo : tope;
        const visibleAlto = Math.min(r.bottom, H) - Math.max(r.top, lim);
        const lado = m.e.lado || "arriba-der";
        let x = lado.endsWith("der") ? r.right - w - 12 : r.left + 12;
        let y = lado.startsWith("arriba") ? r.top - h / 2 : r.bottom - h / 2;
        x = Math.max(8, Math.min(W - w - 8, x));
        const minY = Math.max(lim + 4, Math.min(r.top, H));
        y = Math.max(minY, Math.min(H - h - 8, y));
        // se oculta si sale de la ventana o si lo que queda visible bajo la cabecera mide menos de 24 px
        // (un objetivo mas bajito que 24 px cuenta entero: si se ve completo, lleva su marca)
        const ok = r.width > 0 && r.bottom > lim && r.top < H && visibleAlto >= Math.min(24, r.height - 0.5);
        return { id: m.e.id, x, y, w, h, ok };
      });
      // choques: si dos quedan a menos de 32 px, el siguiente baja 34 px
      const ord = pos.filter((p) => p.ok).sort((a, b) => a.y - b.y || a.x - b.x);
      for (let i = 1; i < ord.length; i++) {
        for (let j = 0; j < i; j++) {
          const a = ord[j], b = ord[i];
          const solapa = b.x < a.x + a.w + 8 && a.x < b.x + b.w + 8;
          if (solapa && Math.abs(b.y - a.y) < 32) b.y = a.y + 34;
        }
      }
      // escrituras
      for (const p of pos) {
        posicion.current.set(p.id, p);
        const b = botones.current.get(p.id);
        if (!b) continue;
        b.style.transform = `translate3d(${Math.round(p.x)}px, ${Math.round(p.y)}px, 0)`;
        b.dataset.oculta = p.ok ? "" : "1";
      }
      const ab = getCapa().abierta;
      const f = ficha.current;
      if (f && ab && fichaRect && f.dataset.modo === "flota") {
        const p = posicion.current.get(ab);
        if (p) {
          const fw = fichaRect.width, fh = fichaRect.height;
          const left = Math.max(8, Math.min(W - fw - 8, p.x + p.w - fw));
          let top = p.y + p.h + 8;
          if (top + fh > H - 8) top = Math.max(tope + 8, p.y - fh - 8);
          f.style.transform = `translate3d(${Math.round(left)}px, ${Math.round(top)}px, 0)`;
        }
      }
    };
    const pedir = () => { if (!raf) { raf = requestAnimationFrame(colocar); tm = window.setTimeout(colocar, 120); } };
    colocar();
    const ro = new ResizeObserver(pedir);
    ro.observe(document.documentElement);
    window.addEventListener("scroll", pedir, { passive: true });
    window.addEventListener("resize", pedir, { passive: true });
    const mo = new MutationObserver(pedir);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "open", "hidden"] });
    document.fonts?.ready.then(pedir).catch(() => {});
    const t = window.setTimeout(pedir, 120);
    return () => {
      cancelAnimationFrame(raf); window.clearTimeout(tm); window.clearTimeout(t);
      ro.disconnect(); mo.disconnect();
      window.removeEventListener("scroll", pedir); window.removeEventListener("resize", pedir);
    };
  }, [st.activa, st.marcas, st.abierta]);

  const abierta = st.activa && st.abierta ? st.marcas.find((m) => m.e.id === st.abierta) || null : null;
  const abiertaEtiqueta = st.activa && st.abierta ? abierta?.e || etiquetas.find((e) => e.id === st.abierta) || null : null;

  return (
    <>
      {st.activa && createPortal(
        <div className="cap cap-marcas">
          {st.marcas.map((m) => (
            <button key={m.e.id} type="button" className="cap-marca" data-tipo={m.tipo} data-pulso={st.pulso === m.e.id ? "" : undefined}
              ref={(b) => { if (b) botones.current.set(m.e.id, b); else botones.current.delete(m.e.id); }}
              aria-label={etiquetaDeMarca(m)} aria-expanded={st.abierta === m.e.id} aria-controls={st.abierta === m.e.id ? "cap-ficha" : undefined}
              data-cap-abre={m.e.id}
              onClick={() => (st.abierta === m.e.id ? cerrarFicha() : abrirFicha(m.e.id))}>
              {m.tipo === "propuesta"
                ? <><b>{m.info.id}</b><span className="cap-marca__corto" aria-hidden="true">{" · "}{"corto" in m.info ? m.info.corto : ""}</span></>
                : <><span aria-hidden="true">✓</span>{" "}Ya</>}
            </button>
          ))}
        </div>,
        document.body,
      )}
      {abiertaEtiqueta && <FichaEtiqueta etiqueta={abiertaEtiqueta} refFicha={ficha} />}
      {st.activa && st.pista && createPortal(
        <div className="cap cap-pista" role="note">
          <p>Las etiquetas <b className="cap-pista__c">carmesí</b> marcan dónde iría cada propuesta; las <b className="cap-pista__v">verdes</b>, lo que ya funciona. Tocá una para ver qué cambiaría ahí.</p>
          <button type="button" className="cap-btn cap-btn--claro" onClick={cerrarPista}>Entendido</button>
        </div>,
        document.body,
      )}
    </>
  );
}

// ---------------------------------------------------------------- controles de la franja
export function ControlesCapa() {
  const loc = useLocation();
  const st = useCapa();
  const conInterruptor = esRutaDeTienda(loc.pathname) || loc.pathname.startsWith("/detras-de-escena");
  const nProp = st.marcas.filter((m) => m.tipo === "propuesta").length;
  const irA = (m: Marca) => {
    m.el.scrollIntoView({ block: "center", behavior: calm() ? "auto" : "smooth" });
    abrirFicha(m.e.id);
  };
  return (
    <>
      {conInterruptor && st.activa && (
        <nav className="cap cap-chips" aria-label="Propuestas en esta pantalla">
          {st.marcas.length === 0 ? (
            <span className="cap-chips__vacio">Aquí no hay propuestas · <Link to="/propuestas">Ver todas</Link></span>
          ) : (
            <ul>
              {st.marcas.map((m) => (
                <li key={m.e.id}>
                  <button type="button" className="cap-chip" data-tipo={m.tipo} data-cap-abre={m.e.id} aria-expanded={st.abierta === m.e.id} onClick={() => irA(m)}
                    aria-label={etiquetaDeMarca(m)}>
                    {m.tipo === "propuesta" ? <><b>{m.info.id}</b><span className="cap-chip__largo">{" · "}{"corto" in m.info ? m.info.corto : ""}</span></> : <>✓ {m.info.id}</>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <button type="button" className="cap-chip cap-chip--ya" aria-pressed={st.existentes} onClick={() => setExistentes(!st.existentes)}>
            <span className="cap-chip__largo">Ver también lo que ya existe</span><span className="cap-chip__corto" aria-hidden="true">Lo que ya existe</span>
          </button>
        </nav>
      )}
      {conInterruptor && (
        <button type="button" role="switch" aria-checked={st.activa} className="cap-switch" onClick={() => setActiva(!st.activa)}>
          {st.activa ? (
            <><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>Ocultar<span className="cap-sr"> propuestas</span></>
          ) : (
            <><i className="cap-switch__pista" aria-hidden="true" /><span className="cap-switch__l">Ver propuestas</span><span className="cap-switch__c" aria-hidden="true">Propuestas</span>
              {nProp > 0 && <b className="cap-switch__n"><span className="cap-sr">, </span>{nProp}<span className="cap-sr"> en esta pantalla</span></b>}</>
          )}
        </button>
      )}
      {loc.pathname !== "/propuestas" && <Link className="cap-lqv" to="/propuestas">Lo que viene</Link>}
    </>
  );
}
