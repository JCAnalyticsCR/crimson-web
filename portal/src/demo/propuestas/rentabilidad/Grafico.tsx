/* Gráficos de «¿Es rentable?»: SVG a mano, sin librerías.
   - El viewBox se mide con el ancho real del contenedor (1 unidad = 1 px): el texto nunca se encoge ni se infla.
   - Pantalla de decisión: cero coreografía. La única respuesta es la retícula al pasar el cursor o tocar.
   - Cada gráfico tiene role="img", título, descripción generada y su tabla equivalente. */
import { useEffect, useId, useLayoutEffect, useRef, useState, type PointerEvent as PE, type RefObject } from "react";
import { crc, crcGrande, ejeColones, numero } from "./cifras";
import type { EscenarioId, Mes } from "./modelo";

function useAncho<T extends HTMLElement>(inicial: number): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [ancho, setAncho] = useState(inicial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const medir = () => { const w = Math.round(el.getBoundingClientRect().width); if (w > 0) setAncho(w); };
    medir();
    if (typeof ResizeObserver === "undefined") { window.addEventListener("resize", medir); return () => window.removeEventListener("resize", medir); }
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, ancho];
}

const idSeguro = (s: string) => s.replace(/[^a-zA-Z0-9_-]/g, "");

/** Escalones redondos para el eje y (1, 2, 2,5 o 5 × 10ⁿ), siempre incluyendo el 0. */
export function escalaY(lo: number, hi: number, objetivo = 4): { min: number; max: number; marcas: number[] } {
  let a = Math.min(0, lo), b = Math.max(0, hi);
  if (b - a < 1) b = a + 1;
  const crudo = (b - a) / objetivo;
  const pot = 10 ** Math.floor(Math.log10(crudo));
  const paso = [1, 2, 2.5, 5, 10].map((m) => m * pot).find((p) => p >= crudo - 1e-9) ?? 10 * pot;
  a = Math.floor(a / paso + 1e-9) * paso;
  b = Math.ceil(b / paso - 1e-9) * paso;
  const marcas: number[] = [];
  for (let v = a; v <= b + paso / 2; v += paso) marcas.push(Math.round(v));
  return { min: a, max: b, marcas };
}

export type Serie = { id: EscenarioId; nombre: string; valores: number[] /* meses 0…24 */; elegida: boolean };

const ROJO = "#c41a2f";
const GRIS = "#6f6a74";
const TINTA = "#15131a";

/* =====================================================================================
   Gráfico 1 · Utilidad acumulada en 24 meses (tres escenarios, inversión y equilibrio)
   ===================================================================================== */
