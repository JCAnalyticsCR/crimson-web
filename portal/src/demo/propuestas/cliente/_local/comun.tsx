/* COPIA LOCAL MINIMA de propuestas/_comun (equipo cliente). Solo existe mientras la capa construye _comun:
   al integrar, cliente/comun.ts pasa a `export * from "../_comun"` y esta carpeta se borra.
   Reglas: exporta SOLO nombres de la seccion 1.6 de la especificacion, con firmas identicas y comportamiento
   minimo. Las clases llevan el prefijo cli-pp- (pp- es de _comun). Nada sale a la red: useProducto pide a
   /api/public/store/crimson/..., que responde mockApi dentro del navegador. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import "./comun.css";

// ---------------------------------------------------------------- tipos y constantes (registro.ts)
export type PropuestaId = "A1" | "A2" | "A3" | "A4" | "A5" | "B1" | "B2" | "B3" | "B4" | "C1" | "C2" | "C3" | "D1" | "D2" | "E1" | "E2" | "E3";
export type ExistenteId = "H1" | "H2" | "H3" | "H4" | "H5" | "H6" | "H7" | "H8" | "H9" | "H10" | "H11" | "H12" | "H13" | "H14" | "H15" | "H16";
export type MaquetaId = PropuestaId | "DE" | "RT";
export type Etapa = 1 | 2 | 3 | 4 | 5;

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

const IDS_PROPUESTA: readonly PropuestaId[] = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "C1", "C2", "C3", "D1", "D2", "E1", "E2", "E3"];
const esPropuesta = (x: unknown): x is PropuestaId => typeof x === "string" && (IDS_PROPUESTA as readonly string[]).includes(x);

/** titulo y ruta de cada maqueta, para el pie «Siguiente maqueta» (orden de registro.ORDEN_MAQUETAS) */
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
export const colones = (n: number) => nf0.format(Number(n) || 0);
export const colones2 = (n: number) => nf2.format(Number(n) || 0);
export const porcentaje = (x: number, dec = 1) =>
  `${new Intl.NumberFormat("es-CR", { minimumFractionDigits: dec, maximumFractionDigits: dec }).format((Number(x) || 0) * 100)} %`;
const aFecha = (d: Date | string) => (d instanceof Date ? d : new Date(d));
const sinPunto = (s: string) => s.replace(/\./g, "").replace(/,/g, "").replace(/\s+/g, " ").trim();
export const fecha = (d: Date | string, estilo: "corta" | "larga" | "dia" = "corta") => {
  const x = aFecha(d);
  if (estilo === "larga") return new Intl.DateTimeFormat("es-CR", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(x);
  if (estilo === "dia") return sinPunto(new Intl.DateTimeFormat("es-CR", { weekday: "short", day: "numeric", month: "short" }).format(x));
  return sinPunto(new Intl.DateTimeFormat("es-CR", { day: "numeric", month: "short" }).format(x));
};
export const hora = (d: Date | string) => new Intl.DateTimeFormat("es-CR", { hour: "2-digit", minute: "2-digit", hour12: false }).format(aFecha(d));
export const desdeAhora = (dias: number, hh?: number, mm?: number) => {
  const d = new Date(Date.now() + dias * 86400000);
  if (hh !== undefined) d.setHours(hh, mm ?? 0, 0, 0);
  return d;
};

function caja(tipo: "local" | "sesion") {
  const s = (): Storage | null => { try { return tipo === "local" ? window.localStorage : window.sessionStorage; } catch { return null; } };
  return {
    get(k: string): string | null { try { return s()?.getItem(k) ?? null; } catch { return null; } },
    set(k: string, v: string): boolean { try { const x = s(); if (!x) return false; x.setItem(k, v); return x.getItem(k) === v; } catch { return false; } },
    del(k: string): void { try { s()?.removeItem(k); } catch { /* sin almacenamiento */ } },
  };
}
export const almacen = { local: caja("local"), sesion: caja("sesion") };

// ---------------------------------------------------------------- hooks
export function usePaso(ids: readonly string[]): [indice: number, irA: (indice: number) => void] {
  const [sp, setSp] = useSearchParams();
  const i = Math.max(0, ids.indexOf(sp.get("paso") ?? ""));
  const irA = useCallback((n: number) => {
    const k = Math.max(0, Math.min(ids.length - 1, n));
    setSp((prev) => { const p = new URLSearchParams(prev); p.set("paso", ids[k]); return p; }); // push: «atras» retrocede un paso
  }, [ids, setSp]);
  return [i, irA];
}

let memInteres: PropuestaId[] | null = null;
function leerInteres(): PropuestaId[] {
  const raw = almacen.local.get(CLAVES.interes);
  if (raw == null) return memInteres ?? [];
  try { const a: unknown = JSON.parse(raw); return Array.isArray(a) ? a.filter(esPropuesta) : []; } catch { return []; }
}
function escribirInteres(ids: PropuestaId[]) {
  memInteres = ids;
  almacen.local.set(CLAVES.interes, JSON.stringify(ids));
  window.dispatchEvent(new CustomEvent<PropuestaId[]>(EVENTOS.interes, { detail: ids }));
}
export function useInteres() {
  const [ids, setIds] = useState<PropuestaId[]>(leerInteres);
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent<unknown>).detail; setIds(Array.isArray(d) ? d.filter(esPropuesta) : leerInteres()); };
    const st = (e: StorageEvent) => { if (e.key === null || e.key === CLAVES.interes) setIds(leerInteres()); };
    window.addEventListener(EVENTOS.interes, on);
    window.addEventListener("storage", st);
    return () => { window.removeEventListener(EVENTOS.interes, on); window.removeEventListener("storage", st); };
  }, []);
  const tiene = useCallback((id: PropuestaId) => ids.includes(id), [ids]);
  const alternar = useCallback((id: PropuestaId) => { const cur = leerInteres(); escribirInteres(cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]); }, []);
  const agregar = useCallback((xs: PropuestaId[]) => { const cur = leerInteres(); escribirInteres([...cur, ...xs.filter((x) => esPropuesta(x) && !cur.includes(x))]); }, []);
  const quitar = useCallback((xs: PropuestaId[]) => escribirInteres(leerInteres().filter((x) => !xs.includes(x))), []);
  return { ids, tiene, alternar, agregar, quitar };
}

