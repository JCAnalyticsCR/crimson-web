/* Detras de escena · Inventario web (ya construido, datos de ejemplo). Imita inventory/WebInventory.tsx: lo publicado
   contra el inventario real, reservas por pedido y ajustes. Lo reservado se CALCULA de los pedidos (los mismos de
   Pedidos): como la reserva real (store_inventory.hold_for_order), cada pedido toma lo libre y lo que falta queda
   "requiere compra". "La ultima unidad" simula dos compras al mismo tiempo (momento de 380 ms, solo por un toque). */
import { useMemo, useRef, useState, type RefObject } from "react";
import { MarcoMaqueta, Pantalla, SelloPropuesta, colones } from "./comun";
import { AJUSTES_INV, INVENTARIO, PRODUCTOS, RESERVA_HORAS, RESERVA_MIN_TARJETA, RESUMEN_INV, pedidosEjemplo, type Estado, type PedidoGes } from "./datos";
import { leerTodos } from "./estadoPedidos";
import { IC, Ico, Region, SubnavDE, calma, relativo, useMedia } from "./ui";
import "./gestion.css";

const RESERVA: Estado[] = ["nuevo", "en_revision", "pagado", "preparando", "listo"];
const DESPACHO: Estado[] = ["enviado", "entregado"];
const PAGADO: Estado[] = ["pagado", "preparando"];
const MIN = 60_000;

type Reserva = { pedido: string; quien: string; estado: string; id: number; cantidad: number; vence: string; sim?: boolean };
type Fila = { id: number; bodega: number; reservado: number; libre: number; compra: { n: number; pedidos: string[] }; proveedor: string | null };

function calcular(pedidos: PedidoGes[], sim: boolean): { filas: Fila[]; reservas: Reserva[] } {
  const inicial = new Map(pedidosEjemplo().map((o) => [o.numero, o.status]));
  const filas: Fila[] = INVENTARIO.map((r) => ({ id: r.id, bodega: r.bodega, reservado: 0, libre: 0, compra: { n: 0, pedidos: [] }, proveedor: r.proveedor }));
  const fila = new Map(filas.map((f) => [f.id, f]));
  const idDe = (nombre: string) => Object.values(PRODUCTOS).find((p) => p.nombre === nombre)?.id;
  // lo que salio de bodega desde el estado inicial (despachado en esta visita)
  for (const o of pedidos) {
    const antes = o.demo ? "nuevo" : inicial.get(o.numero);
    if (!antes || !RESERVA.includes(antes) || !DESPACHO.includes(o.status)) continue;
    for (const l of o.lineas) { const f = fila.get(l.product_id ?? idDe(l.name) ?? -1); if (f) f.bodega = Math.max(0, f.bodega - l.quantity); }
  }
  filas.forEach((f) => { f.libre = f.bodega; });
  const reservas: Reserva[] = [];
  const activos = pedidos.filter((o) => RESERVA.includes(o.status)).sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
  for (const o of activos) {
    for (const l of o.lineas) {
      if (l.envio) continue;
      const f = fila.get(l.product_id ?? idDe(l.name) ?? -1);
      if (!f) continue;
      const take = Math.min(l.quantity, f.libre);
      if (take > 0) {
        f.reservado += take; f.libre -= take;
        const vence = PAGADO.includes(o.status) ? "Pagado: hasta despachar" : o.status === "listo" ? "Hasta el retiro" : relativo(new Date(new Date(o.created_at).getTime() + (o.pago.kind === "tarjeta" ? RESERVA_MIN_TARJETA : RESERVA_HORAS * 60) * MIN).toISOString());
        reservas.push({ pedido: o.numero, quien: o.cliente.company || o.cliente.name, estado: o.status, id: f.id, cantidad: take, vence });
      }
      const resto = l.quantity - take;
      if (resto > 0) { f.compra.n += resto; if (!f.compra.pedidos.includes(o.numero)) f.compra.pedidos.push(o.numero); }
    }
  }
  if (sim) {
    const f = fila.get(177);
    if (f && f.libre > 0) {
      f.reservado += 1; f.libre -= 1;
      reservas.push({ pedido: "Ana (simulación)", quien: "Ana", estado: "nuevo", id: 177, cantidad: 1, vence: relativo(new Date(Date.now() + RESERVA_HORAS * 60 * MIN).toISOString()), sim: true });
    }
  }
  return { filas, reservas };
}

