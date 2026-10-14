/* Control de la demo sobre «Mi pedido» (mejora 1):
   - ?pedido=ejemplo-<estado>: tarjeta «Pedido de ejemplo · ORD-000128» para recorrer los 6 estados reales; cada uno
     navega con replace y OrderStatus (key={pedido}) se vuelve a cargar solo. Arranca PLEGADO (barra «Pedido de ejemplo ·
     {estado} ▴») salvo en pantallas muy anchas y altas, donde la tarjeta cabe al costado sin tapar los productos ni el
     total del pedido. En escritorio, si en su esquina quedaria encima del pedido (p. ej. el total a 1280×720), pasa a la
     otra esquina de abajo. Debajo de 1024 px la barra va a todo lo ancho y se despliega en hoja.
   - ?v=mi-pedido: pista para abrir el pedido de ejemplo.
   - ?pedido=demo-*: el pedido de prueba de esta sesion, con el enlace a «Detrás de escena» y el hilo carmesi.
   Pantalla de trabajo: nada se anima. */
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { EVENTOS } from "../registro";
import { HiloCarmesi } from "../_comun/componentes";
import { ETIQUETA_ESTADO, EVENTO_PEDIDOS, leerPedidosDemo, type CambioPedido } from "../_comun/pedidos";

export const ESTADOS_EJEMPLO = ["nuevo", "en_revision", "pagado", "preparando", "enviado", "entregado"] as const;
type EstadoEjemplo = (typeof ESTADOS_EJEMPLO)[number];

const PIE: Record<EstadoEjemplo, string> = {
  nuevo: "María eligió SINPE Móvil. Los productos quedan apartados mientras paga; si no paga a tiempo, la reserva vence sola.",
  en_revision: "María subió el comprobante. Solo lo ve el equipo de Crimson, que confirma el pago desde su panel.",
  pagado: "Crimson confirmó el pago y la factura electrónica salió sola.",
  preparando: "El equipo arma el paquete; María ve el avance sin tener que escribir.",
  enviado: "Salió con guía de Correos de Costa Rica (guía de ejemplo): María la tiene aquí mismo.",
  entregado: "Entregado. Con la propuesta B1, este pedido quedaría en su cuenta con sus equipos y su garantía.",
};

/** Barra a todo lo ancho que se despliega en hoja (movil y tableta). */
const Q_HOJA = "(max-width: 1023px)";
/** Solo aqui la tarjeta abierta (360 px) cabe al costado del pedido (1152 px de ancho, centrado) sin taparlo. */
const Q_ABIERTA = "(min-width: 1920px) and (min-height: 860px)";
const mq = (q: string) => typeof matchMedia === "function" && matchMedia(q).matches;
const esMovil = () => mq(Q_HOJA);

/** Lo que el control nunca tapa al quedarse quieto en escritorio: el pedido (cabecera, avance, columnas, total), la
    busqueda de «Mi pedido» y el panel de la guia. */
const CONTENIDO = ".ck-thanks, .ck-ohead, .ck-closed, .ck-track, .ck-ogrid > div > *, .ck-ogrid > aside, .ck-lookup, .vip-tour";
type Caja = { left: number; right: number; top: number; bottom: number };

/** Escritorio: el control vive abajo a la derecha; si ahi queda encima del pedido y en la esquina de abajo a la
    izquierda no, se pasa a esa (y vuelve cuando la derecha se libera). Si las dos chocan, no se mueve: el usuario ya
    desplazo la pagina y lo que pasa por debajo de una barra fija se ve al seguir bajando. Lecturas en un rAF. */
