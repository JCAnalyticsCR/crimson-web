/* COPIA LOCAL MÍNIMA de propuestas/_comun para el equipo «rentabilidad» (sección 1.4 de la especificación).
   Exporta solo un SUBCONJUNTO de los nombres de la sección 1.6, con las mismas firmas y un comportamiento mínimo.
   Clases con prefijo rnt-pp- (pp- queda reservado a _comun). Al integrar, comun.ts pasa a
   `export * from "../_comun";` y esta carpeta se borra: ningún otro archivo cambia. */
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import "./comun.css";

// ---------- tipos y constantes (de registro.ts)
export type PropuestaId = "A1" | "A2" | "A3" | "A4" | "A5" | "B1" | "B2" | "B3" | "B4" | "C1" | "C2" | "C3" | "D1" | "D2" | "E1" | "E2" | "E3";
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

const IDS: readonly PropuestaId[] = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "B4", "C1", "C2", "C3", "D1", "D2", "E1", "E2", "E3"];
const esId = (x: unknown): x is PropuestaId => typeof x === "string" && (IDS as readonly string[]).includes(x);

// ---------- almacenamiento (try/catch; set devuelve false si no se pudo guardar)
type Cajon = { get(k: string): string | null; set(k: string, v: string): boolean; del(k: string): void };
const cajon = (s: () => Storage): Cajon => ({
  get(k) { try { return s().getItem(k); } catch { return null; } },
  set(k, v) { try { s().setItem(k, v); return true; } catch { return false; } },
  del(k) { try { s().removeItem(k); } catch { /* almacenamiento bloqueado: no pasa nada */ } },
});
export const almacen: { local: Cajon; sesion: Cajon } = { local: cajon(() => window.localStorage), sesion: cajon(() => window.sessionStorage) };

// ---------- formato
const fCRC = new Intl.NumberFormat("es-CR", { style: "currency", currency: "CRC", currencyDisplay: "narrowSymbol", minimumFractionDigits: 0, maximumFractionDigits: 0 });
export const colones = (n: number): string => fCRC.format(n);
export const porcentaje = (x: number, dec = 1): string =>
  new Intl.NumberFormat("es-CR", { style: "percent", minimumFractionDigits: dec, maximumFractionDigits: dec }).format(x);

// ---------- interés (Me interesa), sincronizado entre pantallas
let memoria: PropuestaId[] = []; // respaldo cuando el navegador no deja guardar
const leerInteres = (): PropuestaId[] => {
  const raw = almacen.local.get(CLAVES.interes);
  if (raw === null) return memoria;
  try { const a: unknown = JSON.parse(raw); return Array.isArray(a) ? a.filter(esId) : []; } catch { return []; }
};
const escribirInteres = (ids: PropuestaId[]) => {
  const limpio = IDS.filter((id) => ids.includes(id));
  memoria = limpio;
  almacen.local.set(CLAVES.interes, JSON.stringify(limpio));
  window.dispatchEvent(new CustomEvent<PropuestaId[]>(EVENTOS.interes, { detail: limpio }));
};

export function useInteres(): {
  ids: PropuestaId[]; tiene: (id: PropuestaId) => boolean; alternar: (id: PropuestaId) => void;
  agregar: (ids: PropuestaId[]) => void; quitar: (ids: PropuestaId[]) => void;
} {
  const [ids, setIds] = useState<PropuestaId[]>(leerInteres);
  useEffect(() => {
    const onEvento = (e: Event) => {
      const d = (e as CustomEvent<unknown>).detail;
      setIds(Array.isArray(d) ? d.filter(esId) : leerInteres());
    };
    const onStorage = (e: StorageEvent) => { if (e.key === null || e.key === CLAVES.interes) setIds(leerInteres()); };
    window.addEventListener(EVENTOS.interes, onEvento);
    window.addEventListener("storage", onStorage);
    return () => { window.removeEventListener(EVENTOS.interes, onEvento); window.removeEventListener("storage", onStorage); };
  }, []);
  const tiene = useCallback((id: PropuestaId) => ids.includes(id), [ids]);
  const alternar = useCallback((id: PropuestaId) => {
    const a = leerInteres();
    escribirInteres(a.includes(id) ? a.filter((x) => x !== id) : [...a, id]);
  }, []);
  const agregar = useCallback((nuevos: PropuestaId[]) => escribirInteres([...leerInteres(), ...nuevos]), []);
  const quitar = useCallback((fuera: PropuestaId[]) => escribirInteres(leerInteres().filter((x) => !fuera.includes(x))), []);
  return { ids, tiene, alternar, agregar, quitar };
}