export function GraficoAcumulado(p: { series: Serie[]; inversion: number | null; equilibrio: number | null; descripcion: string }) {
  const [ref, ancho] = useAncho<HTMLDivElement>(640);
  const uid = idSeguro(useId());
  const [k, setK] = useState<number | null>(null);
  const tactil = useRef(false);

  const W = Math.max(300, ancho);
  const angosto = W < 560;
  const H = angosto ? 260 : 300;
  const M = { l: angosto ? 50 : 62, r: angosto ? 84 : 118, t: 22, b: 30 };
  const pw = W - M.l - M.r;
  const ph = H - M.t - M.b;
  const todos = p.series.flatMap((s) => s.valores).concat(p.inversion ?? 0);
  const esc = escalaY(Math.min(...todos), Math.max(...todos));
  const x = (mes: number) => M.l + (mes / 24) * pw;
  const y = (v: number) => M.t + (1 - (v - esc.min) / (esc.max - esc.min)) * ph;
  const camino = (vs: number[]) => vs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  const orden = [...p.series].sort((a, b) => Number(a.elegida) - Number(b.elegida)); // la elegida se dibuja encima
  const elegida = p.series.find((s) => s.elegida) ?? p.series[0];

  // rótulos directos al final de cada línea, separados si chocan (con línea guía)
  const GAP = 30;
  const rot = p.series.map((s) => ({ s, meta: y(s.valores[24]), pos: y(s.valores[24]) })).sort((a, b) => a.meta - b.meta);
  for (let i = 1; i < rot.length; i++) if (rot[i].pos - rot[i - 1].pos < GAP) rot[i].pos = rot[i - 1].pos + GAP;
  const tope = H - M.b - 4;
  const exceso = rot.length ? rot[rot.length - 1].pos - tope : 0;
  if (exceso > 0) rot.forEach((r) => (r.pos -= exceso));
  const piso = M.t + 4;
  if (rot.length && rot[0].pos < piso) { const d = piso - rot[0].pos; rot.forEach((r) => (r.pos += d)); }

  const eqMes = p.equilibrio !== null && p.equilibrio <= 24 ? p.equilibrio : null;

  const mover = (e: PE<SVGRectElement>) => {
    const r = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!r || !r.width) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    setK(Math.max(0, Math.min(24, Math.round(((px - M.l) / pw) * 24))));
  };
  useEffect(() => {
    if (k === null || !tactil.current) return;
    const fuera = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) setK(null); };
    document.addEventListener("pointerdown", fuera);
    return () => document.removeEventListener("pointerdown", fuera);
  }, [k, ref]);

  const lectura = k === null ? null : [...p.series].sort((a, b) => b.valores[k] - a.valores[k]);

  // La lectura se ubica junto a la retícula sin salirse del gráfico: a la derecha si cabe, si no a la izquierda, y en
  // pantallas angostas (donde no cabe de ningún lado) centrada sobre la retícula y recortada a los bordes. Se mide su
  // ancho real antes de pintar (useLayoutEffect), así nunca aparece cortada ni salta.
  const tipRef = useRef<HTMLDivElement>(null);
  const xk = k === null ? 0 : x(k);
  useLayoutEffect(() => {
    const t = tipRef.current;
    if (!t) return;
    const tw = t.offsetWidth;
    let left = xk + 12;
    if (left + tw > W) left = xk - 12 - tw;
    if (left < 0) left = Math.max(0, Math.min(W - tw, xk - tw / 2));
    t.style.left = `${Math.round(left)}px`;
  });

  return (
    <div className="rnt-graf" ref={ref}>
      <svg className="rnt-graf__svg" width="100%" height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${uid}-t ${uid}-d`}>
        <title id={`${uid}-t`}>Utilidad acumulada en 24 meses, tres escenarios</title>
        <desc id={`${uid}-d`}>{p.descripcion}</desc>
        {esc.marcas.map((v) => (
          <g key={v}>
            <line x1={M.l} x2={M.l + pw} y1={y(v)} y2={y(v)} stroke={v === 0 ? "rgba(21,19,26,.42)" : "rgba(21,19,26,.10)"} strokeWidth="1" shapeRendering="crispEdges" />
            <text x={M.l - 8} y={y(v) + 4} textAnchor="end" className="rnt-graf__eje">{ejeColones(v)}</text>
          </g>
        ))}
        {[0, 6, 12, 18, 24].map((mes) => (
          <text key={mes} x={x(mes)} y={H - 10} textAnchor={mes === 0 ? "start" : mes === 24 ? "end" : "middle"} className="rnt-graf__eje">{mes === 0 ? "Mes 0" : mes}</text>
        ))}
        {p.inversion !== null && (
          <g>
            <line x1={M.l} x2={M.l + pw} y1={y(p.inversion)} y2={y(p.inversion)} stroke={TINTA} strokeWidth="1.5" strokeDasharray="2 4" />
            <text x={M.l + 6} y={y(p.inversion) - 7} className="rnt-graf__nota">Inversión {crcGrande(p.inversion)}</text>
          </g>
        )}
        {orden.map((s) => (
          <path key={s.id} d={camino(s.valores)} fill="none" stroke={s.elegida ? ROJO : GRIS} strokeWidth={s.elegida ? 3 : 1.5}
            strokeDasharray={s.elegida ? undefined : "4 3"} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {rot.map((r) => (
          <g key={r.s.id}>
            {Math.abs(r.pos - r.meta) > 3 && <line x1={x(24) + 3} y1={r.meta} x2={x(24) + 9} y2={r.pos} stroke={r.s.elegida ? ROJO : GRIS} strokeWidth="1" />}
            <text x={x(24) + 12} y={r.pos - 3} className={r.s.elegida ? "rnt-graf__serie rnt-graf__serie--on" : "rnt-graf__serie"}>{r.s.nombre}</text>
            <text x={x(24) + 12} y={r.pos + 11} className="rnt-graf__valor">{ejeColones(r.s.valores[24])}</text>
          </g>
        ))}
        {eqMes !== null && (
          <g>
            <circle cx={x(eqMes)} cy={y(elegida.valores[eqMes])} r="5.5" fill={ROJO} stroke="#fff" strokeWidth="2" />
            <text x={Math.min(Math.max(x(eqMes), M.l + 60), M.l + pw - 60)} y={y(elegida.valores[eqMes]) - 12} textAnchor="middle" className="rnt-graf__nota rnt-graf__nota--on">
              Equilibrio: mes {eqMes}
            </text>
          </g>
        )}
        {k !== null && lectura && (
          <g aria-hidden="true">
            <line x1={x(k)} x2={x(k)} y1={M.t} y2={M.t + ph} stroke={TINTA} strokeWidth="1" />
            {lectura.map((s) => <circle key={s.id} cx={x(k)} cy={y(s.valores[k])} r="4" fill={s.elegida ? ROJO : GRIS} stroke="#fff" strokeWidth="2" />)}
          </g>
        )}
        <rect x={M.l} y={M.t} width={pw} height={ph} fill="transparent" className="rnt-graf__zona"
          onPointerDown={(e) => { tactil.current = e.pointerType !== "mouse"; mover(e); }}
          onPointerMove={mover}
          onPointerLeave={(e) => { if (e.pointerType === "mouse") setK(null); }} />
      </svg>
      {k !== null && lectura && (
        <div ref={tipRef} className="rnt-graf__tip" aria-hidden="true">
          <p className="rnt-graf__tip-t">{k === 0 ? "Mes 0" : `Mes ${k}`}</p>
          <ul>
            {lectura.map((s) => (
              <li key={s.id}>
                <svg width="16" height="6" aria-hidden="true"><line x1="0" x2="16" y1="3" y2="3" stroke={s.elegida ? ROJO : GRIS} strokeWidth={s.elegida ? 3 : 1.5} strokeDasharray={s.elegida ? undefined : "4 3"} /></svg>
                <b>{crc(s.valores[k])}</b><span>{s.nombre}</span>
              </li>
            ))}
            {p.inversion !== null && (
              <li><svg width="16" height="6" aria-hidden="true"><line x1="0" x2="16" y1="3" y2="3" stroke={TINTA} strokeWidth="1.5" strokeDasharray="2 4" /></svg><b>{crc(p.inversion)}</b><span>Inversión</span></li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

/* =====================================================================================
   Gráfico 2 · De dónde sale la utilidad del mes 12
   ===================================================================================== */
export type Barra = { id: string; nombre: string; detalle: string; valor: number; color: string; nota?: string; horas?: boolean; texto?: string };

export function GraficoFuentes(p: { barras: Barra[]; descripcion: string }) {
  const [ref, ancho] = useAncho<HTMLDivElement>(640);
  const uid = idSeguro(useId());
  const W = Math.max(300, ancho);
  const FILA = 50;
  const plata = p.barras.filter((b) => !b.horas);
  const horas = p.barras.filter((b) => b.horas);
  const max = Math.max(1, ...p.barras.map((b) => b.valor));
  // la cifra va siempre afuera, en la punta: la escala deja lugar para la cifra más larga
  const rotulo = (b: Barra) => `${b.texto ?? crc(b.valor)}${b.nota ? ` · ${b.nota}` : ""}`;
  const anchoTxt = (t: string) => t.length * 7.4 + 12;
  const util = Math.max(80, W - Math.max(...p.barras.filter((b) => b.valor > 0).map((b) => anchoTxt(rotulo(b))), 0));
  const largo = (v: number) => (Math.max(0, v) / max) * util;
  const sep = horas.length ? 22 : 0;
  const H = p.barras.length * FILA + sep + 4;
  const fila = (b: Barra, y0: number) => {
    const w = largo(b.valor);
    const texto = rotulo(b);
    const yB = y0 + 26;
    const r = Math.min(4, w / 2);
    const forma = w <= 0 ? null : `M0 ${yB}H${(w - r).toFixed(1)}Q${w.toFixed(1)} ${yB} ${w.toFixed(1)} ${yB + r}V${yB + 16 - r}Q${w.toFixed(1)} ${yB + 16} ${(w - r).toFixed(1)} ${yB + 16}H0Z`;
    return (
      <g key={b.id}>
        <text x="0" y={y0 + 16} className="rnt-graf__fila">{b.nombre}<tspan className="rnt-graf__fila-d">{` · ${b.detalle}`}</tspan></text>
        {forma ? <path d={forma} fill={b.horas ? `url(#${uid}-r)` : b.color} /> : <line x1="0" x2="0" y1={yB} y2={yB + 16} stroke={GRIS} strokeWidth="2" />}
        <text x={w + 8} y={yB + 12.5} className="rnt-graf__cifra">{texto}</text>
        <title>{`${b.nombre}: ${texto}, al mes 12`}</title>
      </g>
    );
  };
  return (
    <div className="rnt-graf" ref={ref}>
      <svg className="rnt-graf__svg" width="100%" height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${uid}-t ${uid}-d`}>
        <title id={`${uid}-t`}>De dónde sale la utilidad del mes 12</title>
        <desc id={`${uid}-d`}>{p.descripcion}</desc>
        <defs>
          <pattern id={`${uid}-r`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="#ebe7e3" />
            <line x1="1" y1="0" x2="1" y2="6" stroke={GRIS} strokeWidth="2" />
          </pattern>
        </defs>
        {plata.map((b, i) => fila(b, i * FILA))}
        {horas.length > 0 && <line x1="0" x2={W} y1={plata.length * FILA + 10} y2={plata.length * FILA + 10} stroke="rgba(21,19,26,.18)" strokeDasharray="3 3" />}
        {horas.map((b, i) => fila(b, plata.length * FILA + sep + i * FILA))}
      </svg>
    </div>
  );
}

/* =====================================================================================
   Tablas equivalentes
   ===================================================================================== */
export function TablaEscenarios(p: { series: { nombre: string; meses: Mes[]; elegida: boolean }[] }) {
  const cortes = [3, 6, 9, 12, 15, 18, 21, 24];
  return (
    <div className="rnt-tabla-region" role="region" tabIndex={0} aria-label="Utilidad acumulada por escenario, cada 3 meses">
      <table className="rnt-tabla rnt-tabla--chica">
        <caption className="rnt-sr">Utilidad acumulada por escenario, cada 3 meses (₡ sin IVA)</caption>
        <thead><tr><th scope="col">Mes</th>{p.series.map((s) => <th key={s.nombre} scope="col" data-on={s.elegida || undefined}>{s.nombre}</th>)}</tr></thead>
        <tbody>
          {cortes.map((m) => (
            <tr key={m}><th scope="row">{m}</th>{p.series.map((s) => <td key={s.nombre} data-on={s.elegida || undefined}>{crc(s.meses[m - 1].acum)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TablaMeses(p: { meses: Mes[]; sumarHoras: boolean; hasta?: number }) {
  const cols: { k: keyof Mes; t: string }[] = [
    { k: "S1", t: "S1 Ventas" }, { k: "S2", t: "S2 Instalaciones" }, { k: "S3", t: "S3 Plan" }, { k: "S4", t: "S4 Carritos" },
    { k: "S5", t: "S5 Recompras" }, { k: "S6", t: p.sumarHoras ? "S6 Horas" : "S6 Horas (aparte)" }, { k: "S7", t: "S7 Fygaro" },
    { k: "costos", t: "Costos" }, { k: "U", t: "Utilidad del mes" }, { k: "acum", t: "Acumulado" },
  ];
  return (
    <div className="rnt-tabla-region" role="region" tabIndex={0} aria-label="Tabla mes a mes">
      <table className="rnt-tabla rnt-tabla--meses">
        <caption className="rnt-sr">Fuentes, costos y utilidad, mes a mes (₡ sin IVA)</caption>
        <thead><tr><th scope="col">Mes</th>{cols.map((c) => <th key={c.k} scope="col">{c.t}</th>)}</tr></thead>
        <tbody>
          {p.meses.slice(0, p.hasta ?? 24).map((m) => (
            <tr key={m.k}>
              <th scope="row" data-label="Mes">{m.k}</th>
              {cols.map((c) => (
                <td key={c.k} data-label={c.t} data-fuerte={c.k === "U" || c.k === "acum" || undefined}>
                  {c.k === "costos" ? (m.costos ? crc(-m.costos) : crc(0)) : c.k === "S6" ? `${crc(m.S6)} (${numero(m.horas, 1)} h)` : crc(m[c.k] as number)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
