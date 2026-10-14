/* «¿Es rentable?» (/rentabilidad): pantalla de DECISIÓN. Cero coreografía: las cifras cambian al instante y solo
   se mueve lo que Andrés toca. Modelo a la vista (modelo.ts), supuestos editables con insignia, fórmulas con los
   números sustituidos, tres escenarios, equilibrio contra la inversión que él escriba y cierre por etapas.
   Nada sale a la red: todo se calcula en el navegador y se guarda (si se puede) en localStorage. */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type Dispatch, type RefObject } from "react";
import { useSearchParams } from "react-router-dom";
import { BarraMonitor, NavPropuestas, SelloPropuesta } from "./comun";
import { ComoSeCalcula, QueNoIncluye } from "./Calculo";
import { Insignia, Supuestos, grupoDe, idCampo, type GrupoId } from "./Campos";
import { crc, crcGrande, numero } from "./cifras";
import {
  ajustado, entradasDe, escribirResumen, estadoInicial, guardar, leerGuardado, paramDe, precioPlanB2, reducir, textoEquilibrio,
  type Accion, type Estado,
} from "./estado";
import { AceptarPorEtapas } from "./Etapas";
import { GraficoAcumulado, GraficoFuentes, TablaEscenarios, TablaMeses, type Barra } from "./Grafico";
import { calcular, ESCENARIOS, mes, verificarModelo, type EscenarioId, type Resultado } from "./modelo";
import { ENFOQUES, ESCENARIO_INFO, FUENTES } from "./supuestos";
import "./rentabilidad.css";

// Autoverificación en desarrollo: si alguien toca el modelo y deja de cuadrar con la sección 6.7, la consola lo dice.
if (import.meta.env.DEV) {
  const v = verificarModelo();
  if (!v.ok) console.error(`[rentabilidad] el modelo no pasa ${v.total - v.pasan} de ${v.total} vectores`, v.filas.filter((f) => !f.pasa));
}

const ESCRITORIO = "(min-width: 1024px)";
const mq = (q: string) => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(q).matches;

function useMedia(q: string): boolean {
  const [m, setM] = useState(() => mq(q));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const l = window.matchMedia(q);
    const on = () => setM(l.matches);
    on();
    l.addEventListener("change", on);
    return () => l.removeEventListener("change", on);
  }, [q]);
  return m;
}

const esEscenario = (x: string | null): x is EscenarioId => !!x && (ESCENARIOS as readonly string[]).includes(x);

