/* Ficha de una etiqueta de la capa. Escritorio: tarjeta NO modal de 340 px anclada al marcador (z 58; la coloca el
   motor). Movil (<768): hoja inferior con velo (z 57/58), aria-modal y foco contenido. En los dos: foco al titulo,
   Esc o tocar afuera la cierran y el foco vuelve a quien la abrio. Sin animacion: es una pantalla de decision. */
import { useEffect, useId, useRef, useState, type MutableRefObject } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { ETAPAS, porId, type Etiqueta, type Propuesta } from "../registro";
import { BotonMeInteresa, SelloPropuesta } from "../_comun/sello";
import { cerrarFicha } from "./estado";

// escritorio: nace fuera de la pantalla (no oculta: asi el titulo puede recibir el foco) y el motor la ancla al marcador
const FUERA = { transform: "translate3d(-10000px, 0, 0)" } as const;
const esMovil = () => typeof matchMedia === "function" && matchMedia("(max-width: 767px)").matches;

export default function FichaEtiqueta({ etiqueta, refFicha }: { etiqueta: Etiqueta; refFicha: MutableRefObject<HTMLDivElement | null> }) {
  const info = porId(etiqueta.propuesta);
  const [movil, setMovil] = useState(esMovil);
  const titulo = useRef<HTMLHeadingElement>(null);
  const abridor = useRef<HTMLElement | null>(null);
  const tId = useId();

  useEffect(() => {
    const mq = matchMedia("(max-width: 767px)");
    const on = () => setMovil(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // foco al titulo; al cerrar vuelve a quien abrio (o al marcador de esta etiqueta)
  useEffect(() => {
    const act = document.activeElement as HTMLElement | null;
    abridor.current = act && act !== document.body ? act : null;
    titulo.current?.focus({ preventScroll: true });
    const id = etiqueta.id;
    return () => {
      const a = abridor.current;
      const destino = a && a.isConnected ? a : document.querySelector<HTMLElement>(`.cap-marca[data-cap-abre="${CSS.escape(id)}"]`);
      destino?.focus({ preventScroll: true });
    };
  }, [etiqueta.id]);

  // Esc, tocar afuera (escritorio) y foco contenido (movil)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cerrarFicha(); return; }
      if (e.key !== "Tab" || !movil) return;
      const f = refFicha.current;
      if (!f) return;
      const foc = Array.from(f.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), [tabindex='0']"));
      if (!foc.length) return;
      const first = foc[0], last = foc[foc.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === titulo.current)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    const onDown = (e: PointerEvent) => {
      if (movil) return; // en movil cierra el velo
      const t = e.target as Element | null;
      if (!t || refFicha.current?.contains(t) || t.closest("[data-cap-abre], .cap-switch, .cap-chips")) return;
      cerrarFicha();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    return () => { document.removeEventListener("keydown", onKey, true); document.removeEventListener("pointerdown", onDown, true); };
  }, [movil, refFicha]);

  if (!info) return null;
  const prop = !info.id.startsWith("H") ? (info as Propuesta) : null;
  const etapa = prop ? ETAPAS.find((e) => e.n === prop.etapa) : undefined;

  const cuerpo = (
    <div ref={refFicha} id="cap-ficha" className="cap cap-ficha" data-modo={movil ? "hoja" : "flota"} data-tipo={prop ? "propuesta" : "existente"}
      role="dialog" aria-labelledby={tId} aria-modal={movil ? true : undefined} style={movil ? undefined : FUERA}>
      <div className="cap-ficha__top">
        {prop ? <SelloPropuesta id={prop.id} /> : <SelloPropuesta estado="existente" />}
        {etapa && <span className="cap-ficha__etapa">Etapa {etapa.n} · {etapa.titulo}</span>}
        <button type="button" className="cap-ficha__x" onClick={cerrarFicha} aria-label="Cerrar la ficha">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>
        </button>
      </div>
      <h2 ref={titulo} id={tId} tabIndex={-1} className="cap-ficha__t">{prop ? `${prop.id} · ${prop.titulo}` : info.titulo}</h2>
      {prop ? <p className="cap-ficha__b">{prop.beneficio}</p> : <p className="cap-ficha__b">{"detalle" in info ? info.detalle : ""}</p>}
      <p className="cap-ficha__aqui"><b>Aquí:</b> {etiqueta.aqui}</p>
      {prop?.hoy && <p className="cap-ficha__hoy"><b>Hoy ya existe:</b> <span aria-hidden="true">✓ </span>{prop.hoy}</p>}
      {prop?.depende && <p className="cap-ficha__dep"><b>Depende de:</b> {prop.depende}</p>}
      <div className="cap-ficha__acc">
        {prop?.maqueta && <Link className="cap-btn cap-btn--crimson" to={prop.maqueta}>Ver maqueta <span aria-hidden="true">→</span></Link>}
        {prop && <BotonMeInteresa id={prop.id} variante="chip" />}
        {prop ? <Link className="cap-link" to="/propuestas">Ver todas las propuestas</Link> : <Link className="cap-link" to="/propuestas?ver=base">Ver todo lo construido</Link>}
      </div>
    </div>
  );

  return createPortal(
    movil ? <><div className="cap cap-velo" onClick={cerrarFicha} aria-hidden="true" />{cuerpo}</> : cuerpo,
    document.body,
  );
}
