/* Capa «Ver propuestas» sobre la tienda REAL (y sobre «Detrás de escena»), sin tocar su codigo ni su diseno:
   - busca el objetivo de cada etiqueta por selector (el primero visible, fuera de .vip y .cap);
   - pinta marcadores fijos en un portal (z 50: quedan bajo el velo del carrito y de los modales de la tienda), cada uno
     en un lugar libre AFUERA de su objetivo: nunca tapa lo que explica ni el texto o los botones de al lado;
   - contorno al objetivo con una clase (nunca cambia position ni medidas de la tienda);
   - la franja (ControlesCapa) muestra el interruptor y, encendida, un chip por marca: la via de teclado.
   Herramienta, no coreografia: los marcadores aparecen en 200 ms y solo laten al llegar por «Ver dónde va».
   Rendimiento: todas las lecturas (getBoundingClientRect) antes que las escrituras, dentro de un rAF. */
import { useEffect, useMemo, useRef, useState } from "react";
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

/** Barras fijas abajo (compra de la ficha, acciones del pago, comparador, barras de maqueta, control del pedido):
    un marcador nunca flota encima de ellas, aunque su objetivo quede detras. */
const BARRAS_ABAJO = ".vt-stickybuy, .ck-actions, .vt-cmptray, .vt-consent, [data-pp-barra], .cap-control";

type Caja = { left: number; right: number; top: number; bottom: number };
type Plan = { dx: number; dy: number; corto: boolean; tw: number; th: number };

/** Lo que nunca se tapa: controles, enlaces e imagenes. Un texto se detecta aparte, por donde esta escrito. */
const OBSTACULO = "a, button, input, select, textarea, img, svg, video, canvas, iframe, [role=button], [role=img]";
/** ¿Hay texto propio de `e` escrito en ese punto? (un parrafo ancho con texto corto deja libre su parte vacia) */
function textoEn(e: Element, px: number, py: number): boolean {
  for (const n of Array.from(e.childNodes)) {
    if (n.nodeType !== 3 || !n.textContent?.trim()) continue;
    const rg = document.createRange();
    rg.selectNodeContents(n);
    for (const r of Array.from(rg.getClientRects())) if (px >= r.left - 2 && px <= r.right + 2 && py >= r.top - 2 && py <= r.bottom + 2) return true;
  }
  return false;
}
/** ¿Ese lugar esta libre? Se prueba lo que de verdad se ve ahi (elementsFromPoint, sin contar la capa ni la guia) en una
    reticula de 3 filas y una columna cada ~18 px. Ocupa: un texto escrito ahi, o un control, enlace o imagen que no sea
    del objetivo (ni lo contenga). El fondo de un contenedor no ocupa. */
