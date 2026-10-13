/* COPIA LOCAL MINIMA de propuestas/_comun para el equipo GESTION (especificacion, seccion 1.6).
   Exporta un SUBCONJUNTO de los nombres de _comun con firmas identicas y comportamiento minimo, para construir y
   probar sin esperar a la capa. Al integrar, ../comun.ts pasa a `export * from "../_comun";` y esta carpeta se borra:
   ningun otro archivo cambia. Nada aqui exporta nombres que no esten en 1.6 (lo propio del equipo vive en ../ui.tsx).
   Clases con prefijo ges-pp- (pp- queda reservado a _comun). */
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import "./comun.css";

// ---------------------------------------------------------------- tipos y constantes (espejo de registro.ts)
export type PropuestaId = "A1" | "A2" | "A3" | "A4" | "A5" | "B1" | "B2" | "B3" | "B4" | "C1" | "C2" | "C3" | "D1" | "D2" | "E1" | "E2" | "E3";
export type ExistenteId = "H1" | "H2" | "H3" | "H4" | "H5" | "H6" | "H7" | "H8" | "H9" | "H10" | "H11" | "H12" | "H13" | "H14" | "H15" | "H16";
export type MaquetaId = PropuestaId | "DE" | "RT";
export type Etapa = 1 | 2 | 3 | 4 | 5;
export type Etiqueta = {
  id: string;
  propuesta: PropuestaId | ExistenteId;
  rutas: string[];
  selector: string[];
  aqui: string;
  irA: string;
  lado?: "arriba-der" | "arriba-izq" | "abajo-der" | "abajo-izq";
  preparar?: "carrito";
};

export const CLAVES = {
  interes: "crimson-demo:interes",
  notas: "crimson-demo:notas",
  capa: "crimson-demo:capa",
  capaExistentes: "crimson-demo:capa-existentes",
  foco: "crimson-demo:capa-foco",
  pista: "crimson-demo:capa-pista",
  ultimaRuta: "crimson-demo:ultima-ruta",
  pedidos: "crimson-demo:pedidos",
  estados: "crimson-demo:estados",
  vitrina: "crimson-demo:vitrina",
  planB2: "crimson-demo:b2:plan",
  rentabilidad: "crimson-demo:rentabilidad",
  rentabilidadResumen: "crimson-demo:rentabilidad-resumen",
  carrito: "crimson-shop:crimson",
} as const;
export const EVENTOS = {
  aviso: "crimson-demo:aviso",
  interes: "crimson-demo:interes",
  capa: "crimson-demo:capa",
  recargarTienda: "crimson-demo:recargar-tienda",
  tourCerrar: "crimson-demo:tour-cerrar",
} as const;

const IDS_PROPUESTA: readonly string[] = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "C1", "C2", "C3", "D1", "D2", "E1", "E2", "E3"];
/* titulos minimos para la navegacion local (la capa los saca de registro.ts) */
const MAQUETAS: { id: MaquetaId; titulo: string; ruta: string }[] = [
  { id: "A1", titulo: "Instalación con fecha y precio estimado", ruta: "/propuestas/a1" },
  { id: "A3", titulo: "Cotización formal para empresas", ruta: "/propuestas/a3" },
  { id: "B1", titulo: "La compra abre el portal del cliente", ruta: "/propuestas/b1" },
  { id: "B2", titulo: "Plan de mantenimiento como suscripción", ruta: "/propuestas/b2" },
  { id: "C1", titulo: "Tablero de la tienda", ruta: "/propuestas/c1" },
  { id: "E1", titulo: "Asistente técnico", ruta: "/propuestas/e1" },
  { id: "RT", titulo: "¿Es rentable?", ruta: "/rentabilidad" },
];

// ---------------------------------------------------------------- formato
const nf0 = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 0, maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (n: number) => (Number.isFinite(n) ? n : 0);
export const colones = (n: number) => nf0.format(num(n));
export const colones2 = (n: number) => nf2.format(num(n));
export const porcentaje = (x: number, dec = 1) =>
  `${new Intl.NumberFormat("es-CR", { minimumFractionDigits: dec, maximumFractionDigits: dec }).format(num(x) * 100)} %`;