export type ProductoDemo = {
  id: number; name: string; code: string; image: string | null; display_price: number;
  path: string; availability: { state: string; label: string };
};
const cacheProd = new Map<number, ProductoDemo>();
export function useProducto(id: number): ProductoDemo | null {
  const [p, setP] = useState<ProductoDemo | null>(() => cacheProd.get(id) ?? null);
  useEffect(() => {
    const ya = cacheProd.get(id);
    if (ya) { setP(ya); return; }
    let vivo = true;
    fetch(`/api/public/store/crimson/item/x-${id}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j: Record<string, unknown> | null) => {
        if (!j) { if (vivo) setP(null); return; }
        const v: ProductoDemo = {
          id: Number(j.id), name: String(j.name ?? ""), code: String(j.code ?? ""), image: typeof j.image === "string" ? j.image : null,
          display_price: Number(j.display_price) || 0, path: String(j.path ?? ""),
          availability: (j.availability as ProductoDemo["availability"]) ?? { state: "", label: "" },
        };
        cacheProd.set(id, v);
        if (vivo) setP(v);
      })
      .catch(() => { if (vivo) setP(null); });
    return () => { vivo = false; };
  }, [id]);
  return p;
}

// ---------------------------------------------------------------- datos de la sesion (contrato con mockApi)
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
const ETIQUETA: Record<string, string> = {
  nuevo: "Pendiente de pago", en_revision: "Comprobante en revisión", pagado: "Pagado", preparando: "Preparando", listo: "Listo para retiro",
  enviado: "Enviado", entregado: "Entregado", cancelado: "Cancelado", reembolsado: "Reembolsado",
};
function leerObjeto(k: string): Record<string, unknown> {
  try { const v: unknown = JSON.parse(almacen.sesion.get(k) || "{}"); return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {}; } catch { return {}; }
}
export function leerPedidosDemo(): PedidoDemo[] {
  const pedidos = leerObjeto(CLAVES.pedidos);
  const estados = leerObjeto(CLAVES.estados) as Record<string, EstadoDemo | undefined>;
  return Object.values(pedidos)
    .filter((o): o is PedidoDemo => !!o && typeof o === "object" && typeof (o as PedidoDemo).token === "string" && typeof (o as PedidoDemo).number === "string")
    .map((o) => { const e = estados[o.token]; return e && e.status ? { ...o, ...e, status_label: ETIQUETA[e.status] ?? o.status_label } : o; })
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

// ---------------------------------------------------------------- componentes
const Sr = ({ children }: { children: ReactNode }) => <span className="cli-pp-sr">{children}</span>;

export function SelloPropuesta({ estado = "propuesta", id, tamano = "normal", tono = "claro", className = "" }: {
  estado?: "propuesta" | "existente" | "ejemplo"; id?: string; tamano?: "normal" | "chico"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const txt = estado === "existente" ? (tamano === "chico" ? "Ya está" : "Ya está en la plataforma") : estado === "ejemplo" ? "Datos de ejemplo" : "Propuesta";
  return (
    <span className={`cli-pp-sello cli-pp-sello--${estado} cli-pp-sello--${tamano} cli-pp-sello--${tono} ${className}`.trim()}>
      {id && <b className="cli-pp-sello__id">{id}</b>}
      <span className="cli-pp-sello__t">
        {estado === "existente" && <span aria-hidden="true">✓ </span>}
        {txt}
        {estado === "propuesta" && (tamano === "chico" ? <Sr> · no incluida aún</Sr> : " · no incluida aún")}
      </span>
    </span>
  );
}

export function BotonMeInteresa({ id, variante = "boton", tono = "claro", className = "" }: {
  id: PropuestaId; variante?: "boton" | "chip"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const { tiene, alternar } = useInteres();
  const on = tiene(id);
  return (
    <button type="button" aria-pressed={on} onClick={() => alternar(id)} className={`cli-pp-interes cli-pp-interes--${variante} cli-pp-interes--${tono} ${className}`.trim()}>
      {on ? <>Te interesa ✓<Sr> (marcada)</Sr></> : "Me interesa"}
    </button>
  );
}

export function BarraMonitor({ cam, rotulo, derecha }: { cam: string; rotulo: string; derecha?: ReactNode }) {
  const fmt = () => new Date().toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const [t, setT] = useState(fmt);
  useEffect(() => { const k = window.setInterval(() => setT(fmt()), 1000); return () => window.clearInterval(k); }, []);
  return (
    <div className="cli-pp-monitor">
      <span aria-hidden="true">● CAM {cam} · {rotulo}</span>
      <span><span aria-hidden="true">{t}</span>{derecha}</span>
    </div>
  );
}

export function NavPropuestas({ actual }: { actual?: MaquetaId | "LQV" }) {
  const tienda = almacen.sesion.get(CLAVES.ultimaRuta) || "/";
  const yo = MAQUETAS.find((m) => m.id === actual);
  return (
    <nav className="cli-pp-nav" data-pp-nav aria-label="Propuestas">
      <div className="cli-pp-nav__in">
        <Link to={tienda} className="cli-pp-nav__back">← Tienda</Link>
        {yo && <span className="cli-pp-nav__yo" aria-hidden="true">{yo.id} · {yo.titulo}</span>}
        <span className="cli-pp-nav__links">
          <Link to="/propuestas" aria-current={actual === "LQV" ? "page" : undefined}>Lo que viene</Link>
          <Link to="/rentabilidad" aria-current={actual === "RT" ? "page" : undefined}>¿Es rentable?</Link>
          <Link to="/detras-de-escena" aria-current={actual === "DE" ? "page" : undefined}>Detrás de escena</Link>
        </span>
      </div>
    </nav>
  );
}

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
type MarcoInfo = { id: MaquetaId; estado: "propuesta" | "existente" };
const MarcoCtx = createContext<MarcoInfo | null>(null);
const ETAPA_TITULO: Record<Etapa, string> = { 1: "Cada venta abre la siguiente", 2: "Ingreso que se repite", 3: "Recuperar y fidelizar", 4: "Empresas y paquetes", 5: "Proveedor conectado" };

export function MarcoMaqueta({ id, titulo, bajada, estado, etapa, hoy, conPropuesta, pasos, aparte, children }: MarcoMaquetaProps) {
  const est = estado ?? (id === "DE" ? "existente" : "propuesta");
  const ids = useMemo(() => (pasos ?? []).map((p) => p.id), [pasos]);
  const [i, irA] = usePaso(ids);
  const h1 = useRef<HTMLHeadingElement>(null);
  const h2 = useRef<HTMLHeadingElement>(null);
  const pasoVisto = useRef(i);
  const esProp = est === "propuesta" && id !== "DE" && id !== "RT";

  useEffect(() => {
    window.scrollTo(0, 0);
    document.title = id === "DE" ? `${titulo} — Detrás de escena · Tienda Crimson (vista previa)` : `${id} · ${titulo} — Propuesta · Tienda Crimson (vista previa)`;
    h1.current?.focus({ preventScroll: true });
  }, [id, titulo]);
  useEffect(() => {
    if (pasoVisto.current === i) return; // al montar (y en el doble montaje de StrictMode) el foco queda en el h1
    pasoVisto.current = i;
    h2.current?.focus();
  }, [i]);

  const paso = pasos?.[i];
  const idx = MAQUETAS.findIndex((m) => m.id === id);
  const sig = idx >= 0 ? MAQUETAS[(idx + 1) % MAQUETAS.length] : null;
  const ctx = useMemo<MarcoInfo>(() => ({ id, estado: est }), [id, est]);
  const ver = (n: number) => (e: MouseEvent) => { e.preventDefault(); irA(n); };

  return (
    <MarcoCtx.Provider value={ctx}>
      <div className="cli-pp cli-pp-marco" data-estado={est}>
        <NavPropuestas actual={id} />
        <main id="contenido" tabIndex={-1} className="cli-pp-marco__main">
          <header className="cli-pp-marco__head">
            <p className="cli-pp-marco__rot">{est === "existente" ? "YA CONSTRUIDO · DATOS DE EJEMPLO" : `MAQUETA · ${id} · DATOS DE EJEMPLO`}</p>
            <div className="cli-pp-marco__chips">
              <SelloPropuesta estado={est === "existente" ? "existente" : "propuesta"} id={esProp ? id : undefined} />
              {etapa && <span className="cli-pp-marco__etapa">Etapa {etapa} · {ETAPA_TITULO[etapa]}</span>}
            </div>
            <h1 ref={h1} tabIndex={-1} className="cli-pp-marco__h1">{titulo}</h1>
            <p className="cli-pp-marco__bajada">{bajada}</p>
            {(hoy || conPropuesta) && (
              <div className="cli-pp-marco__comp">
                {hoy && <div className="cli-pp-marco__hoy"><span className="cli-pp-marco__k">✓ Hoy en la tienda</span><p>{hoy}</p></div>}
                {conPropuesta && <div className="cli-pp-marco__con"><span className="cli-pp-marco__k">Con la propuesta</span><p>{conPropuesta}</p></div>}
              </div>
            )}
            <div className="cli-pp-marco__acts">
              {esProp && <BotonMeInteresa id={id as PropuestaId} />}
              <p className="cli-pp-marco__aviso">Maqueta con datos de ejemplo: no funciona ni guarda nada real.</p>
            </div>
          </header>

          {pasos && pasos.length > 0 && (
            <ol className="cli-pp-pasos" aria-label="Pasos de la maqueta">
              {pasos.map((p, n) => (
                <li key={p.id} aria-current={n === i ? "step" : undefined} data-hecho={n < i}>
                  <a href={`#paso-${p.id}`} onClick={ver(n)}>
                    <span className="cli-pp-pasos__n">{n + 1}</span>
                    <span className="cli-pp-pasos__t">{p.titulo}<small>{p.vista === "crimson" ? "Así lo ve Crimson" : "Así lo ve el cliente"}</small></span>
                  </a>
                </li>
              ))}
            </ol>
          )}

          <div className={`cli-pp-marco__grid${aparte ? " cli-pp-marco__grid--aparte" : ""}`}>
            {aparte && <details className="cli-pp-marco__aparte-m"><summary>Qué pasa aquí</summary><div>{aparte}</div></details>}
            <section className="cli-pp-marco__escena" aria-labelledby="cli-pp-paso-t">
              <h2 id="cli-pp-paso-t" ref={h2} tabIndex={-1} className="cli-pp-marco__h2">{paso ? paso.titulo : titulo}</h2>
              {children}
            </section>
            {aparte && <aside className="cli-pp-marco__aparte" aria-label="Qué pasa aquí">{aparte}</aside>}
          </div>

          <footer className="cli-pp-marco__pie">
            {pasos && i > 0 ? <a href={`#paso-${pasos[i - 1].id}`} onClick={ver(i - 1)}>← {pasos[i - 1].titulo}</a> : <span />}
            {pasos && i < pasos.length - 1
              ? <a href={`#paso-${pasos[i + 1].id}`} onClick={ver(i + 1)}>{pasos[i + 1].titulo} →</a>
              : sig && <Link to={sig.ruta}>Siguiente maqueta: {sig.titulo} →</Link>}
          </footer>
        </main>
      </div>
    </MarcoCtx.Provider>
  );
}