const ESTADO_TXT: Record<string, string> = { nuevo: "Pendiente de pago", en_revision: "Comprobante en revisión", pagado: "Pagado", preparando: "Preparando", listo: "Listo para retiro" };


const Compra = ({ f }: { f: Fila }) => (f.compra.n > 0 ? <><span className="ges-compra">Requiere compra: {f.compra.n}</span><span className="ges-pedidos-rc">{f.compra.pedidos.join(" · ")}</span></> : null);
/* "Eurocomp · 120 u. · hace 2 dias (cargado a mano)" en dos renglones */
const Prov = ({ t }: { t: string | null }) => { if (!t) return <>—</>; const [a, b, c] = t.split(" · "); return <>{a} · {b}<span className="ges-sub">{c}</span></>; };
function BotonSim({ refBtn, sim, libre, onSimular }: { refBtn: RefObject<HTMLButtonElement | null>; sim: boolean; libre: number; onSimular: () => void }) {
  return (
    <button ref={refBtn} type="button" id="ges-ultima-unidad" className="btn btn--crimson btn--sm ges-simbtn" disabled={sim || libre !== 1}
      aria-describedby={libre !== 1 && !sim ? "ges-sim-pista" : undefined} onClick={onSimular}>
      Simular dos compras al mismo tiempo
    </button>
  );
}

