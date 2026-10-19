/* Detras de escena · Pedidos de la tienda (ya construido, datos de ejemplo). Imita store/Orders.tsx: pestanas por
   estado con conteo, filtros en memoria, detalle con comprobante privado, solo los saltos que acepta el API
   (store_orders.next_states) con los rotulos reales, factura automatica al confirmar el pago, correo al cliente
   (simulado), historial y bitacora de auditoria. Los pedidos de prueba de Andres salen primero y lo que Crimson hace
   con ellos lo ve el cliente en "Mi pedido" (escribirEstadoDemo). Pantalla de trabajo: nada se mueve solo. */
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { notifyDemo } from "../../mockApi";
import { HiloCarmesi, MarcoMaqueta, Pantalla, SelloPropuesta, colones, fecha, hora, leerPedidosDemo } from "./comun";
import { ACCION, CAMINO_128, ES_ESTADO, ETIQUETA, METODO, PESTANAS, POR_ATENDER, siguientes, type Estado, type PedidoGes } from "./datos";
import { cargarPedidos, demoAGes, escribirParaCliente, estadoParaCliente, guardarPedidos, reducer, type Aplicar } from "./estadoPedidos";
import { Dialogo, IC, Ico, Region, SubnavDE, cuando, isoDia, relativo, useMedia } from "./ui";
import "../../../modules/store/orders.css";
import "./gestion.css";

const quien = (o: PedidoGes) => (o.cliente.company ? `${o.cliente.company} (${o.cliente.name})` : o.cliente.name);
const entregaTxt = (o: PedidoGes) => (o.entrega.type === "retiro" ? "Retiro" : o.entrega.method || "—");
const lugar = (o: PedidoGes) => (o.entrega.canton ? `${o.entrega.canton}, ${o.entrega.province}` : null);

const CERRADO: Estado[] = ["cancelado", "reembolsado", "enviado", "entregado"];
const comprar = (o: PedidoGes) => !!o.requiereCompra && !CERRADO.includes(o.status);
function EstadoCelda({ o, primera, soloCompra = false }: { o: PedidoGes; primera?: string; soloCompra?: boolean }) {
  return (
    <div className="ges-estado">
      {!soloCompra && <span className="wo-badge" data-s={o.status}>{ETIQUETA[o.status]}</span>}
      {comprar(o) && (
        <span className="ges-compra" id={o.numero === primera ? "ges-requiere-compra" : undefined}>
          <span>Requiere compra: {o.requiereCompra}</span>
          <SelloPropuesta id="D2" tamano="chico" />
        </span>
      )}
    </div>
  );
}
const Prueba = ({ o }: { o: PedidoGes }) => (o.demo ? <span className="ges-prueba">Tu pedido de prueba</span> : null);