const aFecha = (d: Date | string) => (d instanceof Date ? d : new Date(d));
export const fecha = (d: Date | string, estilo: "corta" | "larga" | "dia" = "corta") => {
  const x = aFecha(d);
  if (estilo === "larga") return new Intl.DateTimeFormat("es-CR", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(x);
  if (estilo === "dia") return new Intl.DateTimeFormat("es-CR", { weekday: "short", day: "numeric", month: "short" }).format(x).replace(/,/g, "").replace(/\./g, "");
  return new Intl.DateTimeFormat("es-CR", { day: "numeric", month: "short" }).format(x).replace(/\./g, "");
};
export const hora = (d: Date | string) => new Intl.DateTimeFormat("es-CR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(aFecha(d));
export const desdeAhora = (dias: number, hh?: number, mm?: number) => {
  const d = new Date(Date.now() + dias * 86_400_000);
  if (hh != null) d.setHours(hh, mm ?? 0, 0, 0);
  return d;
};

/* almacenamiento con respaldo en memoria: si el navegador lo bloquea, la demo sigue igual durante la visita */
const memoria = { local: new Map<string, string>(), sesion: new Map<string, string>() };
const zona = (tipo: "local" | "sesion") => {
  const area = (): Storage => (tipo === "local" ? window.localStorage : window.sessionStorage);
  const mem = memoria[tipo];
  return {
    get(k: string): string | null {
      try { const v = area().getItem(k); if (v !== null) return v; } catch { /* bloqueado */ }
      return mem.has(k) ? mem.get(k)! : null;
    },
    set(k: string, v: string): boolean {
      mem.set(k, v);
      try { area().setItem(k, v); return true; } catch { return false; }
    },
    del(k: string): void {
      mem.delete(k);
      try { area().removeItem(k); } catch { /* bloqueado */ }
    },
  };
};
export const almacen = { local: zona("local"), sesion: zona("sesion") };

// ---------------------------------------------------------------- pedidos de la sesion (contrato con mockApi)
export type EstadoDemo = {
  status: "nuevo" | "en_revision" | "pagado" | "preparando" | "listo" | "enviado" | "entregado" | "cancelado" | "reembolsado";
  paid_at?: string | null; tracking_number?: string | null; carrier?: string | null; cancel_reason?: string | null;
  history: { status: string; label: string; at: string }[];
};
export type PedidoDemo = {
  token: string; number: string; status: string; status_label: string; created_at: string; paid_at: string | null;
  total: number; subtotal: number; tax_total: number; shipping: number;
  payment_method: string; payment_kind: string; shipping_method: string | null;
  contact: { name: string | null; email: string | null };
  delivery: { type: "envio" | "retiro"; province?: string; canton?: string; district?: string };
  lines: { name: string; quantity: number; total: number; unit_price: number; tax_rate: number; shipping: boolean }[];
  proofs: { filename: string; at: string }[]; history: { status: string; label: string; at: string }[];
  tracking_number: string | null; carrier: string | null; cancel_reason: string | null;
};
const ETIQUETA_ESTADO: Record<string, string> = {
  nuevo: "Pendiente de pago", en_revision: "Comprobante en revisión", pagado: "Pagado", preparando: "Preparando", listo: "Listo para retiro",
  enviado: "Enviado", entregado: "Entregado", cancelado: "Cancelado", reembolsado: "Reembolsado",
};
const leerJson = <T,>(raw: string | null, vacio: T): T => {
  if (!raw) return vacio;
  try { const v = JSON.parse(raw); return v && typeof v === "object" ? (v as T) : vacio; } catch { return vacio; }
};
export function leerPedidosDemo(): PedidoDemo[] {
  const base = leerJson<Record<string, Record<string, unknown>>>(almacen.sesion.get(CLAVES.pedidos), {});
  const est = leerJson<Record<string, EstadoDemo>>(almacen.sesion.get(CLAVES.estados), {});
  const out: PedidoDemo[] = [];
  for (const [token, o] of Object.entries(base)) {
    if (!o || typeof o !== "object" || typeof o.number !== "string") continue;
    const e = est[token];
    const m = (e ? { ...o, ...e, status_label: ETIQUETA_ESTADO[e.status] || e.status } : o) as Record<string, unknown>;
    const n = (k: string) => Number(m[k]) || 0;
    const arr = <T,>(k: string): T[] => (Array.isArray(m[k]) ? (m[k] as T[]) : []);
    const s = (k: string) => (typeof m[k] === "string" ? (m[k] as string) : null);
    out.push({
      token, number: String(m.number), status: s("status") || "nuevo", status_label: s("status_label") || "", created_at: s("created_at") || new Date().toISOString(),
      paid_at: s("paid_at"), total: n("total"), subtotal: n("subtotal"), tax_total: n("tax_total"), shipping: n("shipping"),
      payment_method: s("payment_method") || "", payment_kind: s("payment_kind") || "", shipping_method: s("shipping_method"),
      contact: (m.contact as PedidoDemo["contact"]) || { name: null, email: null },
      delivery: (m.delivery as PedidoDemo["delivery"]) || { type: "envio" },
      lines: arr("lines"), proofs: arr("proofs"), history: arr("history"),
      tracking_number: s("tracking_number"), carrier: s("carrier"), cancel_reason: s("cancel_reason"),
    });
  }
  return out.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
}
export function escribirEstadoDemo(token: string, e: EstadoDemo): void {
  const est = leerJson<Record<string, EstadoDemo>>(almacen.sesion.get(CLAVES.estados), {});
  est[token] = e;
  almacen.sesion.set(CLAVES.estados, JSON.stringify(est));
}

// ---------------------------------------------------------------- hooks
export function usePaso(ids: readonly string[]): [indice: number, irA: (indice: number) => void] {
  const [sp, setSp] = useSearchParams();
  const actual = sp.get("paso");
  const i = actual ? ids.indexOf(actual) : 0;
  const indice = i < 0 ? 0 : i;
  const irA = useCallback((n: number) => {
    const k = Math.max(0, Math.min(ids.length - 1, n));
    const next = new URLSearchParams(sp);
    next.set("paso", ids[k]);
    setSp(next); // push: "atras" del navegador retrocede un paso
  }, [ids, sp, setSp]);
  return [indice, irA];
}

const leerInteres = (): PropuestaId[] => {
  const v = leerJson<unknown[]>(almacen.local.get(CLAVES.interes), []);
  return Array.isArray(v) ? (v.filter((x) => typeof x === "string" && IDS_PROPUESTA.includes(x)) as PropuestaId[]) : [];
};
export function useInteres() {
  const [ids, setIds] = useState<PropuestaId[]>(leerInteres);
  useEffect(() => {
    const re = () => setIds(leerInteres());
    window.addEventListener(EVENTOS.interes, re);
    window.addEventListener("storage", re);
    return () => { window.removeEventListener(EVENTOS.interes, re); window.removeEventListener("storage", re); };
  }, []);
  const escribir = useCallback((next: PropuestaId[]) => {
    const limpio = [...new Set(next)].filter((x) => IDS_PROPUESTA.includes(x));
    almacen.local.set(CLAVES.interes, JSON.stringify(limpio));
    setIds(limpio);
    window.dispatchEvent(new CustomEvent<PropuestaId[]>(EVENTOS.interes, { detail: limpio }));
  }, []);
  return useMemo(() => ({
    ids,
    tiene: (id: PropuestaId) => ids.includes(id),
    alternar: (id: PropuestaId) => escribir(ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]),
    agregar: (more: PropuestaId[]) => escribir([...ids, ...more]),
    quitar: (less: PropuestaId[]) => escribir(ids.filter((x) => !less.includes(x))),
  }), [ids, escribir]);
}

export type ProductoDemo = {
  id: number; name: string; code: string; image: string | null; display_price: number;
  path: string; availability: { state: string; label: string };
};
export function useProducto(id: number): ProductoDemo | null {
  const [p, setP] = useState<ProductoDemo | null>(null);
  useEffect(() => {
    let vivo = true;
    fetch(`/api/public/store/crimson/item/x-${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (vivo && j) setP({ id: j.id, name: j.name, code: j.code, image: j.image ?? null, display_price: Number(j.display_price) || 0, path: j.path, availability: j.availability }); })
      .catch(() => null);
    return () => { vivo = false; };
  }, [id]);
  return p;
}

// ---------------------------------------------------------------- componentes
const Sr = ({ children }: { children: ReactNode }) => <span className="ges-pp-sr">{children}</span>;

export function SelloPropuesta(p: {
  estado?: "propuesta" | "existente" | "ejemplo"; id?: string; tamano?: "normal" | "chico"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const estado = p.estado || "propuesta";
  const chico = p.tamano === "chico";
  const texto = estado === "existente" ? (chico ? "Ya está" : "Ya está en la plataforma") : estado === "ejemplo" ? "Datos de ejemplo" : chico ? "Propuesta" : "Propuesta · no incluida aún";
  return (
    <span className={`ges-pp-sello${p.className ? ` ${p.className}` : ""}`} data-estado={estado} data-tono={p.tono || "claro"} data-tamano={chico ? "chico" : "normal"}>
      {p.id && <b className="ges-pp-sello__id">{p.id}</b>}
      {estado === "existente" && <span aria-hidden="true">✓</span>}
      <span>{texto}</span>
      {chico && estado === "propuesta" && <Sr> · no incluida aún</Sr>}
      {chico && estado === "existente" && <Sr> en la plataforma</Sr>}
    </span>
  );
}

export function BotonMeInteresa(p: { id: PropuestaId; variante?: "boton" | "chip"; tono?: "claro" | "oscuro"; className?: string }) {
  const { tiene, alternar } = useInteres();
  const on = tiene(p.id);
  return (
    <button type="button" aria-pressed={on} onClick={() => alternar(p.id)}
      className={`ges-pp-interes${p.className ? ` ${p.className}` : ""}`} data-variante={p.variante || "boton"} data-tono={p.tono || "claro"}>
      {on ? <>Te interesa ✓<Sr> (marcada)</Sr></> : "Me interesa"}
    </button>
  );
}

function useReloj() {
  const fmt = () => new Date().toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const [t, setT] = useState(fmt);
  useEffect(() => { const id = window.setInterval(() => setT(fmt()), 1000); return () => window.clearInterval(id); }, []);
  return t;
}
export function BarraMonitor(p: { cam: string; rotulo: string; derecha?: ReactNode }) {
  const t = useReloj();
  return (
    <div className="ges-pp-monitor">
      <span aria-hidden="true"><i className="ges-pp-rec" />CAM {p.cam} · {p.rotulo}</span>
      <span><span aria-hidden="true" className="ges-pp-monitor__t">{t}</span>{p.derecha}</span>
    </div>
  );
}

export function NavPropuestas(p: { actual?: MaquetaId | "LQV" }) {
  const volver = almacen.sesion.get(CLAVES.ultimaRuta) || "/";
  const i = MAQUETAS.findIndex((m) => m.id === p.actual);
  const ant = i > 0 ? MAQUETAS[i - 1] : null;
  const sig = i >= 0 && i < MAQUETAS.length - 1 ? MAQUETAS[i + 1] : null;
  return (
    <nav className="ges-pp-nav" data-pp-nav aria-label="Propuestas">
      <div className="ges-pp-nav__in">
        <Link to={volver} className="ges-pp-nav__a ges-pp-nav__back">← Tienda</Link>
        <Link to="/propuestas" className="ges-pp-nav__a" aria-current={p.actual === "LQV" ? "page" : undefined}>Lo que viene</Link>
        {ant && <Link to={ant.ruta} className="ges-pp-nav__a ges-pp-nav__step" aria-label={`Maqueta anterior: ${ant.titulo}`}>‹ {ant.id}</Link>}
        {sig && <Link to={sig.ruta} className="ges-pp-nav__a ges-pp-nav__step" aria-label={`Maqueta siguiente: ${sig.titulo}`}>{sig.id} ›</Link>}
        <span className="ges-pp-nav__sp" />
        <Link to="/rentabilidad" className="ges-pp-nav__a" aria-current={p.actual === "RT" ? "page" : undefined}>¿Es rentable?</Link>
        <Link to="/detras-de-escena" className="ges-pp-nav__a" aria-current={p.actual === "DE" ? "page" : undefined}>Detrás de escena</Link>
      </div>
    </nav>
  );
}

// ---------------------------------------------------------------- pantalla (marco de dispositivo)
export type Superficie = "tienda" | "portal" | "correo" | "celular" | "admin" | "pdf";
export type SeccionAdmin = "Inicio" | "Pedidos" | "Inventarios" | "Mi Tienda" | "Levantamientos" | "Cotizaciones"
  | "Soporte" | "Mantenimientos" | "Tablero de la tienda";
type CtxMarco = { id: MaquetaId; estado: "propuesta" | "existente" };
const MarcoCtx = createContext<CtxMarco | null>(null);
const ROTULO: Record<Superficie, string> = {
  tienda: "Así lo ve el cliente", portal: "Portal del cliente", correo: "Correo al cliente",
  celular: "En el celular del cliente", admin: "Así lo ve Crimson", pdf: "Documento PDF",
};
const MENU: { s: SeccionAdmin; n: string; to?: string; prop?: boolean }[] = [
  { s: "Inicio", n: "01", to: "/detras-de-escena" }, { s: "Levantamientos", n: "04" }, { s: "Cotizaciones", n: "05" },
  { s: "Inventarios", n: "06", to: "/detras-de-escena/inventario" }, { s: "Mantenimientos", n: "08" }, { s: "Soporte", n: "09" },
  { s: "Mi Tienda", n: "10", to: "/detras-de-escena/mi-tienda" }, { s: "Pedidos", n: "11", to: "/detras-de-escena/pedidos" },
  { s: "Tablero de la tienda", n: "C1", to: "/propuestas/c1", prop: true },
];
export function Pantalla(p: {
  superficie: Superficie; rotulo?: string; direccion?: string; seccion?: SeccionAdmin; ancho?: "completo" | "telefono";
  estado?: "propuesta" | "existente"; id?: string; className?: string; children: ReactNode;
}) {
  const ctx = useContext(MarcoCtx);
  const estado = p.estado || ctx?.estado || "propuesta";
  const rotulo = p.rotulo || ROTULO[p.superficie];
  return (
    <div id={p.id} className={`ges-pp-pant${p.className ? ` ${p.className}` : ""}`} data-superficie={p.superficie} data-estado={estado} data-ancho={p.ancho || (p.superficie === "celular" ? "telefono" : "completo")}>
      <div className="ges-pp-pant__rot"><span>{rotulo}</span></div>
      <div className="ges-pp-pant__marco">
        <i className="ges-pp-esq" aria-hidden="true"><b /><b /><b /><b /></i>
        {(p.superficie === "tienda" || p.superficie === "portal") && (
          <div className="ges-pp-pant__barra" aria-hidden="true"><i /><i /><i /><span>{p.direccion || (p.superficie === "portal" ? "portal.crimsoncr.com" : "tienda.crimsoncr.com")}</span></div>
        )}
        {p.superficie === "admin" ? (
          <div className="ges-pp-adm">
            <div className="ges-pp-adm__linea">Plataforma Crimson · {p.seccion || "Inicio"}</div>
            <div className="ges-pp-adm__grid">
              <ul className="ges-pp-adm__menu" aria-label="Menú de la plataforma (decorativo)">
                {MENU.map((m) => (
                  <li key={m.s} aria-current={m.s === p.seccion ? "page" : undefined} data-prop={m.prop ? "1" : undefined}>
                    <span className="ges-pp-adm__n" aria-hidden="true">{m.n}</span>
                    {m.to && m.s !== p.seccion ? <Link to={m.to}>{m.s}</Link> : <span>{m.s}</span>}
                    {m.prop && <em>propuesta</em>}
                  </li>
                ))}
              </ul>
              <div className="ges-pp-adm__main">{p.children}</div>
            </div>
          </div>
        ) : (
          <div className="ges-pp-pant__cuerpo">{p.children}</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- marco de maqueta
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
function useAncho(q: string) {
  const [ok, setOk] = useState(() => typeof matchMedia === "function" && matchMedia(q).matches);
  useEffect(() => {
    const m = matchMedia(q);
    const on = () => setOk(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [q]);
  return ok;
}
export function MarcoMaqueta(p: MarcoMaquetaProps) {
  const estado = p.estado || (p.id === "DE" ? "existente" : "propuesta");
  const esDE = p.id === "DE";
  const ids = useMemo(() => (p.pasos || []).map((x) => x.id), [p.pasos]);
  const [i, irA] = usePaso(ids);
  const h1 = useRef<HTMLHeadingElement>(null);
  const h2 = useRef<HTMLHeadingElement>(null);
  const sec = useRef<HTMLElement>(null);
  const pasoPrevio = useRef<number | null>(null);
  const escritorio = useAncho("(min-width: 1024px)");
  const paso = p.pasos?.[i];

  useLayoutEffect(() => {
    window.scrollTo(0, 0);
    document.title = esDE ? `${p.titulo} — Detrás de escena · Tienda Crimson (vista previa)` : `${p.id} · ${p.titulo} — Propuesta · Tienda Crimson (vista previa)`;
    h1.current?.focus({ preventScroll: true });
  }, [p.id, p.titulo, esDE]);
  useEffect(() => {
    // solo cuando el paso CAMBIA (StrictMode corre los efectos dos veces al montar: no debe desplazar)
    if (pasoPrevio.current === null || pasoPrevio.current === i) { pasoPrevio.current = i; return; }
    pasoPrevio.current = i;
    sec.current?.scrollIntoView({ block: "start", behavior: "auto" });
    h2.current?.focus({ preventScroll: true });
  }, [i]);

  const k = MAQUETAS.findIndex((m) => m.id === p.id);
  const siguienteMaqueta = k >= 0 && k < MAQUETAS.length - 1 ? MAQUETAS[k + 1] : null;
  const esPropuesta = IDS_PROPUESTA.includes(p.id);
  const ctx = useMemo<CtxMarco>(() => ({ id: p.id, estado }), [p.id, estado]);

  return (
    <MarcoCtx.Provider value={ctx}>
      <div className="ges-pp ges-pp-marco" data-estado={estado}>
        <NavPropuestas actual={p.id} />
        <main id="contenido" tabIndex={-1} className="ges-pp-marco__main">
          <header className="ges-pp-cab">
            <div className="ges-pp-cab__rot">{esDE ? "Ya construido · datos de ejemplo" : `Maqueta · ${p.id} · datos de ejemplo`}</div>
            <div className="ges-pp-cab__sellos">
              <SelloPropuesta estado={estado} id={esDE ? undefined : p.id} />
              {p.etapa && <span className="ges-pp-chip">Etapa {p.etapa}</span>}
            </div>
            <h1 ref={h1} tabIndex={-1} className="ges-pp-cab__h1">{p.titulo}</h1>
            <p className="ges-pp-cab__bajada">{p.bajada}</p>
            {(p.hoy || p.conPropuesta) && (
              <div className="ges-pp-hoy">
                {p.hoy && <div className="ges-pp-hoy__a"><b><span aria-hidden="true">✓ </span>Hoy en la tienda</b><p>{p.hoy}</p></div>}
                {p.conPropuesta && <div className="ges-pp-hoy__b"><b>Con la propuesta</b><p>{p.conPropuesta}</p></div>}
              </div>
            )}
            <div className="ges-pp-cab__pie">
              {esPropuesta && <BotonMeInteresa id={p.id as PropuestaId} />}
              <span className="ges-pp-cab__aviso">{esDE ? "Pantallas que ya existen, con datos de ejemplo: nada de lo que toqués cambia nada real." : "Maqueta con datos de ejemplo: no funciona ni guarda nada real."}</span>
            </div>
          </header>

          {p.pasos && p.pasos.length > 1 && (
            <ol className="ges-pp-pasos" aria-label="Pasos de la maqueta">
              {p.pasos.map((x, n) => (
                <li key={x.id}>
                  <button type="button" aria-current={n === i ? "step" : undefined} onClick={() => irA(n)}>
                    <span className="ges-pp-pasos__n" aria-hidden="true">{n + 1}</span>
                    <span>{x.titulo}<small>{x.vista === "crimson" ? "Crimson" : "Cliente"}</small></span>
                  </button>
                </li>
              ))}
            </ol>
          )}

          <div className="ges-pp-cuerpo" data-aparte={p.aparte ? "1" : "0"}>
            <section ref={sec} className="ges-pp-escena" aria-labelledby={paso ? "ges-pp-paso-t" : undefined} aria-label={paso ? undefined : p.titulo}>
              {paso && <h2 ref={h2} tabIndex={-1} id="ges-pp-paso-t" className="ges-pp-escena__h2">{paso.titulo}</h2>}
              {p.children}
            </section>
            {p.aparte && (escritorio ? (
              <aside className="ges-pp-aparte" aria-label="Qué pasa aquí">{p.aparte}</aside>
            ) : (
              <details className="ges-pp-aparte ges-pp-aparte--m"><summary>Qué pasa aquí</summary><div>{p.aparte}</div></details>
            ))}
          </div>

          <footer className="ges-pp-pie">
            {paso && i > 0 ? <button type="button" className="ges-pp-pie__a" onClick={() => irA(i - 1)}>← {p.pasos![i - 1].titulo}</button> : <span />}
            {paso && i < (p.pasos?.length || 0) - 1 ? (
              <button type="button" className="ges-pp-pie__a ges-pp-pie__a--sig" onClick={() => irA(i + 1)}>{p.pasos![i + 1].titulo} →</button>
            ) : siguienteMaqueta ? (
              <Link className="ges-pp-pie__a ges-pp-pie__a--sig" to={siguienteMaqueta.ruta}>Siguiente maqueta: {siguienteMaqueta.titulo} →</Link>
            ) : <Link className="ges-pp-pie__a ges-pp-pie__a--sig" to="/propuestas">Ver lo que viene →</Link>}
          </footer>
        </main>
      </div>
    </MarcoCtx.Provider>
  );
}

// ---------------------------------------------------------------- hilo carmesi
export type Estacion = "compra" | "mi-pedido" | "crimson" | "portal" | "tablero" | "numeros";
export function HiloCarmesi(p: { aqui: Estacion; numero?: string; token?: string }) {
  const numero = p.numero || "ORD-000128";
  const token = p.token || "ejemplo-enviado";
  const est: { k: Estacion; t: string; to: string; tipo: "existe" | "propuesta" | "modelo" }[] = [
    { k: "compra", t: "Compra", to: "/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53", tipo: "existe" },
    { k: "mi-pedido", t: "Mi pedido", to: `/?pedido=${encodeURIComponent(token)}`, tipo: "existe" },
    { k: "crimson", t: "Crimson lo atiende", to: `/detras-de-escena/pedidos?pedido=${encodeURIComponent(numero)}`, tipo: "existe" },
    { k: "portal", t: "Portal del cliente", to: "/propuestas/b1?paso=compras", tipo: "propuesta" },
    { k: "tablero", t: "Tablero", to: "/propuestas/c1", tipo: "propuesta" },
    { k: "numeros", t: "¿Es rentable?", to: "/rentabilidad", tipo: "modelo" },
  ];
  return (
    <nav className="ges-pp-hilo" aria-label={`El hilo carmesí: el pedido ${numero} de punta a punta`}>
      <span className="ges-pp-hilo__t">El hilo carmesí · {numero}</span>
      <ol>
        {est.map((e) => (
          <li key={e.k} data-tipo={e.tipo}>
            <Link to={e.to} aria-current={e.k === p.aqui ? "step" : undefined}>
              <i aria-hidden="true" />
              <span>{e.t}</span>
              <small>{e.tipo === "existe" ? "ya existe" : e.tipo === "propuesta" ? "propuesta" : "modelo"}</small>
            </Link>
          </li>
        ))}
      </ol>
    </nav>
  );
}
