/* C1 · Tablero de la tienda (PROPUESTA, no incluida aun). Pantalla de trabajo: cero coreografia, las cifras cambian
   al instante con el periodo (?periodo=7|30|90). Graficos SVG/HTML hechos a mano (sin librerias): barras finas con
   la cifra en la punta, una sola linea de tendencia con reticula al pasar el cursor y tabla equivalente.
   Todos los numeros son FICTICIOS (banda obligatoria) salvo los pedidos de prueba de la sesion. */
import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as RPointerEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { notifyDemo } from "../../mockApi";
import { HiloCarmesi, MarcoMaqueta, Pantalla, SelloPropuesta, colones, desdeAhora, fecha, leerPedidosDemo, porcentaje } from "./comun";
import { CANALES, EMBUDO, KPI, MARGEN_DIA, MAS_VISTO, META, PERIODOS, PRODUCTOS, TENDENCIA, pedidosEjemplo, type Periodo } from "./datos";
import { Dialogo, IC, Ico, Region, useMedia } from "./ui";
import "./gestion.css";

const nf = (n: number) => new Intl.NumberFormat("es-CR").format(n);
const miles = (n: number) => `₡${nf(n)} mil`;
type FilaMargen = { numero: string; cliente: string; venta: number; costo: number; margen: number; supuesto?: boolean; demo?: boolean };

/* reparto determinista de un total en 7 dias (mismas barras en cada visita) */
function porDia(total: number, semilla: number): number[] {
  let s = semilla * 9301 + 49297;
  const w = Array.from({ length: 7 }, () => { s = (s * 9301 + 49297) % 233280; return 0.7 + (s / 233280) * 0.8; });
  const sw = w.reduce((a, b) => a + b, 0);
  const out = w.map((x) => Math.floor((x / sw) * total));
  out[6] += total - out.reduce((a, b) => a + b, 0);
  return out;
}
const dias7 = () => Array.from({ length: 7 }, (_, i) => desdeAhora(i - 6));