export type Superficie = "tienda" | "portal" | "correo" | "celular" | "admin" | "pdf";
export type SeccionAdmin = "Inicio" | "Pedidos" | "Inventarios" | "Mi Tienda" | "Levantamientos" | "Cotizaciones"
  | "Soporte" | "Mantenimientos" | "Tablero de la tienda";
const ROTULO: Record<Superficie, string> = {
  tienda: "Así lo ve el cliente", portal: "Portal del cliente", correo: "Correo al cliente",
  celular: "En el celular del cliente", admin: "Así lo ve Crimson", pdf: "Documento PDF",
};
const MENU_ADMIN: SeccionAdmin[] = ["Inicio", "Pedidos", "Inventarios", "Mi Tienda", "Levantamientos", "Cotizaciones", "Soporte", "Mantenimientos", "Tablero de la tienda"];

export function Pantalla({ superficie, rotulo, direccion, seccion, ancho = "completo", estado, id, className = "", children }: {
  superficie: Superficie; rotulo?: string; direccion?: string; seccion?: SeccionAdmin; ancho?: "completo" | "telefono";
  estado?: "propuesta" | "existente"; id?: string; className?: string; children: ReactNode;
}) {
  const ctx = useContext(MarcoCtx);
  const est = estado ?? ctx?.estado ?? "propuesta";
  return (
    <div id={id} className={`cli-pp-pant cli-pp-pant--${superficie}${ancho === "telefono" ? " cli-pp-pant--tel" : ""} ${className}`.trim()} data-estado={est}>
      <div className="cli-pp-pant__bar">
        <span className="cli-pp-pant__rot">{rotulo ?? ROTULO[superficie]}</span>
        <span className="cli-pp-pant__est" aria-hidden="true">● {est === "existente" ? "Plataforma Crimson · datos de ejemplo" : `Maqueta${ctx ? ` · ${ctx.id}` : ""} · datos de ejemplo`}</span>
      </div>
      <span className="cli-pp-pant__esq" aria-hidden="true"><i /><i /><i /><i /></span>
      {direccion && (superficie === "tienda" || superficie === "portal") && (
        <div className="cli-pp-pant__url" aria-hidden="true"><i /><i /><i /><span>{direccion}</span></div>
      )}
      {superficie === "admin" ? (
        <div className="cli-pp-pant__admin">
          <ul className="cli-pp-pant__menu" aria-hidden="true">
            <li className="cli-pp-pant__menu-marca">Crimson</li>
            {MENU_ADMIN.map((s) => <li key={s} data-on={s === seccion}>{s}</li>)}
          </ul>
          <div className="cli-pp-pant__adm-main">
            <p className="cli-pp-pant__linea">Plataforma Crimson{seccion ? ` · ${seccion}` : ""}</p>
            {children}
          </div>
        </div>
      ) : superficie === "celular" ? (
        <div className="cli-pp-pant__cel"><div className="cli-pp-pant__cel-in">{children}</div></div>
      ) : superficie === "pdf" ? (
        <div className="cli-pp-pant__pdf">{children}</div>
      ) : (
        <div className="cli-pp-pant__cuerpo">{children}</div>
      )}
    </div>
  );
}

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
  const TIPO = { existe: "ya existe", propuesta: "propuesta", modelo: "modelo con supuestos" };
  return (
    <nav className="cli-pp-hilo" aria-label={`El hilo carmesí: el pedido ${numero} visto desde todos lados`}>
      <p className="cli-pp-hilo__t">El hilo carmesí · {numero}</p>
      <ol>
        {est.map((e) => (
          <li key={e.k} data-tipo={e.tipo} aria-current={e.k === aqui ? "step" : undefined}>
            <Link to={e.to}><i aria-hidden="true" /><span>{e.t}<small>{e.k === aqui ? "Estás aquí" : TIPO[e.tipo]}</small></span></Link>
          </li>
        ))}
      </ol>
    </nav>
  );
}
