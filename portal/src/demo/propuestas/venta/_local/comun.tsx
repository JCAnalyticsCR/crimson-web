/* COPIA LOCAL de propuestas/_comun para el equipo venta (secciones 1.4 y 1.6 de la especificacion).
   Exporta solo un SUBCONJUNTO de la API comun, con las mismas firmas y un comportamiento minimo, para construir y
   probar las maquetas sin esperar a la capa. Al integrar, la capa cambia ../comun.ts por
   `export * from "../_comun";` y borra esta carpeta: ningun otro archivo del equipo cambia.
   Las clases llevan el prefijo ven-pp- ("pp-" queda reservado a la comun). Nada sale a la red. */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import "./comun.css";

// ---------- tipos (estructuralmente identicos a registro.ts) ----------
export type PropuestaId = "A1" | "A2" | "A3" | "A4" | "A5" | "B1" | "B2" | "B3" | "B4" | "C1" | "C2" | "C3" | "D1" | "D2" | "E1" | "E2" | "E3";
export type ExistenteId = "H1" | "H2" | "H3" | "H4" | "H5" | "H6" | "H7" | "H8" | "H9" | "H10" | "H11" | "H12" | "H13" | "H14" | "H15" | "H16";
export type MaquetaId = PropuestaId | "DE" | "RT";
export type Etapa = 1 | 2 | 3 | 4 | 5;

const PROPUESTAS: readonly PropuestaId[] = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "C1", "C2", "C3", "D1", "D2", "E1", "E2", "E3"];
const esPropuesta = (x: unknown): x is PropuestaId => typeof x === "string" && (PROPUESTAS as readonly string[]).includes(x);
const CLAVE_INTERES = "crimson-demo:interes";
const EVENTO_INTERES = "crimson-demo:interes";
const CLAVE_ULTIMA_RUTA = "crimson-demo:ultima-ruta";
const MAQUETAS: Record<string, { titulo: string; ruta: string }> = {
  A1: { titulo: "Instalación con fecha y precio estimado", ruta: "/propuestas/a1" },
  A3: { titulo: "Cotización formal para empresas", ruta: "/propuestas/a3" },
  B1: { titulo: "La compra abre el portal del cliente", ruta: "/propuestas/b1" },
  B2: { titulo: "Plan de mantenimiento como suscripción", ruta: "/propuestas/b2" },
  C1: { titulo: "Tablero de la tienda", ruta: "/propuestas/c1" },
  E1: { titulo: "Asistente técnico", ruta: "/propuestas/e1" },
  RT: { titulo: "¿Es rentable?", ruta: "/rentabilidad" },
};
const ORDEN_MAQUETAS: readonly MaquetaId[] = ["A1", "A3", "B1", "B2", "C1", "E1", "RT"];
const ETAPAS: Record<Etapa, string> = { 1: "Cada venta abre la siguiente", 2: "Ingreso que se repite", 3: "Recuperar y fidelizar", 4: "Empresas y paquetes", 5: "Proveedor conectado" };

// ---------- formato ----------
const nf0 = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 0, maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const colones = (n: number) => nf0.format(n);
export const colones2 = (n: number) => nf2.format(n);
export const porcentaje = (x: number, dec = 1) =>
  `${new Intl.NumberFormat("es-CR", { minimumFractionDigits: dec, maximumFractionDigits: dec }).format(x * 100)} %`;