export default function C1Tablero() {
  const [sp, setSp] = useSearchParams();
  const per: Periodo = (PERIODOS as string[]).includes(sp.get("periodo") || "") ? (sp.get("periodo") as Periodo) : "30";
  const setPer = (p: Periodo) => { const n = new URLSearchParams(sp); n.set("periodo", p); setSp(n, { replace: true }); };
  const k = KPI[per];
  const [detalle, setDetalle] = useState<{ tipo: "producto"; id: number } | { tipo: "pedido"; fila: FilaMargen } | null>(null);
  const [alertas, setAlertas] = useState({ nuevo: true, comprobante: true, stock: false });
  const movil = useMedia("(max-width: 767px)");

  const demos = useMemo(() => leerPedidosDemo(), []);
  const margenes: FilaMargen[] = useMemo(() => {
    const ej = pedidosEjemplo().filter((o) => o.margen != null).map((o) => {
      const venta = Math.round(o.subtotal);
      return { numero: o.numero, cliente: o.cliente.company || o.cliente.name, venta, costo: Math.round(venta * (1 - (o.margen as number))), margen: o.margen as number };
    });
    const dm = demos.filter((d) => d.status !== "cancelado").map((d) => ({ numero: d.number, cliente: d.contact.name || "Cliente de prueba", venta: Math.round(d.subtotal), costo: Math.round(d.subtotal * 0.65), margen: 0.35, supuesto: true, demo: true }));
    return [...dm, ...ej];
  }, [demos]);
  const hilo = demos[0] ? { numero: demos[0].number, token: demos[0].token } : {};
  const total = (xs: [string, number][]) => Math.max(...xs.map((x) => x[1]));

  return (
    <MarcoMaqueta id="C1" etapa={2} titulo="Tablero de la tienda"
      bajada="Visitas, lo más visto, carritos abandonados, conversión, ventas por canal y margen real por pedido, en una sola pantalla."
      hoy="Los pedidos y el inventario web ya se gestionan en la plataforma; los números de la tienda se sacan a mano."
      conPropuesta="Un tablero que se actualiza solo y dice qué se vende, dónde se cae la compra y cuánto deja cada pedido."
      aparte={<>
        <p><b>C1 no suma plata en el modelo de rentabilidad:</b> es el tablero que va a decir si los supuestos se cumplen.</p>
        <p>Con tres meses de datos reales, los ejemplos se reemplazan por los números de Crimson.</p>
        <p><Link to="/rentabilidad" className="ges-link">Ver el modelo en ¿Es rentable? →</Link></p>
      </>}>
      <Pantalla superficie="admin" seccion="Tablero de la tienda">
        <div className="ges ges-adm ges-c1">
          <div className="page-head">
            <div><div className="meta">Tablero de la tienda · propuesta C1</div><h2 className="h1">Tablero de la tienda</h2></div>
            <div className="page-head__actions">
              <div className="tabs" role="group" aria-label="Periodo">
                {PERIODOS.map((p) => <button type="button" key={p} aria-pressed={per === p} className={per === p ? "is-active" : ""} onClick={() => setPer(p)}>{p} días</button>)}
              </div>
            </div>
          </div>
          <p className="ges-banda" role="note"><Ico d={IC.alert} /><b>Datos ficticios para mostrar el tablero: no son ventas de Crimson.</b></p>

          <ul className="ges-kpis" aria-label={`Indicadores de los últimos ${per} días`}>
            <li className="ges-kpi ges-kpi--hero"><span className="ges-kpi__l">Ventas sin IVA</span><b className="ges-kpi__v">{colones(k.ventas)}</b><span className="ges-kpi__s">tienda en línea · {per} días</span></li>
            <li className="ges-kpi"><span className="ges-kpi__l">Visitas</span><b className="ges-kpi__v">{nf(k.visitas)}</b></li>
            <li className="ges-kpi"><span className="ges-kpi__l">Pedidos</span><b className="ges-kpi__v">{nf(k.pedidos)}</b></li>
            <li className="ges-kpi"><span className="ges-kpi__l">Conversión</span><b className="ges-kpi__v">{porcentaje(k.pedidos / k.visitas, 2)}</b></li>
            <li className="ges-kpi"><span className="ges-kpi__l">Monto promedio</span><b className="ges-kpi__v">{colones(Math.round(k.ventas / k.pedidos / 1000) * 1000)}</b></li>
            <li className="ges-kpi"><span className="ges-kpi__l">Carritos abandonados</span><b className="ges-kpi__v">{k.abandonados}</b><span className="ges-kpi__s">{k.conContacto} con contacto</span></li>
            <li className="ges-kpi"><span className="ges-kpi__l">Margen real</span><b className="ges-kpi__v">{porcentaje(k.margen)}</b><span className="ges-kpi__s">meta {porcentaje(META, 0)}</span></li>
          </ul>

          <div className="ges-c1-grid">
            <section className="card ges-card" aria-labelledby="c1-embudo">
              <div className="card__head"><h3 id="c1-embudo" className="ges-card-t">Embudo de compra</h3><span className="meta">{per} días</span></div>
              <div className="card__body">
                <ol className="ges-barras" aria-label="Embudo de compra">
                  {EMBUDO[per].map(([t, v], i, arr) => (
                    <li key={t}>
                      <span className="ges-barras__t">{t}</span>
                      <span className="ges-barras__fila">
                        <span className="ges-barras__bar" style={{ "--p": Math.max(0.015, v / arr[0][1]) } as CSSProperties} data-final={i === arr.length - 1 ? "1" : undefined} />
                        <b className="ges-barras__v">{nf(v)}</b>
                      </span>
                      {i > 0 && <span className="ges-barras__m">{porcentaje(v / arr[i - 1][1], 0)} del paso anterior</span>}
                    </li>
                  ))}
                </ol>
                <p className="ges-a2"><SelloPropuesta id="A2" tamano="chico" /><span>{k.abandonados} carritos abandonados · {k.conContacto} dejaron su contacto: con A2 se les escribe a las 24 h.</span></p>
              </div>
            </section>

            <section className="card ges-card" aria-labelledby="c1-canal">
              <div className="card__head"><h3 id="c1-canal" className="ges-card-t">Ventas por canal</h3><span className="meta">sin IVA · {per} días</span></div>
              <div className="card__body">
                <ol className="ges-barras" aria-label="Ventas por canal">
                  {CANALES[per].map(([t, v]) => (
                    <li key={t}>
                      <span className="ges-barras__t">{t}</span>
                      <span className="ges-barras__fila">
                        <span className="ges-barras__bar" style={{ "--p": Math.max(0.015, v / total(CANALES[per])) } as CSSProperties} data-final={t === "Tienda en línea" ? "1" : undefined} />
                        <b className="ges-barras__v">{colones(v)}</b>
                      </span>
                      <span className="ges-barras__m">{porcentaje(v / CANALES[per].reduce((a, x) => a + x[1], 0), 0)} del total</span>
                    </li>
                  ))}
                </ol>
              </div>
            </section>
          </div>

          <section className="card ges-card" aria-labelledby="c1-visto">
            <div className="card__head"><h3 id="c1-visto" className="ges-card-t">Lo más visto</h3><span className="meta">{per} días · tocá una fila</span></div>
            {movil ? (
              <ul className="ges-tarjetas">
                {MAS_VISTO[per].map(([id, v, c, s], i) => (
                  <li key={id} className="ges-tarjeta">
                    <button type="button" className="ges-tarjeta__btn" onClick={() => setDetalle({ tipo: "producto", id })}>
                      <span className="mono ges-dest__n">{i + 1}</span><b className="ges-prod">{PRODUCTOS[id].nombre}</b>
                    </button>
                    <dl><dt>Vistas</dt><dd className="mono">{nf(v)}</dd><dt>Al carrito</dt><dd className="mono">{c}</dd><dt>Vendidas</dt><dd className="mono">{s}</dd></dl>
                  </li>
                ))}
              </ul>
            ) : (
              <Region label="Lo más visto">
                <table className="table ges-table ges-click">
                  <thead><tr><th scope="col">Producto</th><th scope="col" className="num">Vistas</th><th scope="col" className="num">Al carrito</th><th scope="col" className="num">Vendidas</th></tr></thead>
                  <tbody>{MAS_VISTO[per].map(([id, v, c, s], i) => (
                    <tr key={id} onClick={() => setDetalle({ tipo: "producto", id })}>
                      <td><button type="button" className="ges-rowbtn" onClick={(e) => { e.stopPropagation(); setDetalle({ tipo: "producto", id }); }}><span className="mono ges-dest__n">{i + 1}</span><span className="ges-prod">{PRODUCTOS[id].nombre}</span></button></td>
                      <td className="num mono">{nf(v)}</td><td className="num mono">{c}</td><td className="num mono">{s}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </Region>
            )}
          </section>

          <section className="card ges-card" aria-labelledby="c1-margen">
            <div className="card__head"><h3 id="c1-margen" className="ges-card-t">Margen real por pedido</h3><span className="meta">pedidos web recientes</span></div>
            <div className="card__body ges-formula"><span className="meta">Fórmula</span> Margen sobre venta = (venta sin IVA − costo) ÷ venta sin IVA · meta {porcentaje(META, 0)}</div>
            <Region label="Margen real por pedido" className="ges-region--fija">
              <table className="table ges-table ges-margen">
                <thead><tr><th scope="col">Pedido</th><th scope="col">Cliente</th><th scope="col" className="num">Venta sin IVA</th><th scope="col" className="num">Costo</th><th scope="col">Margen</th></tr></thead>
                <tbody>{margenes.map((f) => {
                  const bajo = f.margen < META - 0.0001;
                  return [
                    <tr key={f.numero} data-bajo={bajo ? "1" : undefined}>
                      <th scope="row"><button type="button" className="ges-rowbtn" onClick={() => setDetalle({ tipo: "pedido", fila: f })}><span className="mono ges-nowrap">{f.demo ? `Tu pedido de prueba ${f.numero}` : f.numero}</span></button></th>
                      <td className="ges-cli">{f.cliente}</td>
                      <td className="num money">{colones(f.venta)}</td>
                      <td className="num money">{colones(f.costo)}</td>
                      <td>
                        <span className="ges-mbar" aria-hidden="true"><span style={{ width: `${Math.min(100, (f.margen / 0.5) * 100)}%` }} data-bajo={bajo ? "1" : undefined} /><i style={{ left: `${(META / 0.5) * 100}%` }} /></span>
                        <span className="mono ges-mval">{porcentaje(f.margen)}</span>
                        {bajo && <span className="ges-bajo"><Ico d={IC.alert} size={13} />bajo el {porcentaje(META, 0)}</span>}
                        {f.supuesto && <span className="ges-sup">margen supuesto</span>}
                      </td>
                    </tr>,
                    bajo && f.numero === "ORD-000125" ? (
                      <tr key={`${f.numero}-c3`} className="ges-c3"><td colSpan={5}>
                        <div className="ges-c3__in">
                          <SelloPropuesta id="C3" tamano="chico" />
                          <p>El costo subió y el precio web no se actualizó. Precio sugerido para volver al 35 %: <b>{colones(66355)} sin IVA</b> ({colones(74981)} con IVA).</p>
                          <button type="button" className="btn btn--soft btn--sm" onClick={() => notifyDemo({ title: "Propuesta C3", text: "Con C3, este botón cambiaría el precio web del Kit Easylink WiFi a ₡66 355 sin IVA, cuidando el margen de 35 %. Esta propuesta no está incluida aún." })}>Aplicar precio sugerido</button>
                        </div>
                      </td></tr>
                    ) : null,
                  ];
                })}</tbody>
              </table>
            </Region>
            <p className="ges-sm muted ges-pad">Costo = venta × (1 − margen). Los márgenes de los pedidos de ejemplo son ficticios.</p>
          </section>

          <div className="ges-c1-grid">
            <section className="card ges-card" aria-labelledby="c1-alertas">
              <div className="card__head"><h3 id="c1-alertas" className="ges-card-t">Alertas al celular</h3><SelloPropuesta id="C2" tamano="chico" /></div>
              <div className="card__body ges-alertas">
                <fieldset>
                  <legend className="ges-lbl">Avisarme al celular:</legend>
                  <label className="ges-check"><input type="checkbox" checked={alertas.nuevo} onChange={(e) => setAlertas({ ...alertas, nuevo: e.target.checked })} />Pedido nuevo</label>
                  <label className="ges-check"><input type="checkbox" checked={alertas.comprobante} onChange={(e) => setAlertas({ ...alertas, comprobante: e.target.checked })} />Comprobante subido</label>
                  <label className="ges-check"><input type="checkbox" checked={alertas.stock} onChange={(e) => setAlertas({ ...alertas, stock: e.target.checked })} />Pocas existencias de lo más vendido</label>
                </fieldset>
                <div className="ges-notif" aria-label="Vista previa de la notificación" role="group">
                  {alertas.nuevo ? (
                    <div className="ges-notif__card"><span className="ges-notif__app"><Ico d={IC.bell} size={14} />Crimson · ahora</span><b>Pedido nuevo ORD-000129</b><span>{colones(62484)} · SINPE Móvil</span></div>
                  ) : alertas.comprobante ? (
                    <div className="ges-notif__card"><span className="ges-notif__app"><Ico d={IC.bell} size={14} />Crimson · ahora</span><b>Comprobante subido · ORD-000128</b><span>{colones(238607)} · revisalo en Pedidos</span></div>
                  ) : alertas.stock ? (
                    <div className="ges-notif__card"><span className="ges-notif__app"><Ico d={IC.bell} size={14} />Crimson · ahora</span><b>Pocas existencias</b><span>Grabador IP NVR Hikvision 4CH PoE · queda 1</span></div>
                  ) : <p className="ges-sm muted">Sin avisos encendidos.</p>}
                  <span className="ges-sm muted">Mensaje de ejemplo: el canal (WhatsApp, SMS o aviso del navegador) está por definir.</span>
                </div>
              </div>
            </section>
            <section className="card ges-card" aria-labelledby="c1-prov">
              <div className="card__head"><h3 id="c1-prov" className="ges-card-t">Proveedor</h3><SelloPropuesta id="D1" tamano="chico" /></div>
              <div className="card__body ges-prov-c1">
                <p><b>Proveedor: Eurocomp (SiReTT)</b> · disponibilidad y costos al día — depende de la llave de API.</p>
                <p className="ges-sm muted">Hoy el dato del proveedor se carga a mano en Inventario web, con su fecha.</p>
                <Link to="/detras-de-escena/inventario" className="ges-link">Ver Inventario web →</Link>
              </div>
            </section>
          </div>

          <Tendencia />
          <HiloCarmesi aqui="tablero" {...hilo} />
        </div>
      </Pantalla>

      {detalle?.tipo === "producto" && <DetalleProducto id={detalle.id} onCerrar={() => setDetalle(null)} />}
      {detalle?.tipo === "pedido" && <DetallePedido f={detalle.fila} onCerrar={() => setDetalle(null)} />}
    </MarcoMaqueta>
  );
}

// ---------------------------------------------------------------- tendencia (12 semanas)
function Tendencia() {
  // el viewBox mide lo mismo que la caja (1 unidad = 1 px): los rotulos quedan legibles en 390 px y en 1920 px
  const caja = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  useEffect(() => {
    const el = caja.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => { const w = Math.round(e.contentRect.width); if (w > 0) setW((v) => (Math.abs(v - w) > 2 ? w : v)); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const angosto = W < 480;
  const H = angosto ? 220 : 240, L = angosto ? 66 : 72, R = angosto ? 24 : 40, T = 30, B = 34;
  const max = Math.ceil(Math.max(...TENDENCIA) / 200) * 200;
  const x = (i: number) => L + (i * (W - L - R)) / (TENDENCIA.length - 1);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const semanas = TENDENCIA.map((_, i) => desdeAhora(-7 * (TENDENCIA.length - 1 - i)));
  const [sel, setSel] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  // compuerta de rAF: un mouse de alta tasa manda cientos de eventos por segundo; se mide y se pinta 1 vez por cuadro
  const cx = useRef(0);
  const raf = useRef(0);
  useEffect(() => () => cancelAnimationFrame(raf.current), []);
  const mover = (e: RPointerEvent<SVGSVGElement>) => {
    cx.current = e.clientX;
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = 0;
      const r = svg.current?.getBoundingClientRect();
      if (!r || !r.width) return;
      const px = ((cx.current - r.left) / r.width) * W;
      setSel(Math.max(0, Math.min(TENDENCIA.length - 1, Math.round(((px - L) / (W - L - R)) * (TENDENCIA.length - 1)))));
    });
  };
  const salir = () => { cancelAnimationFrame(raf.current); raf.current = 0; setSel(null); };
  const d = TENDENCIA.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const ult = TENDENCIA.length - 1;
  const desc = `Ventas de la tienda por semana, últimas 12 semanas, en miles de colones sin IVA (datos ficticios): de ${miles(TENDENCIA[0])} a ${miles(TENDENCIA[ult])}; la más alta, ${miles(Math.max(...TENDENCIA))}.`;
  return (
    <section className="card ges-card" aria-labelledby="c1-tend">
      <div className="card__head"><h3 id="c1-tend" className="ges-card-t">Tendencia: ventas de la tienda por semana</h3><span className="meta">12 semanas · miles de ₡ sin IVA</span></div>
      <div className="card__body ges-tend">
        <div className="ges-tend__plot" ref={caja}>
          <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby="c1-tend-t c1-tend-d" onPointerMove={mover} onPointerDown={mover} onPointerLeave={salir}>
            <title id="c1-tend-t">Tendencia de 12 semanas</title>
            <desc id="c1-tend-d">{desc}</desc>
            {Array.from({ length: max / 200 + 1 }, (_, i) => i * 200).map((v) => (
              <g key={v}>
                <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="rgba(21,19,26,.10)" strokeWidth="1" />
                <text x={L - 8} y={y(v) + 4} textAnchor="end" className="ges-ax">{v === 0 ? "₡0" : `₡${v} mil`}</text>
              </g>
            ))}
            {semanas.map((s, i) => ((angosto ? i % 4 === 0 || i === TENDENCIA.length - 1 : i % 3 === 2 || i === 0) ? <text key={i} x={x(i)} y={H - 10} textAnchor="middle" className="ges-ax">{fecha(s)}</text> : null))}
            <path d={`${d} L${x(ult)},${y(0)} L${x(0)},${y(0)} Z`} fill="rgba(196,26,47,.08)" />
            <path d={d} fill="none" stroke="#c41a2f" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            <circle cx={x(ult)} cy={y(TENDENCIA[ult])} r="5" fill="#c41a2f" stroke="#fff" strokeWidth="2" />
            <text x={x(ult) - 8} y={y(TENDENCIA[ult]) + (TENDENCIA[ult - 1] > TENDENCIA[ult] ? 22 : -14)} textAnchor="end" className="ges-ax ges-ax--v">{miles(TENDENCIA[ult])}</text>
            {sel != null && (
              <g>
                <line x1={x(sel)} x2={x(sel)} y1={T} y2={H - B} stroke="#15131a" strokeOpacity=".45" strokeWidth="1" />
                <circle cx={x(sel)} cy={y(TENDENCIA[sel])} r="5" fill="#c41a2f" stroke="#fff" strokeWidth="2" />
              </g>
            )}
          </svg>
          {sel != null && (
            <div className="ges-tip" style={{ left: Math.min(Math.max(x(sel), 74), W - 74), top: y(TENDENCIA[sel]) }} aria-hidden="true">
              <span>Semana del {fecha(semanas[sel])}</span><b>{miles(TENDENCIA[sel])}</b>
            </div>
          )}
        </div>
        <details className="ges-tabla-eq">
          <summary>Ver los números en tabla</summary>
          <Region label="Ventas por semana">
            <table className="table ges-table">
              <thead><tr><th scope="col">Semana del</th><th scope="col" className="num">Ventas sin IVA</th></tr></thead>
              <tbody>{TENDENCIA.map((v, i) => <tr key={i}><td>{fecha(semanas[i])}</td><td className="num money">{miles(v)}</td></tr>)}</tbody>
            </table>
          </Region>
        </details>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- detalles (7 barras por dia)
function Columnas({ valores, etiqueta, fmt, meta, titulo }: { valores: number[]; etiqueta: string; fmt: (v: number) => string; meta?: number; titulo: string }) {
  const max = Math.max(...valores, meta ?? 0) * 1.12 || 1;
  const ds = dias7();
  return (
    <figure className="ges-cols">
      <figcaption className="ges-lbl">{titulo}</figcaption>
      <div className="ges-cols__plot" role="img" aria-label={`${etiqueta}: ${valores.map((v, i) => `${fecha(ds[i], "dia")} ${fmt(v)}`).join("; ")}`}>
        {meta != null && <span className="ges-cols__meta" style={{ bottom: `${(meta / max) * 100}%` }}><span>meta {fmt(meta)}</span></span>}
        {valores.map((v, i) => (
          <span key={i} className="ges-cols__c">
            <b className="ges-cols__v">{fmt(v)}</b>
            <span className="ges-cols__bar" style={{ height: `${(v / max) * 100}%` }} data-bajo={meta != null && v < meta - 0.0001 ? "1" : undefined} />
            <span className="ges-cols__d">{fecha(ds[i], "dia").split(" ")[0]}</span>
          </span>
        ))}
      </div>
    </figure>
  );
}
function DetalleProducto({ id, onCerrar }: { id: number; onCerrar: () => void }) {
  const p = PRODUCTOS[id];
  const fila = MAS_VISTO["7"].find((r) => r[0] === id);
  const v = porDia(fila?.[1] || 50, id);
  return (
    <Dialogo titulo={p.nombre} onCerrar={onCerrar} pie={<><Link to={p.path} className="btn btn--ghost btn--sm">Ver la ficha en la tienda</Link><button type="button" className="btn btn--crimson btn--sm" onClick={onCerrar}>Listo</button></>}>
      <div className="ges ges-det">
        <p className="ges-banda ges-banda--chica" role="note">Datos ficticios para mostrar el tablero.</p>
        <Columnas valores={v} etiqueta="Vistas por día, últimos 7 días" fmt={nf} titulo="Vistas por día · últimos 7 días" />
        <dl className="ges-det__dl">
          <dt>Vistas (7 días)</dt><dd className="mono">{nf(fila?.[1] || 0)}</dd>
          <dt>Al carrito</dt><dd className="mono">{fila?.[2] ?? 0}</dd>
          <dt>Vendidas</dt><dd className="mono">{fila?.[3] ?? 0}</dd>
          <dt>Precio con IVA</dt><dd className="money">{colones(p.conIva)}</dd>
          <dt>Disponibilidad</dt><dd>{p.disponible ? "Disponible" : "Bajo pedido"}</dd>
        </dl>
      </div>
    </Dialogo>
  );
}
function DetallePedido({ f, onCerrar }: { f: FilaMargen; onCerrar: () => void }) {
  const util = f.venta - f.costo;
  const meta = Math.round(f.costo / (1 - META));
  const bajo = f.margen < META - 0.0001;
  return (
    <Dialogo titulo={`Margen de ${f.demo ? "tu pedido de prueba " : ""}${f.numero}`} onCerrar={onCerrar} pie={<><Link to={`/detras-de-escena/pedidos?pedido=${encodeURIComponent(f.numero)}`} className="btn btn--ghost btn--sm">Ver el pedido en Pedidos</Link><button type="button" className="btn btn--crimson btn--sm" onClick={onCerrar}>Listo</button></>}>
      <div className="ges ges-det">
        <dl className="ges-det__dl">
          <dt>Cliente</dt><dd>{f.cliente}</dd>
          <dt>Venta sin IVA</dt><dd className="money">{colones(f.venta)}</dd>
          <dt>Costo</dt><dd className="money">{colones(f.costo)}</dd>
          <dt>Utilidad bruta</dt><dd className="money">{colones(util)}</dd>
          <dt>Margen sobre venta</dt><dd className="mono">{porcentaje(f.margen)}{f.supuesto ? " (supuesto)" : ""}</dd>
          <dt>Precio para el {porcentaje(META, 0)}</dt><dd className="money">{colones(meta)}{bajo ? ` (faltan ${colones(meta - f.venta)})` : " · en meta"}</dd>
        </dl>
        <Columnas valores={MARGEN_DIA} etiqueta="Margen real de los pedidos web por día, últimos 7 días" fmt={(v) => porcentaje(v)} meta={META} titulo="Margen de los pedidos web por día · últimos 7 días (ficticio)" />
      </div>
    </Dialogo>
  );
}