export default function Rentabilidad() {
  const [params, setParams] = useSearchParams();
  const pEscenario = params.get("escenario");
  const enfoque = params.get("enfoque");
  const [s, d] = useReducer(reducir, undefined, (): Estado => {
    const g = leerGuardado() ?? estadoInicial();
    return esEscenario(pEscenario) ? { ...g, escenario: pEscenario } : g;
  });
  const sRef = useRef(s);
  sRef.current = s;

  // ?escenario= que cambia desde afuera (un enlace) manda
  useEffect(() => { if (esEscenario(pEscenario)) d({ t: "escenario", e: pEscenario }); }, [pEscenario]);

  const res = useMemo(
    () => Object.fromEntries(ESCENARIOS.map((e) => [e, calcular(entradasDe(s, e))])) as Record<EscenarioId, Resultado>,
    // el escenario elegido no cambia ningún cálculo: solo cuál columna manda
    [s.base, s.ajustes, s.inicio, s.incluida, s.inversion, s.sumarHoras],
  );
  const r = res[s.escenario];

  useEffect(() => { guardar(s); }, [s]);
  useEffect(() => {
    const t = window.setTimeout(() => escribirResumen(s, r), 400);
    return () => window.clearTimeout(t);
  }, [s, r]);

  // título, foco al h1 o al bloque pedido con ?enfoque=
  const h1 = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const previo = document.title;
    document.title = "¿Es rentable? · Tienda Crimson (vista previa)";
    return () => { document.title = previo; };
  }, []);
  useEffect(() => {
    const destino = enfoque ? ENFOQUES[enfoque]?.destino : undefined;
    if (!destino) {
      window.scrollTo(0, 0);
      h1.current?.focus({ preventScroll: true });
      return;
    }
    const id = requestAnimationFrame(() => {
      const el = document.getElementById(destino);
      if (!el) return;
      el.scrollIntoView({ block: "center", behavior: "auto" });
      el.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(id);
  }, [enfoque]);

  // grupos de supuestos: desde 1024 px el primero abierto
  const escritorio = useMedia(ESCRITORIO);
  const [abiertos, setAbiertos] = useState<Record<GrupoId, boolean>>(() => ({
    tienda: mq(ESCRITORIO), margenes: false, escenario: false, precios: false, costos: false, calendario: false,
  }));
  const abrir = useCallback((g: GrupoId, v: boolean) => setAbiertos((a) => (a[g] === v ? a : { ...a, [g]: v })), []);
  const irACampo = useCallback((clave: string) => {
    const g = grupoDe(clave);
    setAbiertos((a) => ({ ...a, [g]: true }));
    const id = idCampo(clave, sRef.current.escenario);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const el = document.getElementById(id) as HTMLInputElement | null;
      if (!el) return;
      el.scrollIntoView({ block: "center", behavior: "auto" });
      el.focus({ preventScroll: true });
    }));
  }, []);

  // el plan que Andrés diseñó en B2 (si lo hizo): se ofrece, nunca se aplica solo
  const [planB2, setPlanB2] = useState<number | null>(precioPlanB2);
  useEffect(() => {
    const leer = () => setPlanB2(precioPlanB2());
    window.addEventListener("storage", leer);
    document.addEventListener("visibilitychange", leer);
    return () => { window.removeEventListener("storage", leer); document.removeEventListener("visibilitychange", leer); };
  }, []);

  const elegir = (e: EscenarioId) => {
    d({ t: "escenario", e });
    if (params.has("escenario")) {
      const n = new URLSearchParams(params);
      n.set("escenario", e);
      setParams(n, { replace: true });
    }
  };

  // región viva: solo la cifra principal y el equilibrio, 500 ms después del último cambio
  const nombre = ESCENARIO_INFO[s.escenario].nombre;
  const m12 = mes(r, 12);
  const anuncio = `${nombre}${ajustado(s, s.escenario) ? " con tus cambios" : ""}: utilidad al mes 12, ${crc(m12.U)} por mes. Equilibrio: ${textoEquilibrio(r)}.`;
  const inicial = useRef(anuncio);
  const [vivo, setVivo] = useState("");
  useEffect(() => {
    if (anuncio === inicial.current) return;
    inicial.current = "";
    const t = window.setTimeout(() => setVivo(anuncio), 500);
    return () => window.clearTimeout(t);
  }, [anuncio]);

  // barra fija inferior (debajo de 1024 px) cuando los resultados no están a la vista y no se está escribiendo
  const resRef = useRef<HTMLElement>(null);
  const [resVisible, setResVisible] = useState(false);
  useEffect(() => {
    const el = resRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([en]) => setResVisible(en.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const [escribiendo, setEscribiendo] = useState(false);
  const verResultados = () => {
    const el = document.getElementById("rnt-resultado");
    el?.scrollIntoView({ block: "start", behavior: "auto" });
    document.getElementById("rnt-res-t")?.focus({ preventScroll: true });
  };

  const otros = ESCENARIOS.filter((e) => e !== s.escenario);
  const series = ESCENARIOS.map((e) => ({ id: e, nombre: ESCENARIO_INFO[e].nombre, valores: [0, ...res[e].meses.slice(0, 24).map((m) => m.acum)], elegida: e === s.escenario }));
  const eqTxt = r.equilibrio.tipo === "mes" ? `equilibrio en el mes ${r.equilibrio.mes}` : "equilibrio en más de 36 meses";
  const desc1 = `Escenario ${nombre.toLowerCase()}: ${crc(mes(r, 12).acum)} acumulados a 12 meses y ${crc(mes(r, 24).acum)} a 24; `
    + `${r.inversion === null ? "sin inversión escrita" : `inversión de ${crc(r.inversion)}, ${eqTxt}`}. `
    + `A 24 meses, ${otros.map((e) => `${ESCENARIO_INFO[e].nombre.toLowerCase()} ${crc(mes(res[e], 24).acum)}`).join(" y ")}.`;

  const b = s.base;
  const barras: Barra[] = [
    ...FUENTES.filter((f) => f.id !== "S6").map((f): Barra => ({
      id: f.id, nombre: f.nombre, detalle: `Etapa ${f.etapa}`, valor: m12[f.id], color: f.color,
      nota: f.etapa > 0 && !s.incluida[f.etapa] ? "etapa apagada" : f.id === "S7" && b.Fm === null && b.cF === null ? "escribí lo que pagás" : undefined,
    })),
    { id: "S6", nombre: "Horas liberadas (horas, no plata)", detalle: s.sumarHoras ? "sumadas" : "no se suman", valor: m12.S6, color: "", horas: true,
      texto: `${numero(m12.horas, 1)} h ≈ ${crc(m12.S6)}` },
  ];
  const sumaFuentes = m12.S1 + m12.S2 + m12.S3 + m12.S4 + m12.S5 + m12.S7;
  const desc2 = `Mes 12, escenario ${nombre.toLowerCase()}: `
    + barras.filter((x) => !x.horas).map((x) => `${x.nombre} ${crc(x.valor)}`).join(", ")
    + `. Aparte, horas liberadas: ${numero(m12.horas, 1)} h, unos ${crc(m12.S6)}.`;
  const eqFuera = r.equilibrio.tipo === "mes" && r.equilibrio.mes > 24 ? r.equilibrio.mes : null;

  return (
    <div className="pp rnt">
      <NavPropuestas actual="RT" />
      <main id="contenido" tabIndex={-1} className="rnt-main"
        onFocus={(e) => { if ((e.target as HTMLElement).tagName === "INPUT" && (e.target as HTMLInputElement).type === "text") setEscribiendo(true); }}
        onBlur={(e) => { if ((e.target as HTMLElement).tagName === "INPUT") setEscribiendo(false); }}>
        <header className="rnt-cab">
          <div className="rnt-wrap">
            <BarraMonitor cam="09" rotulo="Números" />
            <div className="rnt-cab__grid">
              <div className="rnt-cab__txt">
                <p className="rnt-ceja rnt-ceja--oscura">Modelo de rentabilidad · vista previa</p>
                <h1 ref={h1} tabIndex={-1} className="rnt-h1">¿Es rentable?</h1>
                <p className="rnt-cab__bajada">Un modelo a la vista para hacer las cuentas con tus números. Arranca en el escenario conservador.</p>
              </div>
              <div className="rnt-cab__aviso">
                <p>
                  Cada valor marcado <b>EJEMPLO</b> es un supuesto para mostrar la cuenta: no es un dato de mercado ni una promesa.
                  El margen sobre venta de 35 % es el dato de Crimson para sus levantamientos: aquí se usa como dato en la instalación; en el
                  equipo de tienda es un ejemplo, escribí el tuyo. Lo que no sabemos (lo que pagás a Fygaro, lo que cuesta operar la
                  tienda y la inversión) queda vacío hasta que lo escribás.
                </p>
                <ul className="rnt-leyenda" aria-label="Qué significa cada insignia">
                  <li><Insignia tipo="ejemplo" /> supuesto para mostrar la cuenta</li>
                  <li><Insignia tipo="crimson" /> dato real de Crimson</li>
                  <li><Insignia tipo="tu-dato" /> vacío hasta que lo escribás</li>
                  <li><Insignia tipo="editado" /> lo cambiaste vos</li>
                </ul>
              </div>
            </div>
          </div>
        </header>

        <div className="rnt-wrap rnt-cuerpo">
          <div className="rnt-grid">
            <section className="rnt-bloque rnt-bloque--esc" aria-label="Escenario">
              <fieldset id="rnt-escenarios" className="rnt-seg">
                <legend className="rnt-seg__leyenda"><span className="rnt-ceja">Paso 1</span><span className="rnt-h2">Elegí un escenario</span></legend>
                <div className="rnt-seg__ops">
                  {ESCENARIOS.map((e) => (
                    <label key={e} className="rnt-seg__op" data-on={e === s.escenario || undefined}>
                      <input type="radio" name="rnt-escenario" value={e} checked={e === s.escenario} onChange={() => elegir(e)} className="rnt-seg__radio" />
                      <span className="rnt-seg__nombre">
                        {ESCENARIO_INFO[e].nombre}
                        {ajustado(s, e) && <span className="rnt-seg__cambios"> (con tus cambios)</span>}
                      </span>
                      <span className="rnt-seg__texto">{ESCENARIO_INFO[e].texto}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              {ajustado(s, s.escenario) && (
                <div className="rnt-volver">
                  <button type="button" className="rnt-btn rnt-btn--fantasma rnt-btn--sm" onClick={() => d({ t: "volverEjemplos" })}>Volver a los ejemplos</button>
                  <p>Vuelve a los ejemplos del escenario {nombre.toLowerCase()}, la base, el calendario y las etapas incluidas, y deja las horas sin sumar. Tus datos (Fygaro, costo de operar e inversión) se conservan.</p>
                </div>
              )}
            </section>

            <Resultados refSec={resRef} s={s} d={d} res={res} r={r} elegir={elegir} irACampo={irACampo} />

            <section className="rnt-bloque" aria-labelledby="rnt-g1-t">
              <p className="rnt-ceja">Gráfico 1</p>
              <h2 id="rnt-g1-t" className="rnt-h2">Utilidad acumulada en 24 meses</h2>
              <p className="rnt-bajada">
                <span className="rnt-clave rnt-clave--on" aria-hidden="true" /> Línea roja continua: escenario {nombre.toLowerCase()}.{" "}
                <span className="rnt-clave" aria-hidden="true" /> Grises punteadas: los otros dos. Montos sin IVA, acumulados desde el mes 0.
                Pasá el cursor o tocá el gráfico para leer cada mes.
              </p>
              <GraficoAcumulado series={series} inversion={r.inversion} equilibrio={r.equilibrio.tipo === "mes" ? r.equilibrio.mes : null} descripcion={desc1} />
              {eqFuera !== null && <p className="rnt-nota">Con la inversión escrita, el equilibrio llega en el mes {eqFuera}: fuera de este gráfico de 24 meses.</p>}
              {r.equilibrio.tipo === "mas36" && <p className="rnt-nota">Con la inversión escrita y estos supuestos, el equilibrio llega después del mes 36.</p>}
              <details className="rnt-mas">
                <summary>Ver los números en tabla</summary>
                <TablaEscenarios series={ESCENARIOS.map((e) => ({ nombre: ESCENARIO_INFO[e].nombre, meses: res[e].meses, elegida: e === s.escenario }))} />
              </details>
            </section>

            <section className="rnt-bloque" aria-labelledby="rnt-sup-t">
              <p className="rnt-ceja">Paso 2</p>
              <h2 id="rnt-sup-t" className="rnt-h2">Supuestos: cambialos por los tuyos</h2>
              <p className="rnt-bajada">Cada cambio recalcula todo al instante. Los resultados {escritorio ? "de la derecha" : "de arriba"} se actualizan solos.</p>
              <Supuestos s={s} d={d} escritorio={escritorio} abiertos={abiertos} abrir={abrir} planB2={planB2} resultado={r} />
            </section>

            <section className="rnt-bloque" aria-labelledby="rnt-g2-t">
              <p className="rnt-ceja">Gráfico 2 · escenario {nombre.toLowerCase()}</p>
              <h2 id="rnt-g2-t" className="rnt-h2">De dónde sale la utilidad del mes 12</h2>
              <p className="rnt-bajada">Cada barra dice su nombre y su etapa: Etapa 0 en tinta (ventas) y ocre (Fygaro), Etapa 1 en carmesí, Etapa 2 en vino y Etapa 3 en gris. Las horas van aparte, rayadas: son horas, no plata.</p>
              <GraficoFuentes barras={barras} descripcion={desc2} />
              <dl className="rnt-cuenta">
                <div><dt>Suma de las fuentes</dt><dd>{crc(sumaFuentes)}</dd></div>
                {m12.costos > 0 && <div><dt>Menos el costo de operar la tienda propia</dt><dd>{crc(-m12.costos)}</dd></div>}
                {s.sumarHoras && <div><dt>Más el valor de las horas (lo encendiste)</dt><dd>+{crc(m12.S6)}</dd></div>}
                <div data-total=""><dt>Utilidad del mes 12</dt><dd>{crc(m12.U)}</dd></div>
              </dl>
              <details className="rnt-mas">
                <summary>Ver la tabla mes a mes</summary>
                <TablaMeses meses={r.meses} sumarHoras={s.sumarHoras} />
              </details>
            </section>

            <section id="rnt-calculo" className="rnt-bloque" aria-labelledby="rnt-calc-t">
              <p className="rnt-ceja">Transparencia</p>
              <h2 id="rnt-calc-t" className="rnt-h2">Cómo se calcula</h2>
              <p className="rnt-bajada">
                Cada fórmula con los números del mes 12 del escenario {nombre.toLowerCase()} ya sustituidos. Montos sin IVA; los porcentajes de la
                fórmula son los de tus supuestos. Tocá un supuesto para cambiarlo.
              </p>
              <ComoSeCalcula s={s} r={r} esc={paramDe(s, s.escenario)} enfoque={enfoque} irACampo={irACampo} />
            </section>

            <section className="rnt-bloque" aria-labelledby="rnt-no-t">
              <p className="rnt-ceja">Límites</p>
              <h2 id="rnt-no-t" className="rnt-h2">Qué no incluye este modelo</h2>
              <QueNoIncluye />
            </section>
          </div>
        </div>

        <AceptarPorEtapas s={s} r={r} enfoque={enfoque} irACampo={irACampo} />
        <p className="rnt-sr" aria-live="polite" aria-atomic="true">{vivo}</p>
      </main>

      {!escritorio && !resVisible && !escribiendo && (
        <div className="rnt-barra" data-pp-barra="">
          <p className="rnt-barra__txt">
            <b>{nombre}: {crc(m12.U)}/mes</b> al mes 12
            <span><span className="rnt-sr">. </span>Equilibrio: {r.equilibrio.tipo === "mes" ? `mes ${r.equilibrio.mes}` : r.equilibrio.tipo === "mas36" ? "más de 36 meses" : "falta la inversión"}</span>
          </p>
          <button type="button" className="rnt-btn rnt-btn--crimson rnt-btn--sm" onClick={verResultados}>Resultados</button>
        </div>
      )}
    </div>
  );
}

/* ============================================================================================================ */

function Resultados(p: {
  refSec: RefObject<HTMLElement | null>; s: Estado; d: Dispatch<Accion>; res: Record<EscenarioId, Resultado>; r: Resultado;
  elegir: (e: EscenarioId) => void; irACampo: (c: string) => void;
}) {
  const { s, r } = p;
  const m1 = mes(r, 1), m12 = mes(r, 12), m24 = mes(r, 24);
  const nombre = ESCENARIO_INFO[s.escenario].nombre;
  const todasIncluidas = [1, 2, 3].every((n) => s.incluida[n]);
  const arrancando = [0, 1, 2, 3].filter((n) => (n === 0 || s.incluida[n]) && m12.r[n] < 1);
  const nota12 = arrancando.length
    ? `al mes 12; ${arrancando.map((n) => `la etapa ${n}`).join(" y ")} todavía ${arrancando.length > 1 ? "están" : "está"} arrancando`
    : todasIncluidas ? "al mes 12, con todas las etapas andando" : "al mes 12, con las etapas incluidas andando";
  const grande = (n: number) => <span title={crc(n)}>{crcGrande(n)}</span>;
  const mrr = r.meses.slice(0, 24).map((m) => m.MRR);
  const mrrMax = Math.max(1, ...mrr);
  const b = s.base;
  const sinInv = r.sinInversion;
  const avisoSinInv = sinInv.length > 0
    ? `${sinInv.length > 1 ? `Las etapas ${sinInv.slice(0, -1).join(", ")} y ${sinInv[sinInv.length - 1]} suman` : `La etapa ${sinInv[0]} suma`} utilidad pero no ${sinInv.length > 1 ? "tienen" : "tiene"} inversión escrita: el equilibrio sale más corto de lo real.`
    : null;
  const avisos = [
    b.Fm === null && "Sin la mensualidad de Fygaro: no se cuenta ese ahorro.",
    b.Cop === null && "Sin el costo de operar la tienda propia: el resultado es antes de ese costo.",
    r.inversion === null && "Sin inversión: no hay punto de equilibrio.",
    avisoSinInv,
  ].filter((x): x is string => !!x);
  const inv0 = s.inversion[0];

  return (
    <section id="rnt-resultado" ref={p.refSec} className="rnt-res" aria-labelledby="rnt-res-t">
      <div className="rnt-res__barra">
        <h2 id="rnt-res-t" tabIndex={-1} className="rnt-res__h">Resultados · {nombre}{ajustado(s, s.escenario) ? " (con tus cambios)" : ""}</h2>
        <SelloPropuesta estado="ejemplo" tamano="chico" />
      </div>

      <div className="rnt-res__bloque rnt-res__heroe">
        <p className="rnt-res__rot">Utilidad incremental por mes</p>
        <p className="rnt-res__cifra">{grande(m12.U)}<span className="rnt-res__unidad">/mes</span></p>
        <p className="rnt-res__nota">{nota12} · Mes 1: {crc(m1.U)}</p>
        <div className="rnt-res__tres" role="group" aria-label="Utilidad al mes 12 de los tres escenarios">
          {ESCENARIOS.map((e) => (
            <button key={e} type="button" className="rnt-res__esc" aria-pressed={e === s.escenario} onClick={() => p.elegir(e)}>
              <span className="rnt-res__esc-n">{ESCENARIO_INFO[e].nombre}</span>
              <span className="rnt-res__esc-v">{crc(mes(p.res[e], 12).U)}<span className="rnt-sr"> por mes al mes 12</span></span>
            </button>
          ))}
        </div>
      </div>

      <div className="rnt-res__bloque">
        <p className="rnt-res__rot">Utilidad acumulada</p>
        <dl className="rnt-res__par">
          <div><dt>A 12 meses</dt><dd>{grande(m12.acum)}</dd></div>
          <div><dt>A 24 meses</dt><dd>{grande(m24.acum)}</dd></div>
        </dl>
      </div>

      <div className="rnt-res__bloque">
        <p className="rnt-res__rot">Ingreso recurrente del plan <span className="rnt-res__tag">B2</span></p>
        <dl className="rnt-res__par">
          <div><dt>Acumulado a 12 meses</dt><dd>{grande(m12.recAcum)}</dd></div>
          <div><dt>Acumulado a 24 meses</dt><dd>{grande(m24.recAcum)}</dd></div>
        </dl>
        <div className="rnt-chispa" aria-hidden="true">
          {mrr.map((v, i) => <i key={i} style={{ height: `${Math.max(v > 0 ? 6 : 2, (v / mrrMax) * 100)}%` }} />)}
        </div>
        <p className="rnt-res__chispa-pie" aria-hidden="true"><span>Mes 1</span><span>Ingreso del plan por mes</span><span>Mes 24</span></p>
        <p className="rnt-res__nota">
          Al mes 24: ≈ {numero(m24.A, 1)} planes activos y {crc(m24.MRR)}/mes (ingreso sin IVA, no utilidad). Arranca chico y crece cada mes: es ingreso que se repite.
        </p>
        {!s.incluida[2] && <p className="rnt-res__nota">La etapa 2 está apagada: el plan no suma.</p>}
      </div>

      <div className="rnt-res__bloque">
        <p className="rnt-res__rot">Punto de equilibrio</p>
        {r.equilibrio.tipo === "mes" && <p className="rnt-res__cifra rnt-res__cifra--m">Mes {r.equilibrio.mes}</p>}
        {r.equilibrio.tipo === "mas36" && <p className="rnt-res__cifra rnt-res__cifra--m rnt-res__cifra--txt">Más de 36 meses con estos supuestos</p>}
        {r.equilibrio.tipo === "sin-inversion" && (
          <p className="rnt-res__falta">
            Escribí la inversión para calcularlo.{" "}
            <button type="button" className="rnt-link" onClick={() => p.irACampo("inv-0")}>Escribir la inversión</button>
          </p>
        )}
        {r.inversion !== null && <p className="rnt-res__nota">Inversión de las etapas incluidas: {crc(r.inversion)}.</p>}
        {avisoSinInv && <p className="rnt-res__alerta" role="note"><span aria-hidden="true">⚠ </span>{avisoSinInv}</p>}
        {r.inversion !== null && inv0 !== null && inv0 > 0 && (
          <p className="rnt-res__nota">
            Solo la tienda propia (Etapa 0) contra su inversión de {crc(inv0)}:{" "}
            <b>{r.equilibrioTienda.tipo === "mes" ? `mes ${r.equilibrioTienda.mes}` : "más de 36 meses con estos supuestos"}</b>.
          </p>
        )}
        <p className="rnt-res__nota">
          {m12.acum > 0
            ? <>Con estos supuestos, una inversión total de las etapas incluidas de hasta <b>{crc(m12.acum)}</b> se recupera en 12 meses{m24.acum > 0 ? <>, y de hasta <b>{crc(m24.acum)}</b> en 24.</> : "."}</>
            : m24.acum > 0
              ? <>Con estos supuestos, a 12 meses la utilidad acumulada todavía no es positiva; una inversión total de las etapas incluidas de hasta <b>{crc(m24.acum)}</b> se recupera en 24.</>
              : <>Con estos supuestos, la utilidad acumulada a 24 meses no es positiva: no alcanza para recuperar una inversión.</>}
        </p>
      </div>

      <div className="rnt-res__bloque">
        <p className="rnt-res__rot">Horas liberadas</p>
        <p className="rnt-res__horas"><b>{numero(m12.horas, 1)} h por mes</b> ≈ {crc(m12.S6)}</p>
        <label className="rnt-switch">
          <input type="checkbox" role="switch" checked={s.sumarHoras} onChange={(e) => p.d({ t: "sumarHoras", v: e.target.checked })} />
          <span className="rnt-switch__pista" aria-hidden="true" />
          <span>Sumar el valor de las horas al resultado</span>
        </label>
        <p className="rnt-res__nota">Apagado por prudencia: las horas valen plata solo si ese tiempo se usa en otra cosa.</p>
      </div>

      {avisos.length > 0 && (
        <ul className="rnt-avisos" aria-label="Datos que faltan">
          {avisos.map((a) => <li key={a}><span aria-hidden="true">⚠</span>{a}</li>)}
          <li className="rnt-avisos__accion"><button type="button" className="rnt-link" onClick={() => p.irACampo(b.Fm === null ? "Fm" : b.Cop === null ? "Cop" : r.inversion === null ? "inv-0" : `inv-${sinInv[0] ?? 0}`)}>Escribir lo que falta</button></li>
        </ul>
      )}

      <p className="rnt-res__pie">Utilidad bruta incremental: antes de impuesto sobre la renta y de gastos fijos que no cambian con la tienda. No incluye las comisiones de tarjeta de las ventas nuevas ni del plan, ni el costo de la mensajería o del asistente: sumalos en «Costo de operar».</p>
    </section>
  );
}