function libre(c: Caja, objetivo: Element): boolean {
  const W = innerWidth, H = innerHeight;
  const filas = [c.top + 5, (c.top + c.bottom) / 2, c.bottom - 5];
  const n = Math.max(2, Math.ceil((c.right - c.left) / 18) + 1);
  for (let i = 0; i < n; i++) {
    const px = c.left + 3 + ((c.right - c.left - 6) * i) / (n - 1);
    for (const py of filas) {
      if (px < 0 || py < 0 || px >= W || py >= H) return false;
      const e = document.elementsFromPoint(px, py).find((x) => !x.closest(".cap, .vip"));
      if (!e || e === objetivo || e.contains(objetivo)) continue;
      const o = e.closest(OBSTACULO);
      if ((o && !o.contains(objetivo)) || textoEn(e, px, py)) return false;
    }
  }
  return true;
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

  // ---------- cambiar de ruta cierra la ficha; tambien si su objetivo desaparece (p. ej. otro paso del pago)
  useEffect(() => { if (getCapa().abierta) cerrarFicha(); }, [loc.pathname, loc.search]);
  useEffect(() => {
    const ab = getCapa().abierta;
    if (ab && !st.marcas.some((m) => m.e.id === ab)) cerrarFicha();
  }, [st.marcas]);

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
  // Cada marcador busca un lugar AFUERA de su objetivo que este libre de verdad (nunca tapa lo que explica ni lo de al
  // lado): arriba o abajo segun su lado, con el rotulo largo o solo el id («D1»); en objetivos de una linea, al final
  // de esa linea; o en el margen de al lado. Solo si nada cabe, a caballo del borde (como antes, con el id solo).
  // El lugar elegido (plan) se guarda relativo al objetivo: al desplazar solo se traslada, y se vuelve a elegir cuando
  // la pagina cambia o el desplazamiento se detiene.
  useEffect(() => {
    if (!st.activa) return;
    let raf = 0;
    let tm = 0;
    let replanT = 0;
    let replanPend = true;
    const planes = new Map<string, Plan>();
    const largos = new Map<string, number>();
    const colocar = () => {
      cancelAnimationFrame(raf); window.clearTimeout(tm); raf = 0;
      const replan = replanPend;
      replanPend = false;
      const W = innerWidth, H = innerHeight;
      const tope = topeSuperior();
      const demo = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--demo-h")) || 0;
      const barras = Array.from(document.querySelectorAll(BARRAS_ABAJO)).map((el) => el.getBoundingClientRect())
        .filter((r) => r.height > 0 && r.width > 0 && r.bottom >= H - 24 && r.top < H);
      const pisoDe = (x: number, w: number) => { let p = H; for (const br of barras) if (br.left < x + w && br.right > x && br.top < p) p = br.top; return p; };
      const fichaRect = ficha.current?.getBoundingClientRect() || null;
      // lecturas y eleccion del lugar (elementsFromPoint tambien es lectura: todavia no se escribio nada en este cuadro).
      // De arriba hacia abajo: cada uno evita tambien a los marcadores ya colocados.
      const colocados: Caja[] = [];
      const enOrden = st.marcas.map((m) => ({ m, r: m.el.getBoundingClientRect() })).sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);
      const pos = enOrden.map(({ m, r }) => {
        const id = m.e.id;
        const b = botones.current.get(id);
        const h = b?.offsetHeight || 28;
        const corto = b?.dataset.angosto === "1";
        const sp = b?.querySelector<HTMLElement>(".cap-marca__corto");
        if (b && !corto) largos.set(id, b.offsetWidth);
        const wS = b ? b.offsetWidth - (sp && !corto ? sp.offsetWidth : 0) : 40;
        const wL = Math.max(wS, largos.get(id) || wS);
        const lim = m.el.closest(".sh-head, [data-pp-nav]") ? demo : tope;
        const lado = m.e.lado || "arriba-der";
        const der = lado.endsWith("der"), arriba = lado.startsWith("arriba");
        let plan = planes.get(id);
        const cambio = !plan || Math.abs(plan.tw - r.width) > 1 || Math.abs(plan.th - r.height) > 1;
        if ((replan || cambio) && r.width > 0 && r.bottom > lim && r.top < H) {
          // fin de la linea (objetivos de una sola linea: una insignia, un rotulo)
          let fin: number | null = null;
          if (r.height <= 44) {
            const rg = document.createRange();
            rg.selectNodeContents(m.el);
            const cr = rg.getBoundingClientRect();
            if (cr.width > 0) fin = cr.right;
          }
          const xEsq = (w: number) => (der ? r.right + 6 - w : r.left - 6);
          const y1 = arriba ? r.top - 6 - h : r.bottom + 6, y2 = arriba ? r.bottom + 6 : r.top - 6 - h;
          const cands: { x: number; y: number; corto: boolean }[] = [
            { x: xEsq(wL), y: y1, corto: false },
            // una insignia con relleno: afuera de su borde; un bloque ancho con texto corto: despues del texto
            ...(fin !== null ? [{ x: r.right - fin <= 24 ? r.right + 6 : fin + 8, y: r.top + (r.height - h) / 2, corto: true }] : []),
            { x: xEsq(wS), y: y1, corto: true },
            { x: der ? r.right + 6 : r.left - 6 - wS, y: Math.max(r.top, lim + 4), corto: true },
            { x: xEsq(wL), y: y2, corto: false },
            { x: xEsq(wS), y: y2, corto: true },
          ];
          const elegido = cands.find((c) => {
            const w = c.corto ? wS : wL;
            if (c.x < 8 || c.x + w > W - 8 || c.y < lim + 4 || c.y + h > pisoDe(c.x, w) - 8) return false;
            if (colocados.some((o) => c.x < o.right + 6 && c.x + w > o.left - 6 && c.y < o.bottom + 4 && c.y + h > o.top - 4)) return false;
            return libre({ left: c.x, right: c.x + w, top: c.y, bottom: c.y + h }, m.el);
          }) || { x: der ? r.right - wS - 8 : r.left + 8, y: (arriba ? r.top : r.bottom) - h / 2, corto: true };
          plan = { dx: elegido.x - r.left, dy: elegido.y - r.top, corto: elegido.corto, tw: r.width, th: r.height };
          planes.set(id, plan);
        }
        const p = plan || { dx: der ? r.width - wS - 8 : 8, dy: (arriba ? 0 : r.height) - h / 2, corto: true, tw: r.width, th: r.height };
        const w = p.corto ? wS : wL;
        const x = Math.max(8, Math.min(W - w - 8, r.left + p.dx));
        const piso = pisoDe(x, w);
        const y = Math.max(lim + 4, Math.min(piso - h - 8, r.top + p.dy));
        const visibleAlto = Math.min(r.bottom, piso) - Math.max(r.top, lim);
        // se oculta si sale de la ventana, si queda detras de una barra fija o si lo que queda visible entre la cabecera
        // y esa barra mide menos de 24 px (un objetivo mas bajito que 24 px cuenta entero: si se ve completo, lleva marca)
        const ok = r.width > 0 && r.bottom > lim && r.top < piso && y + h <= piso - 4 && visibleAlto >= Math.min(24, r.height - 0.5);
        if (ok) colocados.push({ left: x, right: x + w, top: y, bottom: y + h });
        return { id, x, y, w, h, ok, corto: p.corto };
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
        b.dataset.angosto = p.corto ? "1" : "";
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
    const replanear = () => { replanPend = true; pedir(); };
    // al desplazar: solo trasladar; al detenerse, volver a elegir (lo que hay alrededor pudo cambiar: cabecera, barras)
    const alDesplazar = () => { pedir(); window.clearTimeout(replanT); replanT = window.setTimeout(replanear, 160); };
    colocar();
    const ro = new ResizeObserver(replanear);
    ro.observe(document.documentElement);
    window.addEventListener("scroll", alDesplazar, { passive: true });
    window.addEventListener("resize", replanear, { passive: true });
    const mo = new MutationObserver(replanear);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "open", "hidden"] });
    document.fonts?.ready.then(replanear).catch(() => {});
    const t = window.setTimeout(replanear, 120);
    return () => {
      cancelAnimationFrame(raf); window.clearTimeout(tm); window.clearTimeout(t); window.clearTimeout(replanT);
      ro.disconnect(); mo.disconnect();
      window.removeEventListener("scroll", alDesplazar); window.removeEventListener("resize", replanear);
    };
  }, [st.activa, st.marcas, st.abierta]);

  const conCapa = esRutaDeTienda(loc.pathname) || loc.pathname.startsWith("/detras-de-escena");
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
      {st.activa && st.pista && conCapa && createPortal(
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
  // chips que no caben: flechas ‹ › (con mouse no hay otra forma evidente de llegar a los del final)
  const caja = useRef<HTMLUListElement>(null);
  const [desborde, setDesborde] = useState({ izq: false, der: false });
  useEffect(() => {
    const el = caja.current;
    if (!el) { setDesborde({ izq: false, der: false }); return; }
    const medir = () => {
      const izq = el.scrollLeft > 2, der = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
      setDesborde((d) => (d.izq === izq && d.der === der ? d : { izq, der }));
    };
    medir();
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    el.addEventListener("scroll", medir, { passive: true });
    return () => { ro.disconnect(); el.removeEventListener("scroll", medir); };
  }, [st.activa, st.marcas, st.existentes]);
  const correr = (dir: 1 | -1) => {
    const el = caja.current;
    if (el) el.scrollBy({ left: dir * Math.max(160, el.clientWidth * 0.7), behavior: calm() ? "auto" : "smooth" });
  };
  const conInterruptor = esRutaDeTienda(loc.pathname) || loc.pathname.startsWith("/detras-de-escena");
  const nProp = st.marcas.filter((m) => m.tipo === "propuesta").length;
  const irA = (m: Marca) => {
    m.el.scrollIntoView({ block: "center", behavior: calm() ? "auto" : "smooth" });
    abrirFicha(m.e.id);
  };
  return (
    <>
      {conInterruptor && st.activa && (
        <nav className="cap cap-chips" aria-label="Propuestas en esta pantalla" data-izq={desborde.izq ? "" : undefined} data-der={desborde.der ? "" : undefined}>
          {desborde.izq && <button type="button" className="cap-chips__flecha" data-lado="izq" tabIndex={-1} aria-hidden="true" onClick={() => correr(-1)}>‹</button>}
          {st.marcas.length === 0 ? (
            <span className="cap-chips__vacio">Aquí no hay propuestas · <Link to="/propuestas">Ver todas</Link></span>
          ) : null}
          <ul ref={caja} className="cap-chips__lista">
            {st.marcas.length > 0 && <>
              {st.marcas.map((m) => (
                <li key={m.e.id}>
                  <button type="button" className="cap-chip" data-tipo={m.tipo} data-cap-abre={m.e.id} aria-expanded={st.abierta === m.e.id} onClick={() => irA(m)}
                    aria-label={etiquetaDeMarca(m)}>
                    {m.tipo === "propuesta" ? <><b>{m.info.id}</b><span className="cap-chip__largo">{" · "}{"corto" in m.info ? m.info.corto : ""}</span></> : <>✓ {m.info.id}</>}
                  </button>
                </li>
              ))}
            </>}
            <li>
              <button type="button" className="cap-chip cap-chip--ya" aria-pressed={st.existentes} onClick={() => setExistentes(!st.existentes)}>
                <span className="cap-chip__largo">Ver también lo que ya existe</span><span className="cap-chip__corto">Lo que ya existe</span>
              </button>
            </li>
          </ul>
          {desborde.der && <button type="button" className="cap-chips__flecha" data-lado="der" tabIndex={-1} aria-hidden="true" onClick={() => correr(1)}>›</button>}
        </nav>
      )}
      {conInterruptor && (
        <button type="button" role="switch" aria-checked={st.activa} className="cap-switch" onClick={() => setActiva(!st.activa)}>
          {/* rotulo estable: el nombre accesible es el texto visible y el estado lo dicen role=switch + aria-checked */}
          <i className="cap-switch__pista" aria-hidden="true" /><span className="cap-switch__l">Ver propuestas</span><span className="cap-switch__c">Propuestas</span>
          {!st.activa && nProp > 0 && <b className="cap-switch__n"><span className="cap-sr">, </span>{nProp}<span className="cap-sr"> en esta pantalla</span></b>}
        </button>
      )}
      {loc.pathname !== "/propuestas" && <Link className="cap-lqv" to="/propuestas">Lo que viene</Link>}
    </>
  );
}