function useEsquiva(ref: RefObject<HTMLElement | null>, activo: boolean): "der" | "izq" {
  const [lado, setLado] = useState<"der" | "izq">("der");
  useEffect(() => {
    if (!activo) { setLado("der"); return; }
    let raf = 0;
    const medir = () => {
      raf = 0;
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (!r.height || !r.width) return;
      const W = document.documentElement.clientWidth;
      const espejo: Caja = { left: W - r.right, right: W - r.left, top: r.top, bottom: r.bottom };
      const bloques = Array.from(document.querySelectorAll(CONTENIDO)).map((x) => x.getBoundingClientRect()).filter((b) => b.width > 0 && b.height > 0);
      const choca = (a: Caja) => bloques.some((b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top);
      if (choca(r) && !choca(espejo)) setLado((l) => (l === "der" ? "izq" : "der"));
    };
    const pedir = () => { if (!raf) raf = requestAnimationFrame(medir); };
    pedir();
    const t = window.setTimeout(pedir, 400); // el pedido llega despues (fetch al mock)
    const mo = new MutationObserver(pedir);
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("scroll", pedir, { passive: true });
    window.addEventListener("resize", pedir, { passive: true });
    return () => {
      cancelAnimationFrame(raf); window.clearTimeout(t); mo.disconnect();
      window.removeEventListener("scroll", pedir); window.removeEventListener("resize", pedir);
    };
  }, [activo, ref]);
  return lado;
}

export default function ControlDemo() {
  const loc = useLocation();
  const sp = new URLSearchParams(loc.search);
  const pedido = sp.get("pedido");
  if (pedido && pedido.startsWith("ejemplo-")) {
    const k = pedido.slice(8) as EstadoEjemplo;
    if ((ESTADOS_EJEMPLO as readonly string[]).includes(k)) return <ControlEjemplo estado={k} />;
  }
  if (pedido && pedido.startsWith("demo-")) return <ControlPrueba token={pedido} />;
  if (!pedido && sp.get("v") === "mi-pedido") return <PistaBuscar />;
  return null;
}

function Tarjeta({ titulo, resumen, children }: { titulo: string; resumen: string; children: ReactNode }) {
  const [movil, setMovil] = useState(esMovil);
  const [abierta, setAbierta] = useState(() => mq(Q_ABIERTA));
  const cuerpoId = useId();
  const caja = useRef<HTMLElement>(null);
  const lado = useEsquiva(caja, !movil);
  // en movil la barra plegada ocupa el pie: el credito de la demo deja espacio para no quedar tapado
  useEffect(() => {
    document.documentElement.classList.add("cap-con-control");
    return () => document.documentElement.classList.remove("cap-con-control");
  }, []);
  useEffect(() => {
    const hoja = matchMedia(Q_HOJA);
    const ancha = matchMedia(Q_ABIERTA);
    const on = () => { setMovil(hoja.matches); setAbierta(ancha.matches); };
    hoja.addEventListener("change", on);
    ancha.addEventListener("change", on);
    return () => { hoja.removeEventListener("change", on); ancha.removeEventListener("change", on); };
  }, []);
  useEffect(() => {
    if (!abierta || !movil) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !e.defaultPrevented) { e.preventDefault(); setAbierta(false); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [abierta, movil]);
  return (
    <aside ref={caja} className="cap cap-control" data-abierta={abierta ? "" : undefined} data-lado={lado} aria-label={titulo}>
      <button type="button" className="cap-control__head" aria-expanded={abierta} aria-controls={cuerpoId} onClick={() => setAbierta((v) => !v)}>
        <span className="cap-control__t"><i aria-hidden="true" />{titulo}</span>
        <span className="cap-control__r">{!abierta && <span className="cap-control__res">{resumen}</span>}<span aria-hidden="true">{abierta ? "▾" : "▴"}</span><span className="cap-sr">{abierta ? " (minimizar)" : " (abrir)"}</span></span>
      </button>
      <div id={cuerpoId} className="cap-control__body" hidden={!abierta}>{children}</div>
    </aside>
  );
}

function ControlEjemplo({ estado: enRuta }: { estado: EstadoEjemplo }) {
  const navigate = useNavigate();
  // subir un comprobante sobre el ejemplo «Pendiente de pago» lo deja en revision sin cambiar la ruta
  const [visto, setVisto] = useState<EstadoEjemplo | null>(null);
  useEffect(() => { setVisto(null); }, [enRuta]);
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<CambioPedido>).detail || {};
      if (d.token === `ejemplo-${enRuta}` && (ESTADOS_EJEMPLO as readonly string[]).includes(d.estado || "")) setVisto(d.estado as EstadoEjemplo);
    };
    window.addEventListener(EVENTO_PEDIDOS, on);
    return () => window.removeEventListener(EVENTO_PEDIDOS, on);
  }, [enRuta]);
  const estado = visto ?? enRuta;
  const i = ESTADOS_EJEMPLO.indexOf(estado);
  // misma ruta, otro estado en pantalla: se vuelve a montar la tienda para releer el pedido. Solo si el carrito vive en
  // localStorage (con el almacenamiento bloqueado, volver a montar vaciaria el carrito en memoria).
  const volverAlEstado = () => {
    let ok = false;
    try { localStorage.setItem("crimson-demo:prueba", "1"); localStorage.removeItem("crimson-demo:prueba"); ok = true; } catch { ok = false; }
    if (!ok) return;
    setVisto(null);
    window.dispatchEvent(new CustomEvent(EVENTOS.recargarTienda));
  };
  const ir = (k: EstadoEjemplo) => navigate(`/?pedido=ejemplo-${k}`, { replace: true });
  const irA = (k: EstadoEjemplo) => (k !== enRuta ? ir(k) : visto ? volverAlEstado() : undefined);
  return (
    <Tarjeta titulo="Pedido de ejemplo · ORD-000128" resumen={ETIQUETA_ESTADO[estado]}>
      <p className="cap-control__sub">Así lo ve María, la clienta. Recorré cada estado:</p>
      <ol className="cap-estados" aria-label="Estados del pedido de ejemplo">
        {ESTADOS_EJEMPLO.map((k, n) => (
          <li key={k}>
            <button type="button" aria-current={k === estado ? "step" : undefined} data-hecho={n < i ? "" : undefined} onClick={() => irA(k)}>
              <span aria-hidden="true">{n + 1}</span>{ETIQUETA_ESTADO[k]}
            </button>
          </li>
        ))}
      </ol>
      <div className="cap-control__nav">
        <button type="button" className="cap-btn cap-btn--fantasma" disabled={i === 0} onClick={() => irA(ESTADOS_EJEMPLO[i - 1])}>← Estado anterior</button>
        <button type="button" className="cap-btn cap-btn--claro" disabled={i === ESTADOS_EJEMPLO.length - 1} onClick={() => irA(ESTADOS_EJEMPLO[i + 1])}>Estado siguiente →</button>
      </div>
      <p className="cap-control__pie" aria-live="polite">{PIE[estado]}{estado === "entregado" && <> <Link to="/propuestas/b1">Ver la maqueta B1 →</Link></>}</p>
      <Link className="cap-control__crimson" to={`/detras-de-escena/pedidos?pedido=ORD-000128&estado=${estado}`}>Ver este pedido como lo ve Crimson →</Link>
      <Hilo><HiloCarmesi aqui="mi-pedido" numero="ORD-000128" token={`ejemplo-${estado}`} /></Hilo>
    </Tarjeta>
  );
}