export default function Inventario() {
  const pedidos = useMemo(() => leerTodos(), []);
  const [filtro, setFiltro] = useState("");
  const [q, setQ] = useState("");
  const [sim, setSim] = useState(false);
  const movil = useMedia("(max-width: 767px)");
  const simT = useRef<HTMLHeadingElement>(null);
  const simS = useRef<HTMLElement>(null);
  const btnSim = useRef<HTMLButtonElement>(null);

  const sinSim = useMemo(() => calcular(pedidos, false), [pedidos]);
  const { filas, reservas } = useMemo(() => calcular(pedidos, sim), [pedidos, sim]);
  const libreGrabador = sinSim.filas.find((f) => f.id === 177)?.libre ?? 0;
  // si el grabador quedo sin unidad libre por un pedido de prueba de la visita, la pista lo dice (restablecer
  // los ejemplos no lo devuelve: hay que cancelar ese pedido en Pedidos)
  const demoGrabador = libreGrabador === 0
    ? sinSim.reservas.find((h) => h.id === 177 && pedidos.some((o) => o.demo && o.numero === h.pedido))?.pedido
    : undefined;
  const totalReservado = filas.reduce((a, f) => a + f.reservado, 0);

  const visibles = filas.filter((f) => {
    const p = PRODUCTOS[f.id];
    if (filtro === "disponible" && !p.disponible) return false;
    if (filtro === "bajo_pedido" && p.disponible) return false;
    if (filtro === "reservado" && f.reservado === 0) return false;
    if (["consultar", "agotado", "alertas"].includes(filtro)) return false;
    const t = q.trim().toLowerCase();
    return !t || p.nombre.toLowerCase().includes(t) || p.code.toLowerCase().includes(t);
  });

  const simular = () => {
    setSim(true);
    requestAnimationFrame(() => {
      simS.current?.scrollIntoView({ block: "start", behavior: calma() ? "auto" : "smooth" });
      simT.current?.focus({ preventScroll: true });
    });
  };
  const deshacer = () => { setSim(false); requestAnimationFrame(() => btnSim.current?.focus()); };

  const pill = (k: string, label: string, v: number, hot = false) => (
    <button type="button" key={k} className={`ges-pill${filtro === k || hot ? " hot" : ""}`} aria-pressed={filtro === k} onClick={() => setFiltro(filtro === k ? "" : k)}>
      <b>{v}</b><span>{label}</span>
    </button>
  );
  const estadoTienda = (f: Fila) => {
    const p = PRODUCTOS[f.id];
    if (!p.disponible) return { tone: "warn", label: "Bajo pedido", msg: "3 a 5 días hábiles" };
    return { tone: "ok", label: "Disponible", msg: f.libre <= 0 ? "Sin unidades libres" : f.libre <= 2 ? "Últimas unidades" : "En existencia" };
  };
  const boton = <BotonSim refBtn={btnSim} sim={sim} libre={libreGrabador} onSimular={simular} />;

  return (
    <MarcoMaqueta id="DE" estado="existente" titulo="Inventario web"
      bajada="Lo publicado contra el inventario real, con reservas por pedido. Nunca se vende dos veces la última unidad. Ya existe; aquí con datos de ejemplo.">
      <SubnavDE actual="inventario" />
      <Pantalla superficie="admin" seccion="Inventarios">
        <div className="ges ges-adm ges-inv">
          <div className="page-head">
            <div><div className="meta">06 · Inventarios</div><h2 className="h1">Inventarios</h2></div>
          </div>
          <div className="tabs ges-tabs-ro" aria-label="Pestañas de Inventarios (en la vista previa solo «Inventario web»)" role="group">
            {["Existencias", "Reservas", "Seriales"].map((t) => <span key={t} className="ges-tab-off">{t}</span>)}
            <span className="is-active ges-tab-on" aria-current="page">Inventario web</span>
          </div>
          <p className="muted ges-texto">
            Disponible web = existencias en las bodegas que alimentan la tienda menos lo reservado (proyectos, bodega y pedidos web). Un pedido web reserva solo; si no se paga a tiempo, la reserva vence y se libera. Al despachar o facturar sale del inventario una sola vez.
          </p>
          <div className="ges-pending" role="group" aria-label="Filtrar el inventario">
            {pill("", "Publicados", RESUMEN_INV.publicados)}
            {pill("disponible", "Disponibles", RESUMEN_INV.disponible)}
            {pill("bajo_pedido", "Bajo pedido", RESUMEN_INV.bajo_pedido)}
            {pill("consultar", "Proveedor sin dato reciente", RESUMEN_INV.consultar)}
            {pill("agotado", "Agotados", RESUMEN_INV.agotado)}
            {pill("reservado", "Reservado por pedidos", totalReservado)}
            {pill("alertas", "Alertas", RESUMEN_INV.alertas)}
          </div>
          <div className="card ges-aviso-modo" role="note">
            <span>Modo aviso: la tienda reserva lo que hay y lo que falta queda <b>«requiere compra»</b>, sin rechazar pedidos. Cuando el inventario esté cargado, encendé <b>«Bloquear ventas sin existencia»</b> abajo.</span>
          </div>

          <div className="card ges-card">
            <div className="card__head ges-head">
              <span className="ges-card-t">{filtro === "alertas" ? "Publicados sin stock ni proveedor" : "Inventario de la tienda"}</span>
              <input className="input ges-buscar" type="search" placeholder="Buscar código o nombre" aria-label="Buscar en el inventario" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            {visibles.length === 0 ? (
              <div className="empty"><span>{filtro === "alertas" ? "Todo lo publicado tiene existencia o proveedor que lo respalde." : "No hay productos con ese filtro."}</span></div>
            ) : movil ? (
              <ul className="ges-tarjetas" aria-label="Inventario de la tienda">
                {visibles.map((f, i) => {
                  const p = PRODUCTOS[f.id], st = estadoTienda(f);
                  return (
                    <li key={f.id} className="ges-tarjeta">
                      <b className="ges-prod">{p.nombre}</b><span className="meta mono">{p.code}</span>
                      <dl>
                        <dt>Estado en la tienda</dt><dd><span className={`badge badge--${st.tone}`}>{st.label}</span></dd>
                        <dt>En bodegas web</dt><dd className="mono">{f.bodega}</dd>
                        <dt>Reservado</dt><dd className="mono">{f.reservado || "—"}</dd>
                        <dt>Libre</dt><dd className="mono ges-libre-m"><b>{f.libre}</b> <Compra f={f} /></dd>
                        <dt id={i === 0 ? "ges-inv-proveedor" : undefined}>Proveedor{i === 0 && <> <SelloPropuesta id="D1" tamano="chico" /></>}</dt><dd><Prov t={f.proveedor} /></dd>
                        <dt id={i === 0 ? "ges-inv-precio" : undefined}>Precio web{i === 0 && <> <SelloPropuesta id="C3" tamano="chico" /></>}</dt><dd className="money">{colones(p.neto)}</dd>
                      </dl>
                      {f.id === 177 && boton}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <Region label="Inventario de la tienda">
                <table className="table ges-table ges-invtable">
                  <thead><tr>
                    <th scope="col">Producto</th><th scope="col">Estado en la tienda</th><th scope="col" className="num">En bodegas web</th>
                    <th scope="col" className="num" title="Reservado por pedidos web">Reservado</th><th scope="col" className="num">Libre</th>
                    <th scope="col" id="ges-inv-proveedor"><span className="ges-th">Proveedor <SelloPropuesta id="D1" tamano="chico" /></span></th>
                    <th scope="col" className="num" id="ges-inv-precio"><span className="ges-th ges-th--r">Precio web <SelloPropuesta id="C3" tamano="chico" /></span></th>
                  </tr></thead>
                  <tbody>{visibles.map((f) => {
                    const p = PRODUCTOS[f.id], st = estadoTienda(f);
                    return (
                      <tr key={f.id} data-sim={sim && f.id === 177 ? "1" : undefined}>
                        <td className="ges-prodcell"><b className="ges-prod">{p.nombre}</b><div className="meta mono">{p.code}</div>{f.id === 177 && boton}</td>
                        <td><span className={`badge badge--${st.tone}`}>{st.label}</span><span className="ges-sub">{st.msg}</span></td>
                        <td className="num mono">{f.bodega}</td>
                        <td className="num mono ges-res" data-on={f.reservado > 0 ? "1" : undefined}>{f.reservado || "—"}</td>
                        <td className="num mono ges-libre"><b>{f.libre}</b><Compra f={f} /></td>
                        <td className="muted ges-prov"><Prov t={f.proveedor} /></td>
                        <td className="num money">{colones(p.neto)}</td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              </Region>
            )}
            {libreGrabador !== 1 && !sim && <p id="ges-sim-pista" className="ges-sm muted ges-pad">{demoGrabador
              ? `Tu pedido de prueba ${demoGrabador} apartó la última unidad libre del grabador: es la reserva funcionando. Para ver la simulación, cancelá ese pedido en Pedidos.`
              : "La simulación necesita que quede 1 unidad libre del grabador: restablecé los pedidos de ejemplo en Pedidos."}</p>}
            <p className="ges-sm muted ges-pad">Se muestran {INVENTARIO.length} de {RESUMEN_INV.publicados} productos publicados (datos de ejemplo). Precio web sin IVA.</p>
          </div>

          <div className="card ges-card">
            <div className="card__head ges-head"><span className="ges-card-t">Reservado por pedidos web · {reservas.length}</span></div>
            {reservas.length === 0 ? <div className="empty"><b className="h3">Sin reservas de pedidos</b><span>Cada pedido de la tienda reserva su mercadería hasta que se paga y se despacha.</span></div> : movil ? (
              <ul className="ges-tarjetas" aria-label="Reservas de pedidos web">
                {reservas.map((h, i) => (
                  <li key={i} className="ges-tarjeta" data-sim={h.sim ? "1" : undefined}>
                    <b className="mono">{h.pedido}</b>
                    <dl><dt>Producto</dt><dd>{PRODUCTOS[h.id].nombre}</dd><dt>Cantidad</dt><dd className="mono">{h.cantidad}</dd><dt>Vence</dt><dd>{h.vence}</dd></dl>
                  </li>
                ))}
              </ul>
            ) : (
              <Region label="Reservas de pedidos web">
                <table className="table ges-table">
                  <thead><tr><th scope="col">Pedido</th><th scope="col">Producto</th><th scope="col">Bodega</th><th scope="col" className="num">Cantidad</th><th scope="col">Vence</th></tr></thead>
                  <tbody>{reservas.map((h, i) => (
                    <tr key={i} data-sim={h.sim ? "1" : undefined}>
                      <td className="mono">{h.pedido}<div className="meta ges-meta-n">{ESTADO_TXT[h.estado] || h.estado}</div></td>
                      <td className="ges-prod">{PRODUCTOS[h.id].nombre}</td><td className="muted ges-holdbod">Bodega Alajuela</td>
                      <td className="num mono">{h.cantidad}</td><td className="muted ges-nowrap">{h.vence}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </Region>
            )}
          </div>

          <details className="card ges-ajustes">
            <summary><span className="ges-card-t">Ajustes de la tienda · inventario</span><span className="ges-sm muted">solo lectura en la vista previa</span></summary>
            <ul>
              {AJUSTES_INV.map((a) => (
                <li key={a.label}>
                  {a.tipo === "check" ? <span className="ges-ro-check" data-on={a.valor === "sí" ? "1" : "0"} aria-hidden="true">{a.valor === "sí" ? "✓" : ""}</span> : null}
                  <span>{a.label}</span>
                  {a.tipo === "campo" ? <b className="mono">{a.valor}</b> : <b className="ges-sr">{a.valor === "sí" ? "encendido" : "apagado"}</b>}
                </li>
              ))}
            </ul>
          </details>
        </div>
      </Pantalla>

      {sim && (
        <section ref={simS} className="ges ges-ultima" aria-labelledby="ges-ultima-t">
          <div className="ges-ultima__cab">
            <span className="ges-ultima__rot">Simulación · la última unidad</span>
            <h3 id="ges-ultima-t" ref={simT} tabIndex={-1}>Ana y Jorge tocaron «Agregar al carrito» en el mismo instante</h3>
            <p>Quedaba 1 Grabador IP NVR Hikvision 4CH PoE libre. La reserva se hace en una sola operación: la gana quien llega primero por milésimas y la otra persona lo sabe en ese momento.</p>
          </div>
          <div className="ges-ultima__tel">
            <Pantalla superficie="celular" rotulo="Celular de Ana" estado="existente">
              <div className="ges-tel" data-res="ok">
                <span className="ges-tel__top">tienda.crimsoncr.com</span>
                <span className="ges-tel__est"><Ico d={IC.check} size={14} />Reservado</span>
                <p><b>Reservado: 1 × Grabador IP NVR Hikvision 4CH PoE.</b> Te lo apartamos mientras pagás.</p>
                <span className="ges-tel__btn">Ir a pagar</span>
              </div>
            </Pantalla>
            <Pantalla superficie="celular" rotulo="Celular de Jorge" estado="existente">
              <div className="ges-tel" data-res="no">
                <span className="ges-tel__top">tienda.crimsoncr.com</span>
                <span className="ges-tel__est"><Ico d={IC.alert} size={14} />Ya no queda</span>
                <p><b>Ya no queda:</b> otra persona lo apartó hace un instante. ¿Te avisamos cuando llegue?</p>
                <span className="ges-tel__btn ges-tel__btn--g">Avisarme cuando llegue</span>
              </div>
            </Pantalla>
          </div>
          <p className="ges-leyenda"><SelloPropuesta estado="existente" tamano="chico" /> <b>Reservas atómicas (ya construido):</b> la unidad se aparta en una sola operación; nunca se vende dos veces la última. Así se ve con «Bloquear ventas sin existencia» encendido; en modo aviso, el pedido de Jorge entra como «requiere compra» y Crimson lo trae del proveedor.</p>
          <p className="ges-sm muted">En la tabla: el grabador queda con Libre 0 y Reservado 2, y la reserva de Ana aparece en «Reservado por pedidos web».</p>
          <button type="button" className="btn btn--ghost" onClick={deshacer}>Deshacer simulación</button>
        </section>
      )}
    </MarcoMaqueta>
  );
}