export default function Pedidos() {
  const [st, dispatch] = useReducer(reducer, undefined, cargarPedidos);
  const [tick, setTick] = useState(0);
  useEffect(() => { guardarPedidos(st); }, [st]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const demos = useMemo(() => leerPedidosDemo(), [tick]);
  const todos = useMemo(() => [...demos.map((d) => demoAGes(d, st.extras[d.number])), ...st.ejemplo], [demos, st]);

  const [canal, setCanal] = useState<"web" | "otros">("web");
  const [estado, setEstado] = useState<string>("");
  const [metodo, setMetodo] = useState("");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [q, setQ] = useState("");
  const [selNum, setSelNum] = useState<string | null>(null);
  const [aviso, setAviso] = useState("");
  const [sp, setSp] = useSearchParams();
  const movil = useMedia("(max-width: 767px)");
  const sel = selNum ? todos.find((o) => o.numero.toLowerCase() === selNum.toLowerCase()) || null : null;

  // ?pedido=ORD-000128&estado=<k> (lo usa el control de "Mi pedido") · ?pedido=DEMO-xxxx (el hilo carmesi)
  const spKey = sp.toString();
  useEffect(() => {
    const ped = sp.get("pedido");
    if (!ped) return;
    const k = sp.get("estado");
    if (/^ord-?0*128$/i.test(ped) && k && ES_ESTADO(k)) dispatch({ t: "estado128", k, ahora: Date.now() });
    setTick((x) => x + 1);
    setSelNum(/^ord-?0*128$/i.test(ped) ? "ORD-000128" : ped);
  }, [spKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const cerrar = () => {
    setSelNum(null);
    if (sp.has("pedido") || sp.has("estado")) { const n = new URLSearchParams(sp); n.delete("pedido"); n.delete("estado"); setSp(n, { replace: true }); }
  };
  const aplicar = (o: PedidoGes, p: Aplicar) => {
    const ahora = Date.now();
    if (o.demo && o.token) {
      const base = leerPedidosDemo().find((d) => d.token === o.token);
      escribirParaCliente(o.token, estadoParaCliente(o, base, p.a, ahora, p));
    }
    dispatch({ t: "aplicar", numero: o.numero, demo: o.demo ? o : null, p, ahora });
    setTick((x) => x + 1);
    setAviso(`${o.numero}: ${ETIQUETA[p.a]}.${p.a === "pagado" && !o.factura ? " Factura electrónica emitida automáticamente (simulada)." : ""}${p.avisar ? " Correo al cliente: simulado en la vista previa." : ""}`);
  };

  const conteo = useMemo(() => { const c: Record<string, number> = {}; todos.forEach((o) => { c[o.status] = (c[o.status] || 0) + 1; }); return c; }, [todos]);
  const porAtender = todos.filter((o) => POR_ATENDER.includes(o.status)).length;
  const lista = useMemo(() => {
    const t = q.trim().toLowerCase();
    return todos.filter((o) => {
      if (estado === "atender" ? !POR_ATENDER.includes(o.status) : estado && o.status !== estado) return false;
      if (metodo && o.pago.kind !== metodo) return false;
      const dia = isoDia(o.created_at);
      if (desde && dia < desde) return false;
      if (hasta && dia > hasta) return false;
      if (t && ![o.numero, o.cliente.name, o.cliente.company || "", o.cliente.email].some((x) => x.toLowerCase().includes(t))) return false;
      return true;
    });
  }, [todos, estado, metodo, desde, hasta, q]);
  const primeraCompra = lista.find(comprar)?.numero;

  return (
    <MarcoMaqueta id="DE" estado="existente" titulo="Pedidos de la tienda"
      bajada="Así llega cada compra a Crimson: comprobante privado, confirmar pago, factura automática, estados con aviso al cliente y despacho. Ya existe; aquí con datos de ejemplo.">
      <SubnavDE actual="pedidos" />
      <Pantalla superficie="admin" seccion="Pedidos">
        <div className="ges ges-adm" id="ges-pedidos">
          <div className="page-head">
            <div><div className="meta">11 · Pedidos</div><h2 className="h1">Pedidos de la tienda</h2></div>
            <div className="page-head__actions">
              <div className="tabs" role="group" aria-label="Canal">
                <button type="button" aria-pressed={canal === "web"} className={canal === "web" ? "is-active" : ""} onClick={() => setCanal("web")}>Tienda en línea</button>
                <button type="button" aria-pressed={canal === "otros"} className={canal === "otros" ? "is-active" : ""} onClick={() => setCanal("otros")}>Otros canales</button>
              </div>
            </div>
          </div>

          {canal === "otros" ? (
            <div className="card"><div className="empty"><b className="h3">Otros canales</b><span>Pedidos de eventos y del punto de venta aparecen aquí.</span></div></div>
          ) : (
            <div className="card ges-card">
              <div className="wo-tabs">
                <div className="tabs ges-tabs" role="group" aria-label="Filtrar por estado">
                  <span className="ges-tabs__c2">
                    <button type="button" id="ges-por-atender" aria-pressed={estado === "atender"} className={estado === "atender" ? "is-active" : ""} onClick={() => setEstado("atender")}>Por atender<i>{porAtender}</i></button>
                    <SelloPropuesta id="C2" tamano="chico" />
                  </span>
                  <button type="button" aria-pressed={estado === ""} className={estado === "" ? "is-active" : ""} onClick={() => setEstado("")}>Todos<i>{todos.length}</i></button>
                  {PESTANAS.map((s) => (
                    <button type="button" key={s} aria-pressed={estado === s} className={estado === s ? "is-active" : ""} onClick={() => setEstado(s)}>{ETIQUETA[s]}<i>{conteo[s] || ""}</i></button>
                  ))}
                </div>
              </div>
              <div className="wo-filters">
                <input className="input input--search" type="search" placeholder="Buscar número, nombre, correo o teléfono" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar pedidos" />
                <select className="select" value={metodo} onChange={(e) => setMetodo(e.target.value)} aria-label="Método de pago"><option value="">Todos los métodos</option>{Object.entries(METODO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                <label className="ges-fecha">Desde<input className="input" type="date" value={desde} onChange={(e) => setDesde(e.target.value)} /></label>
                <label className="ges-fecha">Hasta<input className="input" type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} /></label>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setTick((x) => x + 1); setAviso("Lista actualizada con los pedidos de esta sesión."); }}><Ico d={IC.refresh} />Refrescar</button>
              </div>
              {lista.length === 0 ? (
                <div className="empty"><span>{estado === "atender" ? "No hay pedidos esperando acción. ¡Al día!" : "No hay pedidos con ese filtro."}</span></div>
              ) : movil ? (
                <ul className="ges-wocards" aria-label="Pedidos">
                  {lista.map((o) => (
                    <li key={o.numero}>
                      <button type="button" className="wo-card ges-wocard" onClick={() => setSelNum(o.numero)} aria-label={`Ver pedido ${o.numero} de ${quien(o)}, ${ETIQUETA[o.status]}, ${colones(o.total)}`}>
                        <span className="ges-wocard__top"><b>{o.numero}</b><span className="wo-badge" data-s={o.status}>{ETIQUETA[o.status]}</span></span>
                        <span>{quien(o)}</span><span className="money">{colones(o.total)}</span>
                        <span className="muted">{cuando(o.created_at)} · {METODO[o.pago.kind] || o.pago.label}</span><span className="muted">{o.entrega.type === "retiro" ? "Retiro" : "Envío"}</span>
                      </button>
                      {(o.demo || comprar(o)) && <div className="ges-wocard__extra"><Prueba o={o} />{comprar(o) && <EstadoCelda o={o} primera={primeraCompra} soloCompra />}</div>}
                    </li>
                  ))}
                </ul>
              ) : (
                <Region label="Pedidos de la tienda en línea">
                  <table className="table wo-table ges-table">
                    <thead><tr><th scope="col">Pedido</th><th scope="col">Fecha</th><th scope="col">Cliente</th><th scope="col">Entrega</th><th scope="col">Pago</th><th scope="col" className="num">Total</th><th scope="col">Estado</th><th scope="col"><span className="ges-sr">Acciones</span></th></tr></thead>
                    <tbody>{lista.map((o) => (
                      <tr key={o.numero} data-demo={o.demo ? "1" : undefined}>
                        <td><span className="mono ges-num">{o.numero}</span><Prueba o={o} /></td>
                        <td className="muted ges-nowrap">{fecha(o.created_at)}<div className="meta ges-meta-n">{hora(o.created_at)}</div></td>
                        <td className="ges-cli">{quien(o)}<div className="meta ges-meta-n">{o.cliente.email}</div></td>
                        <td className="muted">{entregaTxt(o)}{lugar(o) && <div className="meta ges-meta-n">{lugar(o)}</div>}</td>
                        <td className="muted">{METODO[o.pago.kind] || o.pago.label}{o.proofs.length > 0 && <div className="meta ges-meta-n">{o.proofs.length} comprobante(s)</div>}</td>
                        <td className="num money">{colones(o.total)}</td>
                        <td><EstadoCelda o={o} primera={primeraCompra} /></td>
                        <td className="num"><button type="button" className="btn btn--ghost btn--sm" onClick={() => setSelNum(o.numero)} aria-label={`Ver pedido ${o.numero}`}>Ver</button></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </Region>
              )}
            </div>
          )}
          <div className="ges-pie-adm">
            <p className="ges-nota">Datos de ejemplo. Tocá «Ver» y probá cualquier acción: nada sale de tu navegador.</p>
            <button type="button" className="btn btn--soft btn--sm" onClick={() => { dispatch({ t: "restablecer", ahora: Date.now() }); setAviso("Se restablecieron los pedidos de ejemplo."); }}><Ico d={IC.refresh} />Restablecer datos de ejemplo</button>
          </div>
          <p className="ges-sr" role="status" aria-live="polite">{sel ? "" : aviso}</p>
        </div>
      </Pantalla>
      {sel && <Detalle key={sel.numero} o={sel} onCerrar={cerrar} onAplicar={aplicar} aviso={aviso} />}
    </MarcoMaqueta>
  );
}

// ---------------------------------------------------------------- detalle
function Comprobante({ o }: { o: PedidoGes }) {
  const p = o.proofs[0];
  const titulo = `Comprobante ${o.pago.kind === "transferencia" ? "de transferencia" : "SINPE Móvil"} de ejemplo · ${colones(o.total)}`;
  return (
    <figure className="ges-comp">
      <svg viewBox="0 0 280 360" role="img" aria-label={titulo} width="280" height="360">
        <title>{titulo}</title>
        <rect x="1" y="1" width="278" height="358" rx="14" fill="#fff" stroke="#15131a" strokeOpacity=".18" />
        <rect x="1" y="1" width="278" height="64" rx="14" fill="#15131a" />
        <rect x="1" y="50" width="278" height="15" fill="#15131a" />
        <text x="20" y="30" fill="#f4f1ee" fontFamily="Arial, Helvetica, sans-serif" fontSize="10" letterSpacing="1.4">BANCO DE EJEMPLO</text>
        <text x="20" y="50" fill="#fff" fontFamily="Arial, Helvetica, sans-serif" fontSize="15" fontWeight="700">{o.pago.kind === "transferencia" ? "Transferencia enviada" : "SINPE Móvil enviado"}</text>
        <circle cx="140" cy="108" r="22" fill="#157a41" />
        <path d="M129 108l7 7 14-14" stroke="#fff" strokeWidth="3.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <text x="140" y="164" textAnchor="middle" fill="#15131a" fontFamily="Arial, Helvetica, sans-serif" fontSize="28" fontWeight="700">{colones(o.total)}</text>
        {[["Para", "Crimson (ejemplo)"], ["Detalle", o.numero], ["Referencia", `EJ-${o.numero.replace(/\D/g, "").slice(-4)}-0001`], ["Fecha", p ? cuando(p.at) : "—"]].map(([k, v], i) => (
          <g key={k}>
            <text x="20" y={206 + i * 34} fill="#6f6a74" fontFamily="Arial, Helvetica, sans-serif" fontSize="9.5" letterSpacing="1.2">{k.toUpperCase()}</text>
            <text x="20" y={222 + i * 34} fill="#15131a" fontFamily="Arial, Helvetica, sans-serif" fontSize="13" fontWeight="600">{v}</text>
          </g>
        ))}
      </svg>
      <figcaption><b>{p?.filename || "comprobante-ejemplo.jpg"}</b> · Solo lo ve el equipo de Crimson.</figcaption>
    </figure>
  );
}

function Detalle({ o, onCerrar, onAplicar, aviso }: { o: PedidoGes; onCerrar: () => void; onAplicar: (o: PedidoGes, p: Aplicar) => void; aviso: string }) {
  const nav = useNavigate();
  const [act, setAct] = useState<Estado | null>(null);
  const [nota, setNota] = useState("");
  const [guia, setGuia] = useState(o.tracking_number || (o.numero === "ORD-000128" ? "EJ012844710CR" : ""));
  const [transp, setTransp] = useState(o.carrier || "Correos de Costa Rica");
  const [ref, setRef] = useState("");
  const [monto, setMonto] = useState(String(Math.round(o.total)));
  const [avisar, setAvisar] = useState(true);
  const [err, setErr] = useState("");
  const [verComp, setVerComp] = useState(false);
  const notaRef = useRef<HTMLInputElement>(null);
  const next = siguientes(o);
  const needNote = act === "cancelado" || (act === "nuevo" && o.status === "en_revision");
  const dv = o.entrega;

  const correr = () => {
    if (!act) return;
    if (needNote && !nota.trim()) {
      setErr(act === "cancelado" ? "Escribí el motivo de la cancelación" : "Escribí qué falló en el comprobante");
      notaRef.current?.focus();
      return;
    }
    onAplicar(o, { a: act, nota, ref, guia, transportista: transp, monto, avisar });
    setAct(null); setNota(""); setErr(""); setRef("");
  };
  const tokenEjemplo = CAMINO_128.includes(o.status) ? `ejemplo-${o.status}` : "ejemplo-enviado";
  const puedeVer = o.demo ? !!o.token : o.numero === "ORD-000128" && CAMINO_128.includes(o.status);
  const verCliente = () => {
    if (o.demo && o.token) {
      const base = leerPedidosDemo().find((d) => d.token === o.token);
      if (base && base.status !== o.status) escribirParaCliente(o.token, estadoParaCliente(o, base, o.status, Date.now(), { a: o.status, nota: "", ref: "", guia: o.tracking_number || "", transportista: o.carrier || "", monto: "", avisar: false }));
      nav(`/?pedido=${encodeURIComponent(o.token)}`);
    } else if (puedeVer) nav(`/?pedido=${tokenEjemplo}`);
  };
  const demo = (title: string, text: string) => notifyDemo({ title, text });

  const pie = (
    <div className="wo-foot">
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => demo("Imprimir", "En la plataforma real se abre la hoja del pedido lista para imprimir. En la vista previa no se genera ningún archivo.")}><Ico d={IC.print} />Imprimir</button>
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => demo("Enlace del cliente", "En la plataforma real se copia el enlace firmado de «Mi pedido» para mandárselo al cliente.")}><Ico d={IC.link} />Enlace del cliente</button>
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => demo("WhatsApp", "En la plataforma real se abre WhatsApp con un mensaje para el cliente sobre su pedido. Desde la vista previa no se envía nada.")}><Ico d={IC.wa} />WhatsApp</button>
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => demo("Reenviar correo", "En la plataforma real se reenvía al cliente el correo con el estado de su pedido. En la vista previa no se envía ningún correo.")}><Ico d={IC.mail} />Reenviar correo</button>
      {o.factura ? (
        <button type="button" className="btn btn--crimson btn--sm" onClick={() => demo(`Factura ${o.factura}`, "En la plataforma real se abre el comprobante electrónico de Hacienda (PDF y XML). Este número es de ejemplo.")}>Ver factura {o.factura}</button>
      ) : (
        <button type="button" className="btn btn--soft btn--sm" onClick={() => demo("Factura electrónica", "En la plataforma real se emite el comprobante electrónico (con cédula: factura; sin cédula: tiquete). Con la factura automática encendida sale sola al confirmar el pago.")}>Emitir comprobante</button>
      )}
    </div>
  );

  return (
    <Dialogo ancho="ancho" onCerrar={onCerrar} pie={pie}
      titulo={<>Pedido {o.numero} <span className="wo-badge" data-s={o.status}>{ETIQUETA[o.status]}</span>{o.demo && <span className="ges-prueba">Tu pedido de prueba</span>}</>}>
      <div className="ges ges-detalle wo-detail">
        <p className="ges-sr" role="status" aria-live="polite">{aviso}</p>
        <div className="wo-grid">
          <div className="wo-box"><span className="meta">Cliente</span><b>{o.cliente.company || o.cliente.name}</b>{o.cliente.company && <span>Atención: {o.cliente.name}</span>}<span>{o.cliente.email}</span>{o.cliente.id_number && <span className="mono">Céd. {o.cliente.id_number}</span>}</div>
          <div className="wo-box"><span className="meta">Entrega</span>
            {dv.type === "retiro" ? <b>Retiro en sitio</b> : <><b>{dv.method || "Envío"}</b><span>{[dv.district, dv.canton, dv.province].filter(Boolean).join(", ") || "—"}</span></>}
            {o.tracking_number && <span>Guía <b className="mono">{o.tracking_number}</b>{o.carrier ? ` · ${o.carrier}` : ""}{!o.demo && " (ejemplo)"}</span>}
          </div>
          <div className="wo-box"><span className="meta">Pago</span><b>{o.pago.label}</b>
            <span>{o.paid_at ? `Pagado ${cuando(o.paid_at)}` : o.status === "en_revision" ? "Comprobante en revisión" : o.expires_at ? `Pendiente · vence ${relativo(o.expires_at)}` : "Pendiente"}</span>
            {o.payment_ref && <span className="mono">Ref. {o.payment_ref}</span>}
            {o.factura && <span className="ges-fe">{o.facturaAuto ? `Factura electrónica ${o.factura} emitida automáticamente (simulada)` : `Factura ${o.factura} (ejemplo)`}</span>}
            {o.refund != null && <span>Reembolso registrado: {colones(o.refund)}</span>}
          </div>
        </div>
        {o.cancel_reason && <div className="wo-box"><span className="meta">Motivo de cancelación</span>{o.cancel_reason}</div>}
        {o.requiereCompra && <div className="wo-box ges-box-compra"><span className="meta">Inventario</span><span>Requiere compra: {o.requiereCompra}. Hoy se crea una solicitud interna de compra en borrador (ya funciona).</span><span className="ges-compra-d2"><SelloPropuesta id="D2" tamano="chico" /> Con D2 (y D1), la orden se armaría con los costos de Eurocomp y se enviaría al proveedor con un clic.</span></div>}
        {o.proofs.length > 0 && (
          <div className="wo-box"><span className="meta">Comprobantes (privados)</span>
            <div className="wo-proofs">
              <button type="button" className="btn btn--soft btn--sm" aria-expanded={verComp} onClick={() => setVerComp((v) => !v)}><Ico d={IC.doc} />{verComp ? "Ocultar comprobante" : "Ver comprobante"} · {o.proofs[0].filename} · {cuando(o.proofs[0].at)}</button>
            </div>
            {verComp && <Comprobante o={o} />}
          </div>
        )}

        {next.length > 0 && (
          <div className="wo-act">
            <div className="wo-act__row" role="group" aria-label="Acciones permitidas para este pedido">
              {next.map((s) => (
                <button type="button" key={s} aria-pressed={act === s}
                  className={`btn btn--sm ${act === s ? "btn--crimson" : s === "cancelado" || (s === "nuevo" && o.status === "en_revision") ? "btn--ghost" : "btn--soft"}`}
                  onClick={() => { setAct(act === s ? null : s); setErr(""); }}>{ACCION[s] || ETIQUETA[s]}</button>
              ))}
            </div>
            {act && (
              <>
                <div className="wo-act__row">
                  {act === "pagado" && <label className="field"><span className="ges-lbl">Referencia del pago (opcional)</span><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="N.º de comprobante SINPE / transferencia" /></label>}
                  {act === "enviado" && <>
                    <label className="field"><span className="ges-lbl">Número de guía (opcional)</span><input className="input input--mono ges-left" value={guia} onChange={(e) => setGuia(e.target.value)} /></label>
                    <label className="field"><span className="ges-lbl">Transportista</span><input className="input" value={transp} onChange={(e) => setTransp(e.target.value)} /></label>
                  </>}
                  {act === "reembolsado" && <label className="field"><span className="ges-lbl">Monto (CRC)</span><input className="input input--mono" inputMode="decimal" value={monto} onChange={(e) => setMonto(e.target.value)} /><small>Solo registro: el dinero se devuelve por el banco o por ONVO.</small></label>}
                  <label className="field">
                    <span className="ges-lbl">{needNote ? (act === "cancelado" ? "Motivo (lo ve el cliente)" : "Qué falló (lo ve el cliente)") : "Nota (opcional)"}</span>
                    <input ref={notaRef} className="input" value={nota} maxLength={500} onChange={(e) => { setNota(e.target.value); if (err) setErr(""); }}
                      aria-invalid={err ? true : undefined} aria-describedby={err ? "ges-nota-err" : undefined} aria-required={needNote || undefined} />
                    {err && <span id="ges-nota-err" className="ges-err"><Ico d={IC.alert} size={14} />{err}</span>}
                  </label>
                </div>
                <div className="wo-act__row ges-act-fin">
                  <label className="ges-check"><input type="checkbox" checked={avisar} onChange={(e) => setAvisar(e.target.checked)} />Avisar al cliente por correo</label>
                  <button type="button" className="btn btn--crimson btn--sm" onClick={correr}><Ico d={IC.check} />{ACCION[act] || "Aplicar"}</button>
                </div>
              </>
            )}
          </div>
        )}

        <Region label={`Productos del pedido ${o.numero}`}>
          <table className="table ges-table">
            <thead><tr><th scope="col">Producto</th><th scope="col" className="num">Cant.</th><th scope="col" className="num">Precio s/IVA</th><th scope="col" className="num">Total</th></tr></thead>
            <tbody>{o.lineas.map((l, i) => <tr key={i}><td className="ges-prod">{l.name}</td><td className="num mono">{l.quantity}</td><td className="num money">{colones(l.unit_price)}</td><td className="num money">{colones(l.total)}</td></tr>)}</tbody>
          </table>
        </Region>
        <div className="totals ges-totals">
          <div><span>Subtotal</span><span className="money">{colones(o.subtotal)}</span></div>
          <div><span>Envío</span><span className="money">{colones(o.envio)}</span></div>
          <div><span>IVA</span><span className="money">{colones(o.iva)}</span></div>
          <div className="total"><span>Total</span><span className="money">{colones(o.total)}</span></div>
        </div>

        <div className="ges-dos">
          <div><div className="meta ges-mb">Historial</div>
            <ul className="wo-hist">{[...o.history].reverse().map((h, i) => <li key={i}><time dateTime={h.at}>{cuando(h.at)}</time><span><b>{ETIQUETA[h.status]}</b>{h.by ? ` · ${h.by}` : ""}{h.note ? ` — ${h.note}` : ""}</span></li>)}</ul>
          </div>
          <div><div className="meta ges-mb">Correos al cliente y al equipo</div>
            {o.correos.length === 0 ? <span className="muted ges-sm">Sin correos.</span> : [...o.correos].reverse().map((m, i) => (
              <div key={i} className="wo-mail ges-mail" data-s={m.status}>
                <b>{m.status === "simulado" ? "Correo al cliente: simulado en la vista previa" : "enviado (ejemplo)"}</b><span className="muted">{m.subject} → {m.to}</span>
              </div>
            ))}
          </div>
        </div>

        <section className="ges-bita" aria-labelledby="ges-bita-t">
          <div className="ges-bita__h"><h3 id="ges-bita-t" className="meta">Bitácora de auditoría</h3><SelloPropuesta estado="existente" tamano="chico" /></div>
          {o.bitacora.length === 0 ? <p className="muted ges-sm">Todavía nadie tocó este pedido.</p> : (
            <ol>{[...o.bitacora].reverse().map((b, i) => <li key={i}><time className="mono" dateTime={b.at}>{cuando(b.at)}</time> · <b>{b.quien}</b> · {b.texto}</li>)}</ol>
          )}
        </section>

        <div className="ges-cliente">
          <button type="button" className="btn btn--crimson" disabled={!puedeVer} onClick={verCliente} aria-describedby={puedeVer ? undefined : "ges-cliente-pista"}>Ver cómo lo ve el cliente →</button>
          {!puedeVer && <span id="ges-cliente-pista" className="ges-sm muted">{o.numero === "ORD-000128" ? "El pedido de ejemplo recorre de «Pendiente de pago» a «Entregado»." : "En la vista previa, «Mi pedido» existe para ORD-000128 y para tus pedidos de prueba."}</span>}
        </div>
        <HiloCarmesi aqui="crimson" numero={o.numero} token={o.demo ? o.token : tokenEjemplo} />
      </div>
    </Dialogo>
  );
}