function ControlPrueba({ token }: { token: string }) {
  // el estado cambia sin cambiar la ruta (comprobante subido, pago simulado): se relee con el aviso de mockApi
  const [, setVuelta] = useState(0);
  useEffect(() => {
    const on = () => setVuelta((n) => n + 1);
    window.addEventListener(EVENTO_PEDIDOS, on);
    return () => window.removeEventListener(EVENTO_PEDIDOS, on);
  }, []);
  const p = leerPedidosDemo().find((x) => x.token === token);
  const numero = p?.number || `DEMO-${token.split("-")[1] || ""}`;
  return (
    <Tarjeta titulo={`Tu pedido de prueba · ${numero}`} resumen={p?.status_label || "Pedido de prueba"}>
      <p className="cap-control__sub">Este es tu pedido de prueba. Nada se cobró ni se envió.</p>
      <Link className="cap-control__crimson" to={`/detras-de-escena/pedidos?pedido=${encodeURIComponent(numero)}`}>Seguilo del lado de Crimson →</Link>
      <Hilo><HiloCarmesi aqui="mi-pedido" numero={numero} token={token} /></Hilo>
    </Tarjeta>
  );
}

/** El hilo carmesi plegado: la tarjeta no tapa el pedido y el recorrido queda a un toque. */
function Hilo({ children }: { children: ReactNode }) {
  return (
    <details className="cap-control__hilo">
      <summary>Seguí esta compra por el hilo carmesí</summary>
      {children}
    </details>
  );
}

function PistaBuscar() {
  const navigate = useNavigate();
  const caja = useRef<HTMLElement>(null);
  const [movil] = useState(esMovil);
  const lado = useEsquiva(caja, !movil);
  return (
    <aside ref={caja} className="cap cap-control cap-control--pista" data-lado={lado} aria-label="Pedido de ejemplo">
      <p>¿No tenés un pedido a mano? Abrí el pedido de ejemplo ORD-000128.</p>
      <button type="button" className="cap-btn cap-btn--claro" onClick={() => navigate("/?pedido=ejemplo-enviado")}>Abrir el pedido de ejemplo</button>
    </aside>
  );
}