// ---------- componentes
export function SelloPropuesta(p: {
  estado?: "propuesta" | "existente" | "ejemplo"; id?: string; tamano?: "normal" | "chico"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const estado = p.estado ?? "propuesta";
  const tamano = p.tamano ?? "normal";
  const texto =
    estado === "propuesta" ? (tamano === "chico" ? <>Propuesta<span className="rnt-pp-sr"> · no incluida aún</span></> : "Propuesta · no incluida aún")
    : estado === "existente" ? (tamano === "chico" ? "Ya está" : "Ya está en la plataforma")
    : "Datos de ejemplo";
  return (
    <span className={`rnt-pp-sello${p.className ? ` ${p.className}` : ""}`} data-estado={estado} data-tamano={tamano} data-tono={p.tono ?? "claro"}>
      {p.id && <b className="rnt-pp-sello__id">{p.id}</b>}
      {estado === "existente" && <span aria-hidden="true">✓</span>}
      <span>{texto}</span>
    </span>
  );
}

function useReloj(): string {
  const f = () => new Date().toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const [t, setT] = useState(f);
  useEffect(() => { const id = window.setInterval(() => setT(f()), 1000); return () => window.clearInterval(id); }, []);
  return t;
}

/** «● CAM 09 · NÚMEROS» + hora HH:MM:SS (texto que se actualiza 1 vez por segundo; nada se anima). */
export function BarraMonitor(p: { cam: string; rotulo: string; derecha?: ReactNode }) {
  const t = useReloj();
  return (
    <div className="rnt-pp-monitor">
      <span className="rnt-pp-monitor__l" aria-hidden="true"><i className="rnt-pp-monitor__rec" />CAM {p.cam}<span className="rnt-pp-monitor__sep">·</span>{p.rotulo}</span>
      <span className="rnt-pp-monitor__r">{p.derecha}<span className="rnt-pp-monitor__tc" aria-hidden="true">{t}</span></span>
    </div>
  );
}

const ENLACES: { to: string; texto: string; id: MaquetaId | "LQV" }[] = [
  { to: "/propuestas", texto: "Lo que viene", id: "LQV" },
  { to: "/rentabilidad", texto: "¿Es rentable?", id: "RT" },
  { to: "/detras-de-escena", texto: "Detrás de escena", id: "DE" },
];

/** Barra pegajosa de las propuestas (versión local: sin anterior/siguiente). */
export function NavPropuestas(p: { actual?: MaquetaId | "LQV" }) {
  const [abierta, setAbierta] = useState(false);
  const lista = useId();
  const loc = useLocation();
  useEffect(() => setAbierta(false), [loc.pathname, loc.search]);
  useEffect(() => {
    if (!abierta) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setAbierta(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [abierta]);
  const tienda = almacen.sesion.get(CLAVES.ultimaRuta) || "/";
  const titulo = ENLACES.find((e) => e.id === p.actual)?.texto ?? (p.actual ? `Maqueta ${p.actual}` : "Propuestas");
  return (
    <nav className="rnt-pp-nav" data-pp-nav="" aria-label="Propuestas">
      <div className="rnt-pp-nav__in">
        <Link className="rnt-pp-nav__tienda" to={tienda}>← Tienda</Link>
        <span className="rnt-pp-nav__titulo" aria-hidden="true">{titulo}</span>
        <ul className="rnt-pp-nav__links">
          {ENLACES.map((e) => (
            <li key={e.id}><Link to={e.to} aria-current={e.id === p.actual ? "page" : undefined}>{e.texto}</Link></li>
          ))}
        </ul>
        <button type="button" className="rnt-pp-nav__todas" aria-expanded={abierta} aria-controls={lista} onClick={() => setAbierta((v) => !v)}>Todas</button>
      </div>
      <ul id={lista} className="rnt-pp-nav__lista" hidden={!abierta}>
        {ENLACES.map((e) => (
          <li key={e.id}><Link to={e.to} aria-current={e.id === p.actual ? "page" : undefined}>{e.texto}</Link></li>
        ))}
      </ul>
    </nav>
  );
}
