/* Componentes comunes de las maquetas de propuestas (demo de la tienda Crimson).
   Tres estados visuales que nunca se confunden: EXISTENTE (la tienda tal cual), YA CONSTRUIDO con datos de ejemplo
   (borde solido) y PROPUESTA (borde punteado carmesi + sello «Propuesta · no incluida aún»).
   Pantallas de decision y trabajo: nada se mueve solo; solo responde lo que el usuario toca. */
import { createContext, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { CLAVES, ETAPAS, ORDEN_MAQUETAS, esPropuestaId, infoMaqueta, type Etapa, type MaquetaId } from "../registro";
import { usePaso } from "./hooks";
import { almacen } from "./pedidos";
import { BotonMeInteresa, SelloPropuesta, Visto } from "./sello";
import "./comun.css";

export { BotonMeInteresa, SelloPropuesta };

// ---------------------------------------------------------------- utilidades
function useMedia(q: string): boolean {
  const get = () => typeof matchMedia === "function" && matchMedia(q).matches;
  const [m, setM] = useState(get);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(q);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return m;
}

function useReloj(): string {
  const fmt = () => new Date().toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const [t, setT] = useState(fmt);
  useEffect(() => { const id = window.setInterval(() => setT(fmt()), 1000); return () => window.clearInterval(id); }, []);
  return t;
}

const Flecha = ({ izq }: { izq?: boolean }) => (
  <svg className="pp-flecha" viewBox="0 0 24 24" aria-hidden="true">{izq ? <path d="M19 12H5M11 6l-6 6 6 6" /> : <path d="M5 12h14M13 6l6 6-6 6" />}</svg>
);

// ---------------------------------------------------------------- barra de monitor
export function BarraMonitor({ cam, rotulo, derecha }: { cam: string; rotulo: string; derecha?: ReactNode }) {
  const t = useReloj();
  return (
    <div className="pp-monitor">
      <span className="pp-monitor__l" aria-hidden="true"><i className="pp-rec" />CAM {cam}<span className="pp-monitor__sep">·</span>{rotulo}</span>
      <span className="pp-monitor__r"><span className="pp-monitor__t" aria-hidden="true">{t}</span>{derecha}</span>
    </div>
  );
}

// ---------------------------------------------------------------- navegacion entre maquetas
const ultimaRuta = () => almacen.sesion.get(CLAVES.ultimaRuta) || "/";

export function NavPropuestas({ actual }: { actual?: MaquetaId | "LQV" }) {
  const [abierta, setAbierta] = useState(false);
  const lista = useRef<HTMLDivElement>(null);
  const boton = useRef<HTMLButtonElement>(null);
  const idListaId = useId();
  const i = actual && actual !== "LQV" ? ORDEN_MAQUETAS.indexOf(actual) : -1;
  const prev = i > 0 ? ORDEN_MAQUETAS[i - 1] : null;
  const next = i >= 0 ? ORDEN_MAQUETAS[i + 1] ?? null : ORDEN_MAQUETAS[0];
  const titulo = !actual ? "Propuestas" : actual === "LQV" ? "Lo que viene" : infoMaqueta(actual).titulo;
  const volver = ultimaRuta();

  useEffect(() => {
    if (!abierta) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setAbierta(false); boton.current?.focus(); } };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!lista.current?.contains(t) && !boton.current?.contains(t)) setAbierta(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [abierta]);

  const cerrar = () => setAbierta(false);
  const cur = (m: MaquetaId | "LQV") => (actual === m ? "page" as const : undefined);

  return (
    <nav className="pp-nav" data-pp-nav aria-label="Propuestas">
      <div className="pp-nav__in">
        <Link className="pp-nav__tienda" to={volver}><Flecha izq /><span>Tienda</span></Link>
        <span className="pp-nav__titulo" aria-hidden="true">{titulo}</span>
        <div className="pp-nav__links">
          <Link to="/propuestas" aria-current={cur("LQV")}>Lo que viene</Link>
          {prev && <Link to={infoMaqueta(prev).ruta} className="pp-nav__paso"><span aria-hidden="true">‹ </span><span className="pp-sr">Maqueta anterior: </span>{infoMaqueta(prev).corto}</Link>}
          {next && <Link to={infoMaqueta(next).ruta} className="pp-nav__paso"><span className="pp-sr">{i >= 0 ? "Maqueta siguiente: " : "Primera maqueta: "}</span>{infoMaqueta(next).corto}<span aria-hidden="true"> ›</span></Link>}
          <Link to="/rentabilidad" aria-current={cur("RT")}>¿Es rentable?</Link>
          <Link to="/detras-de-escena" aria-current={cur("DE")}>Detrás de escena</Link>
        </div>
        <div className="pp-nav__movil">
          {prev ? <Link className="pp-nav__flecha" to={infoMaqueta(prev).ruta} aria-label={`Maqueta anterior: ${infoMaqueta(prev).titulo}`}>‹</Link> : <span className="pp-nav__flecha" aria-hidden="true" data-off>‹</span>}
          {next ? <Link className="pp-nav__flecha" to={infoMaqueta(next).ruta} aria-label={`${i >= 0 ? "Maqueta siguiente" : "Primera maqueta"}: ${infoMaqueta(next).titulo}`}>›</Link> : <span className="pp-nav__flecha" aria-hidden="true" data-off>›</span>}
          <button ref={boton} type="button" className="pp-nav__todas" aria-expanded={abierta} aria-controls={idListaId} onClick={() => setAbierta((v) => !v)}>Todas</button>
        </div>
      </div>
      <div ref={lista} id={idListaId} className="pp-nav__lista" hidden={!abierta}>
        <ul>
          <li><Link to="/propuestas" onClick={cerrar} aria-current={cur("LQV")}>Lo que viene <small>17 propuestas</small></Link></li>
          {ORDEN_MAQUETAS.map((m) => (
            <li key={m}><Link to={infoMaqueta(m).ruta} onClick={cerrar} aria-current={cur(m)}>{m !== "RT" && <b>{m}</b>}{infoMaqueta(m).titulo}</Link></li>
          ))}
          <li><Link to="/detras-de-escena" onClick={cerrar} aria-current={cur("DE")}>Detrás de escena <small>ya existe</small></Link></li>
          <li><Link to={volver} onClick={cerrar}>← Volver a la tienda</Link></li>
        </ul>
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------- marco de maqueta
type CtxMarco = { id: MaquetaId | ""; estado: "propuesta" | "existente" };
const MarcoCtx = createContext<CtxMarco>({ id: "", estado: "propuesta" });

export type Paso = { id: string; titulo: string; vista: "cliente" | "crimson" };
export type MarcoMaquetaProps = {
  id: MaquetaId; titulo: string; bajada: string;
  estado?: "propuesta" | "existente";
  etapa?: Etapa;
  hoy?: string;
  conPropuesta?: string;
  pasos?: readonly Paso[];
  aparte?: ReactNode;
  children: ReactNode;
};

const OFFSET_STICKY = () => {
  const demo = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--demo-h")) || 0;
  const nav = document.querySelector("[data-pp-nav]")?.getBoundingClientRect().height || 0;
  return demo + nav + 16;
};

export function MarcoMaqueta({ id, titulo, bajada, estado: est, etapa, hoy, conPropuesta, pasos, aparte, children }: MarcoMaquetaProps) {
  const estado = est ?? (id === "DE" ? "existente" : "propuesta");
  const ids = useMemo(() => (pasos || []).map((p) => p.id), [pasos]);
  const [i, irA] = usePaso(ids);
  const h1 = useRef<HTMLHeadingElement>(null);
  const h2 = useRef<HTMLHeadingElement>(null);
  const listaPasos = useRef<HTMLOListElement>(null);
  const prevI = useRef(i);
  const escritorio = useMedia("(min-width: 1024px)");
  const [aparteAbierto, setAparteAbierto] = useState(escritorio);
  useEffect(() => { setAparteAbierto(escritorio); }, [escritorio]);
  const h1Id = useId();
  const h2Id = useId();

  useEffect(() => {
    window.scrollTo(0, 0);
    document.title = id === "DE" ? `${titulo} — Detrás de escena · Tienda Crimson (vista previa)`
      : id === "RT" ? `${titulo} · Tienda Crimson (vista previa)`
        : `${id} · ${titulo} — Propuesta · Tienda Crimson (vista previa)`;
    h1.current?.focus({ preventScroll: true });
  }, [id, titulo]);

  // movil: la lista horizontal de pasos muestra el actual (sin mover la pagina)
  useEffect(() => {
    const ol = listaPasos.current;
    const li = ol?.children[i] as HTMLElement | undefined;
    if (!ol || !li || ol.scrollWidth <= ol.clientWidth) return;
    ol.scrollLeft = Math.max(0, li.offsetLeft - 16);
  }, [i]);

  useEffect(() => {
    if (prevI.current === i) return;
    prevI.current = i;
    const el = h2.current;
    if (!el) return;
    el.focus({ preventScroll: true });
    const r = el.getBoundingClientRect();
    const off = OFFSET_STICKY();
    if (r.top < off || r.top > innerHeight * 0.55) window.scrollTo({ top: Math.max(0, r.top + scrollY - off), behavior: "auto" });
  }, [i]);

  const paso = pasos?.[i];
  const total = pasos?.length || 0;
  const iOrden = ORDEN_MAQUETAS.indexOf(id);
  const sigMaqueta: MaquetaId | null = iOrden >= 0 ? ORDEN_MAQUETAS[iOrden + 1] ?? null : ORDEN_MAQUETAS[0];
  const etapaInfo = etapa ? ETAPAS.find((e) => e.n === etapa) : undefined;
  const ctx = useMemo<CtxMarco>(() => ({ id, estado }), [id, estado]);
  const rotulo = estado === "existente" ? "YA CONSTRUIDO · DATOS DE EJEMPLO" : `MAQUETA · ${id} · DATOS DE EJEMPLO`;

  return (
    <MarcoCtx.Provider value={ctx}>
      <div className="pp pp-marco" data-estado={estado}>
        <NavPropuestas actual={id} />
        <main id="contenido" tabIndex={-1} className="pp-marco__main" aria-labelledby={h1Id}>
          <header className="pp-cab">
            <p className="pp-rotulo"><i aria-hidden="true" />{rotulo}</p>
            <div className="pp-cab__chips">
              <SelloPropuesta estado={estado} id={estado === "propuesta" && id !== "RT" ? id : undefined} />
              {etapaInfo && <span className="pp-etapa">Etapa {etapaInfo.n} · {etapaInfo.titulo}</span>}
            </div>
            <h1 ref={h1} id={h1Id} tabIndex={-1} className="pp-h1">{titulo}</h1>
            <p className="pp-bajada">{bajada}</p>
            {(hoy || conPropuesta) && (
              <div className="pp-compara">
                {hoy && <div className="pp-compara__hoy"><span className="pp-ceja"><Visto />Hoy en la tienda</span><p>{hoy}</p></div>}
                {conPropuesta && <div className="pp-compara__con"><span className="pp-ceja">Con la propuesta</span><p>{conPropuesta}</p></div>}
              </div>
            )}
            <div className="pp-cab__acc">
              {esPropuestaId(id) && <BotonMeInteresa id={id} />}
              <p className="pp-aviso" role="note">Maqueta con datos de ejemplo: no funciona ni guarda nada real.</p>
            </div>
          </header>

          <div className="pp-cuerpo" data-pasos={total > 0 ? "" : undefined} data-aparte={aparte ? "" : undefined}>
            {total > 0 && pasos && (
              <nav className="pp-pasos" aria-label="Pasos de la maqueta">
                <ol ref={listaPasos}>
                  {pasos.map((p, n) => (
                    <li key={p.id} aria-current={n === i ? "step" : undefined} data-hecho={n < i ? "" : undefined}>
                      <button type="button" onClick={() => irA(n)}>
                        <span className="pp-pasos__n" aria-hidden="true">{n < i ? <Visto /> : n + 1}</span>
                        <span className="pp-pasos__t">{p.titulo}<small>{p.vista === "crimson" ? "Así lo ve Crimson" : "Así lo ve el cliente"}</small></span>
                        <span className="pp-sr">{`, paso ${n + 1} de ${total}`}{n === i ? " (actual)" : ""}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </nav>
            )}
            <section className="pp-escena" aria-labelledby={paso ? h2Id : h1Id}>
              {paso && (
                <h2 ref={h2} id={h2Id} tabIndex={-1} className="pp-h2">
                  <span className="pp-h2__ceja">Paso {i + 1} de {total} · {paso.vista === "crimson" ? "Crimson" : "Cliente"}</span>
                  {paso.titulo}
                </h2>
              )}
              {children}
            </section>
            {aparte && (
              <details className="pp-aparte" open={aparteAbierto} onToggle={(e) => setAparteAbierto((e.currentTarget as HTMLDetailsElement).open)}>
                <summary>Qué pasa aquí</summary>
                <div className="pp-aparte__in"><span className="pp-aparte__ceja">Qué pasa aquí</span>{aparte}</div>
              </details>
            )}
          </div>

          <footer className="pp-pie">
            {paso && i > 0
              ? <button type="button" className="pp-btn pp-btn--fantasma" onClick={() => irA(i - 1)}><Flecha izq />{pasos![i - 1].titulo}</button>
              : <span />}
            {paso && i < total - 1
              ? <button type="button" className="pp-btn pp-btn--tinta" onClick={() => irA(i + 1)}>{pasos![i + 1].titulo}<Flecha /></button>
              : sigMaqueta
                ? <Link className="pp-btn pp-btn--crimson" to={infoMaqueta(sigMaqueta).ruta}><span>Siguiente maqueta: {infoMaqueta(sigMaqueta).titulo}</span><Flecha /></Link>
                : <Link className="pp-btn pp-btn--crimson" to="/propuestas">Ver lo que viene<Flecha /></Link>}
          </footer>
        </main>
      </div>
    </MarcoCtx.Provider>
  );
}

// ---------------------------------------------------------------- pantallas simuladas
export type Superficie = "tienda" | "portal" | "correo" | "celular" | "admin" | "pdf";
export type SeccionAdmin = "Inicio" | "Pedidos" | "Inventarios" | "Mi Tienda" | "Levantamientos" | "Cotizaciones"
  | "Soporte" | "Mantenimientos" | "Tablero de la tienda";
const SECCIONES: SeccionAdmin[] = ["Inicio", "Pedidos", "Inventarios", "Mi Tienda", "Levantamientos", "Cotizaciones", "Soporte", "Mantenimientos", "Tablero de la tienda"];
const ROTULO: Record<Superficie, string> = {
  tienda: "Así lo ve el cliente", portal: "Portal del cliente", correo: "Correo al cliente",
  celular: "En el celular del cliente", admin: "Así lo ve Crimson", pdf: "Documento PDF",
};

export function Pantalla({ superficie, rotulo, direccion, seccion, ancho = "completo", estado: est, id, className, children }: {
  superficie: Superficie; rotulo?: string; direccion?: string; seccion?: SeccionAdmin; ancho?: "completo" | "telefono";
  estado?: "propuesta" | "existente"; id?: string; className?: string; children: ReactNode;
}) {
  const marco = useContext(MarcoCtx);
  const estado = est ?? marco.estado;
  const rot = rotulo || ROTULO[superficie];
  const etiqueta = estado === "existente" ? "Plataforma Crimson · datos de ejemplo" : `Maqueta${marco.id ? ` · ${marco.id}` : ""} · datos de ejemplo`;
  return (
    <div id={id} className={`pp-pant${className ? ` ${className}` : ""}`} data-sup={superficie} data-estado={estado} data-ancho={ancho} role="group" aria-label={rot}>
      <div className="pp-pant__cap"><span className="pp-pant__rot">{rot}</span><span className="pp-pant__est"><i aria-hidden="true" />{etiqueta}</span></div>
      <div className="pp-pant__marco">
        <span className="pp-pant__esq" aria-hidden="true"><i /><i /><i /><i /></span>
        <div className="pp-pant__in">
        {(superficie === "tienda" || superficie === "portal") && (
          <div className="pp-pant__nav" aria-hidden="true"><span className="pp-pant__dots"><i /><i /><i /></span><span className="pp-pant__url">{direccion || (superficie === "portal" ? "portal.crimsoncr.com" : "tienda.crimsoncr.com")}</span></div>
        )}
        {superficie === "admin" ? (
          <div className="pp-pant__admin">
            <div className="pp-pant__menu" aria-hidden="true">
              <b>Crimson</b>
              {SECCIONES.map((s) => <span key={s} data-on={s === seccion ? "" : undefined}>{s}</span>)}
            </div>
            <div className="pp-pant__adminbody">
              <p className="pp-pant__seccion">Plataforma Crimson{seccion ? ` · ${seccion}` : ""}</p>
              {children}
            </div>
          </div>
        ) : superficie === "celular" ? (
          <div className="pp-pant__tel"><div className="pp-pant__telin">{children}</div></div>
        ) : superficie === "pdf" ? (
          <div className="pp-pant__hoja">{children}</div>
        ) : (
          <div className="pp-pant__cuerpo">{children}</div>
        )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- el hilo carmesi
export type Estacion = "compra" | "mi-pedido" | "crimson" | "portal" | "tablero" | "numeros";

export function HiloCarmesi({ aqui, numero = "ORD-000128", token = "ejemplo-enviado" }: { aqui: Estacion; numero?: string; token?: string }) {
  const est: { k: Estacion; t: string; to: string; tipo: "existe" | "propuesta" | "modelo" }[] = [
    { k: "compra", t: "Compra", to: "/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53", tipo: "existe" },
    { k: "mi-pedido", t: "Mi pedido", to: `/?pedido=${encodeURIComponent(token)}`, tipo: "existe" },
    { k: "crimson", t: "Crimson lo atiende", to: `/detras-de-escena/pedidos?pedido=${encodeURIComponent(numero)}`, tipo: "existe" },
    { k: "portal", t: "Portal del cliente", to: "/propuestas/b1?paso=compras", tipo: "propuesta" },
    { k: "tablero", t: "Tablero", to: "/propuestas/c1", tipo: "propuesta" },
    { k: "numeros", t: "¿Es rentable?", to: "/rentabilidad", tipo: "modelo" },
  ];
  const TIPO = { existe: "Ya existe", propuesta: "Propuesta", modelo: "Modelo" } as const;
  return (
    <nav className="pp-hilo" aria-label={`El hilo carmesí: el pedido ${numero}, visto desde cada lado`}>
      <p className="pp-hilo__t"><span className="pp-hilo__ceja">El hilo carmesí</span><span>La misma compra, <b>{numero}</b>, vista desde cada lado.</span></p>
      <ol className="pp-hilo__lista">
        {est.map((e, n) => (
          <li key={e.k} data-tipo={e.tipo} aria-current={e.k === aqui ? "step" : undefined}>
            <Link to={e.to}>
              <i className="pp-hilo__punto" aria-hidden="true" />
              <span className="pp-hilo__n" aria-hidden="true">{String(n + 1).padStart(2, "0")}</span>
              <b>{e.t}</b>
              <small>{TIPO[e.tipo]}{e.k === aqui ? " · aquí" : ""}</small>
            </Link>
          </li>
        ))}
      </ol>
    </nav>
  );
}