const aFecha = (d: Date | string) => (typeof d === "string" ? new Date(d) : d);
export const fecha = (d: Date | string, estilo: "corta" | "larga" | "dia" = "corta") => {
  const x = aFecha(d);
  if (estilo === "larga") return new Intl.DateTimeFormat("es-CR", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(x);
  if (estilo === "dia") return new Intl.DateTimeFormat("es-CR", { weekday: "short", day: "numeric", month: "short" }).format(x).replace(",", "").replace(/\./g, "");
  return new Intl.DateTimeFormat("es-CR", { day: "numeric", month: "short" }).format(x).replace(/\./g, "");
};
export const hora = (d: Date | string) => new Intl.DateTimeFormat("es-CR", { hour: "2-digit", minute: "2-digit", hour12: false }).format(aFecha(d));
export const desdeAhora = (dias: number, hh?: number, mm?: number) => {
  const d = new Date(Date.now() + dias * 86_400_000);
  if (hh !== undefined) d.setHours(hh, mm ?? 0, 0, 0);
  return d;
};
type Caja = { get(k: string): string | null; set(k: string, v: string): boolean; del(k: string): void };
const caja = (s: () => Storage): Caja => ({
  get(k) { try { return s().getItem(k); } catch { return null; } },
  set(k, v) { try { s().setItem(k, v); return s().getItem(k) === v; } catch { return false; } },
  del(k) { try { s().removeItem(k); } catch { /* sin almacenamiento: no hay nada que borrar */ } },
});
export const almacen = { local: caja(() => localStorage), sesion: caja(() => sessionStorage) };

// ---------- hooks ----------
export function usePaso(ids: readonly string[]): [indice: number, irA: (indice: number) => void] {
  const [sp, setSp] = useSearchParams();
  const actual = sp.get("paso");
  const i = actual ? Math.max(0, ids.indexOf(actual)) : 0;
  const irA = useCallback((n: number) => {
    if (!ids.length) return;
    const k = Math.max(0, Math.min(ids.length - 1, n));
    // push (sin replace): "atras" del navegador retrocede un paso
    setSp((prev) => { const q = new URLSearchParams(prev); q.set("paso", ids[k]); return q; });
  }, [ids, setSp]);
  return [i, irA];
}

let interesMemoria: PropuestaId[] | null = null; // si el almacenamiento falla, la seleccion vive en memoria
const leerInteres = (): PropuestaId[] => {
  if (interesMemoria) return interesMemoria;
  try {
    const v: unknown = JSON.parse(almacen.local.get(CLAVE_INTERES) || "[]");
    return Array.isArray(v) ? v.filter(esPropuesta) : [];
  } catch { return []; }
};
export function useInteres(): {
  ids: PropuestaId[]; tiene: (id: PropuestaId) => boolean; alternar: (id: PropuestaId) => void;
  agregar: (ids: PropuestaId[]) => void; quitar: (ids: PropuestaId[]) => void;
} {
  const [ids, setIds] = useState<PropuestaId[]>(leerInteres);
  useEffect(() => {
    const onEvento = (e: Event) => { const d = (e as CustomEvent<unknown>).detail; if (Array.isArray(d)) setIds(d.filter(esPropuesta)); };
    const onStorage = (e: StorageEvent) => { if (e.key === CLAVE_INTERES) { interesMemoria = null; setIds(leerInteres()); } };
    window.addEventListener(EVENTO_INTERES, onEvento);
    window.addEventListener("storage", onStorage);
    return () => { window.removeEventListener(EVENTO_INTERES, onEvento); window.removeEventListener("storage", onStorage); };
  }, []);
  const escribir = useCallback((next: PropuestaId[]) => {
    const limpio = [...new Set(next.filter(esPropuesta))];
    interesMemoria = limpio;
    almacen.local.set(CLAVE_INTERES, JSON.stringify(limpio));
    window.dispatchEvent(new CustomEvent<PropuestaId[]>(EVENTO_INTERES, { detail: limpio }));
  }, []);
  return {
    ids,
    tiene: (id) => ids.includes(id),
    alternar: (id) => escribir(leerInteres().includes(id) ? leerInteres().filter((x) => x !== id) : [...leerInteres(), id]),
    agregar: (xs) => escribir([...leerInteres(), ...xs]),
    quitar: (xs) => escribir(leerInteres().filter((x) => !xs.includes(x))),
  };
}

export type ProductoDemo = {
  id: number; name: string; code: string; image: string | null; display_price: number;
  path: string; availability: { state: string; label: string };
};
export function useProducto(id: number): ProductoDemo | null {
  const [p, setP] = useState<ProductoDemo | null>(null);
  useEffect(() => {
    let vivo = true;
    fetch(`/api/public/store/crimson/item/x-${id}`) // lo responde mockApi: nada sale a la red
      .then((r) => (r.ok ? r.json() : null))
      .then((j: Partial<ProductoDemo> | null) => {
        if (!vivo || !j) return;
        setP({
          id: Number(j.id), name: String(j.name || ""), code: String(j.code || ""), image: j.image || null,
          display_price: Number(j.display_price) || 0, path: String(j.path || ""),
          availability: { state: String(j.availability?.state || ""), label: String(j.availability?.label || "") },
        });
      })
      .catch(() => { if (vivo) setP(null); });
    return () => { vivo = false; };
  }, [id]);
  return p;
}

function useMedia(q: string): boolean {
  const sub = useCallback((cb: () => void) => {
    const m = matchMedia(q);
    m.addEventListener("change", cb);
    return () => m.removeEventListener("change", cb);
  }, [q]);
  return useSyncExternalStore(sub, () => matchMedia(q).matches, () => false);
}

// ---------- componentes ----------
export function SelloPropuesta(p: {
  estado?: "propuesta" | "existente" | "ejemplo"; id?: string; tamano?: "normal" | "chico"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const estado = p.estado ?? "propuesta";
  const chico = p.tamano === "chico";
  const cls = `ven-pp-sello ven-pp-sello--${estado}${chico ? " ven-pp-sello--chico" : ""}${p.tono === "oscuro" ? " ven-pp-sello--oscuro" : ""}${p.className ? ` ${p.className}` : ""}`;
  return (
    <span className={cls}>
      {p.id && <b className="ven-pp-sello__id">{p.id}</b>}
      {estado === "propuesta" && (chico ? <span>Propuesta<span className="ven-pp-sr"> · no incluida aún</span></span> : <span>Propuesta · no incluida aún</span>)}
      {estado === "existente" && <span><span aria-hidden="true">✓ </span>{chico ? "Ya está" : "Ya está en la plataforma"}</span>}
      {estado === "ejemplo" && <span>Datos de ejemplo</span>}
    </span>
  );
}

export function BotonMeInteresa(p: { id: PropuestaId; variante?: "boton" | "chip"; tono?: "claro" | "oscuro"; className?: string }) {
  const { tiene, alternar } = useInteres();
  const on = tiene(p.id);
  return (
    <button type="button" aria-pressed={on} onClick={() => alternar(p.id)}
      className={`ven-pp-interes ven-pp-interes--${p.variante ?? "boton"}${p.tono === "oscuro" ? " ven-pp-interes--oscuro" : ""}${p.className ? ` ${p.className}` : ""}`}>
      {on ? <>Te interesa <span aria-hidden="true">✓</span><span className="ven-pp-sr"> (marcada)</span></> : "Me interesa"}
    </button>
  );
}

export function NavPropuestas(p: { actual?: MaquetaId | "LQV" }) {
  const volver = almacen.sesion.get(CLAVE_ULTIMA_RUTA) || "/";
  const k = p.actual && p.actual !== "LQV" ? ORDEN_MAQUETAS.indexOf(p.actual) : -1;
  const ant = k > 0 ? ORDEN_MAQUETAS[k - 1] : null;
  const sig = k >= 0 && k < ORDEN_MAQUETAS.length - 1 ? ORDEN_MAQUETAS[k + 1] : null;
  const titulo = p.actual === "LQV" ? "Lo que viene" : p.actual ? MAQUETAS[p.actual]?.titulo ?? "Propuestas" : "Propuestas";
  return (
    <nav className="ven-pp-nav" data-pp-nav aria-label="Propuestas">
      <div className="ven-pp-nav__in">
        <Link className="ven-pp-nav__a" to={volver}><span aria-hidden="true">← </span>Tienda</Link>
        <span className="ven-pp-nav__t" aria-hidden="true">{titulo}</span>
        <Link className="ven-pp-nav__a ven-pp-nav__ancho" to="/propuestas" aria-current={p.actual === "LQV" ? "page" : undefined}>Lo que viene</Link>
        <span className="ven-pp-nav__flechas">
          {ant ? <Link className="ven-pp-nav__f" to={MAQUETAS[ant].ruta} aria-label={`Maqueta anterior: ${MAQUETAS[ant].titulo}`}>‹</Link> : <span className="ven-pp-nav__f" aria-hidden="true" />}
          {sig ? <Link className="ven-pp-nav__f" to={MAQUETAS[sig].ruta} aria-label={`Maqueta siguiente: ${MAQUETAS[sig].titulo}`}>›</Link> : <span className="ven-pp-nav__f" aria-hidden="true" />}
        </span>
        <Link className="ven-pp-nav__a ven-pp-nav__ancho" to="/rentabilidad">¿Es rentable?</Link>
        <Link className="ven-pp-nav__a ven-pp-nav__ancho" to="/detras-de-escena">Detrás de escena</Link>
        <Link className="ven-pp-nav__a ven-pp-nav__movil" to="/propuestas">Todas</Link>
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
const SIN_PASOS: readonly string[] = [];

export function MarcoMaqueta(p: MarcoMaquetaProps) {
  const estado = p.estado ?? (p.id === "DE" ? "existente" : "propuesta");
  const pasos = p.pasos ?? [];
  const ids = useIdsEstables(p.pasos);
  const [i, irA] = usePaso(ids);
  const ancho = useMedia("(min-width: 1024px)");
  const h1 = useRef<HTMLHeadingElement>(null);
  const h2 = useRef<HTMLHeadingElement>(null);
  const escena = useRef<HTMLElement>(null);
  const previo = useRef(i);

  useEffect(() => {
    window.scrollTo(0, 0);
    document.title = p.id === "DE"
      ? `${p.titulo} — Detrás de escena · Tienda Crimson (vista previa)`
      : `${p.id} · ${p.titulo} — Propuesta · Tienda Crimson (vista previa)`;
    h1.current?.focus({ preventScroll: true });
  }, [p.id, p.titulo]);
  useEffect(() => {
    if (previo.current === i) return;
    previo.current = i;
    const el = escena.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start", behavior: "auto" });
    h2.current?.focus({ preventScroll: true });
  }, [i]);

  const paso = pasos[i];
  const k = ORDEN_MAQUETAS.indexOf(p.id);
  const sigMaqueta = k >= 0 && k < ORDEN_MAQUETAS.length - 1 ? MAQUETAS[ORDEN_MAQUETAS[k + 1]] : null;
  const rotulo = p.id === "DE" ? "YA CONSTRUIDO · DATOS DE EJEMPLO" : `MAQUETA · ${p.id} · DATOS DE EJEMPLO`;

  return (
    <div className="ven-pp ven-pp-marco" data-estado={estado}>
      <NavPropuestas actual={p.id} />
      <main id="contenido" tabIndex={-1} className="ven-pp-main">
        <header className="ven-pp-cab">
          <p className="ven-pp-cab__rotulo"><i aria-hidden="true" />{rotulo}</p>
          <div className="ven-pp-cab__sellos">
            <SelloPropuesta estado={estado} id={p.id !== "DE" && p.id !== "RT" ? p.id : undefined} />
            {p.etapa && <span className="ven-pp-cab__etapa">Etapa {p.etapa} · {ETAPAS[p.etapa]}</span>}
          </div>
          <h1 ref={h1} tabIndex={-1} className="ven-pp-cab__h1">{p.titulo}</h1>
          <p className="ven-pp-cab__bajada">{p.bajada}</p>
          {(p.hoy || p.conPropuesta) && (
            <div className="ven-pp-hoycon">
              {p.hoy && <div className="ven-pp-hoycon__hoy"><b><span aria-hidden="true">✓ </span>Hoy en la tienda</b><p>{p.hoy}</p></div>}
              {p.conPropuesta && <div className="ven-pp-hoycon__con"><b>Con la propuesta</b><p>{p.conPropuesta}</p></div>}
            </div>
          )}
          <div className="ven-pp-cab__pie">
            {esPropuesta(p.id) && <BotonMeInteresa id={p.id} />}
            <p className="ven-pp-cab__aviso">Maqueta con datos de ejemplo: no funciona ni guarda nada real.</p>
          </div>
        </header>

        {pasos.length > 0 && (
          <ol className="ven-pp-pasos" aria-label="Pasos de la maqueta">
            {pasos.map((x, n) => (
              <li key={x.id}>
                <button type="button" className="ven-pp-pasos__b" aria-current={n === i ? "step" : undefined} onClick={() => irA(n)}>
                  <i aria-hidden="true">{String(n + 1).padStart(2, "0")}</i>
                  <span><b>{x.titulo}</b><small>{x.vista === "crimson" ? "Crimson" : "Cliente"}</small></span>
                </button>
              </li>
            ))}
          </ol>
        )}

        <div className={`ven-pp-cuerpo${p.aparte && ancho ? " ven-pp-cuerpo--aparte" : ""}`}>
          <section ref={escena} className="ven-pp-escena" aria-labelledby={paso ? "ven-pp-paso-h" : undefined} aria-label={paso ? undefined : p.titulo}>
            {paso && <h2 id="ven-pp-paso-h" ref={h2} tabIndex={-1} className="ven-pp-escena__h2"><span className="ven-pp-escena__n">Paso {i + 1} de {pasos.length} · {paso.vista === "crimson" ? "Así lo ve Crimson" : "Así lo ve el cliente"}</span>{paso.titulo}</h2>}
            {p.children}
          </section>
          {p.aparte && (ancho
            ? <aside className="ven-pp-aparte" aria-label="Qué pasa aquí">{p.aparte}</aside>
            : <details className="ven-pp-aparte ven-pp-aparte--m"><summary>Qué pasa aquí</summary>{p.aparte}</details>)}
        </div>

        {pasos.length > 0 && (
          <nav className="ven-pp-pie" aria-label="Paso anterior y siguiente">
            {i > 0 ? <button type="button" className="ven-pp-pie__b" onClick={() => irA(i - 1)}><span aria-hidden="true">← </span>{pasos[i - 1].titulo}</button> : <span />}
            {i < pasos.length - 1
              ? <button type="button" className="ven-pp-pie__b ven-pp-pie__b--sig" onClick={() => irA(i + 1)}>{pasos[i + 1].titulo}<span aria-hidden="true"> →</span></button>
              : sigMaqueta && <Link className="ven-pp-pie__b ven-pp-pie__b--sig" to={sigMaqueta.ruta}>Siguiente maqueta: {sigMaqueta.titulo}<span aria-hidden="true"> →</span></Link>}
          </nav>
        )}
      </main>
    </div>
  );
}
/** ids de los pasos con identidad estable (usePaso los usa como dependencia) */
function useIdsEstables(pasos?: readonly Paso[]): readonly string[] {
  const clave = pasos?.map((x) => x.id).join("|") ?? "";
  return useMemo(() => (clave ? clave.split("|") : SIN_PASOS), [clave]);
}

export type Superficie = "tienda" | "portal" | "correo" | "celular" | "admin" | "pdf";
export type SeccionAdmin = "Inicio" | "Pedidos" | "Inventarios" | "Mi Tienda" | "Levantamientos" | "Cotizaciones"
  | "Soporte" | "Mantenimientos" | "Tablero de la tienda";
const ROTULO: Record<Superficie, string> = {
  tienda: "Así lo ve el cliente", portal: "Portal del cliente", correo: "Correo al cliente",
  celular: "En el celular del cliente", admin: "Así lo ve Crimson", pdf: "Documento PDF",
};
const MENU_ADMIN: readonly SeccionAdmin[] = ["Inicio", "Cotizaciones", "Levantamientos", "Soporte", "Mantenimientos", "Inventarios", "Mi Tienda", "Pedidos", "Tablero de la tienda"];

export function Pantalla(p: {
  superficie: Superficie; rotulo?: string; direccion?: string; seccion?: SeccionAdmin;
  ancho?: "completo" | "telefono"; estado?: "propuesta" | "existente"; id?: string; className?: string; children: ReactNode;
}) {
  const sup = p.superficie;
  const tel = p.ancho === "telefono" || sup === "celular";
  return (
    <div id={p.id} data-estado={p.estado ?? "propuesta"}
      className={`ven-pp-pantalla ven-pp-pantalla--${sup}${tel ? " ven-pp-pantalla--tel" : ""}${p.className ? ` ${p.className}` : ""}`}>
      <span className="ven-pp-pantalla__esq" aria-hidden="true"><i /><i /><i /><i /></span>
      <p className="ven-pp-pantalla__rotulo"><i aria-hidden="true" />{p.rotulo ?? ROTULO[sup]}</p>
      {p.direccion && (sup === "tienda" || sup === "portal") && (
        <div className="ven-pp-pantalla__dir" aria-hidden="true"><span><i /><i /><i /></span><b>{p.direccion}</b></div>
      )}
      {sup === "admin" ? (
        <div className="ven-pp-admin">
          <ul className="ven-pp-admin__menu" aria-hidden="true">
            <li className="ven-pp-admin__marca">Crimson</li>
            {MENU_ADMIN.map((s) => <li key={s} data-on={s === p.seccion}>{s}</li>)}
          </ul>
          <div className="ven-pp-admin__cuerpo">
            <p className="ven-pp-admin__linea">Plataforma Crimson · {p.seccion ?? "Inicio"}</p>
            {p.children}
          </div>
        </div>
      ) : sup === "pdf" ? (
        <div className="ven-pp-pdf"><div className="ven-pp-pdf__hoja">{p.children}</div></div>
      ) : (
        <div className="ven-pp-pantalla__cuerpo">{p.children}</div>
      )}
    </div>
  );
}
