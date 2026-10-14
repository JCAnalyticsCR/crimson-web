/* Maqueta A1 · Instalacion con fecha y precio estimado (con A5, paquete con instalacion incluida, como paso final).
   PROPUESTA no incluida aun: nada se guarda ni se envia; el estado vive en la sesion (crimson-demo:venta:a1).
   Pasos (?paso=): ficha -> instalacion -> listo -> crimson -> paquete. Pantalla de DECISION: las cifras cambian al
   instante, sin coreografia; el unico "momento" es la tarjeta del levantamiento al llegar a Crimson (380 ms, por un
   toque, y nada con reduced-motion). Tarifas de EJEMPLO (tarifa.ts); el margen sobre venta de 35 % es el de Crimson. */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import "../../../modules/store/shop.css";
import "../../../modules/store/vitrina.css";
import { AvailBadge, IC, Ico, PriceTag, money } from "../../../modules/store/storeKit";
import { BotonMeInteresa, colones, fecha, MarcoMaqueta, Pantalla, porcentaje, SelloPropuesta, usePaso, type Paso } from "./comun";
import { CLIENTE, DOC, FICHA_A1, KIT_CASA, PRODUCTOS, ROTULO_TARIFAS } from "./datos";
import { avisar, FotoProducto, SelloLinea, useGuardado } from "./piezas";
import {
  CABLES, CAMARAS_MAX, CAMARAS_MIN, CONFIG_INICIAL, IVA, LUGARES, NOTA_NO_SE, POR_CAMARA, PROVINCIAS, TRASLADO, VISITA,
  cableDe, clampCamaras, diaLargo, diasHabiles, estimar, franjaValida, lugarDe, primeraLibre, provinciaDe, turnoTexto,
  type ConfigA1, type Dia, type Estimado, type Franja, type LugarId, type Turno,
} from "./tarifa";
import "./venta.css";

const PASOS: readonly Paso[] = [
  { id: "ficha", titulo: "En la ficha", vista: "cliente" },
  { id: "instalacion", titulo: "Tu instalación", vista: "cliente" },
  { id: "listo", titulo: "Fecha apartada", vista: "cliente" },
  { id: "crimson", titulo: "Así le llega a Crimson", vista: "crimson" },
  { id: "paquete", titulo: "Variante A5: paquete con instalación incluida", vista: "cliente" },
];
const IDS = PASOS.map((p) => p.id);
const CLAVE = "crimson-demo:venta:a1";
const ICONO: Record<LugarId, string> = { casa: IC.home, negocio: IC.shop, condominio: IC.building, bodega: IC.factory };
const conIva = (n: number) => Math.round(n * (1 + IVA));
const factorTxt = (f: number) => f.toLocaleString("es-CR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const mayus = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

type EstadoA1 = ConfigA1 & { franja: Franja | null };
function validar(x: unknown): EstadoA1 | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  const f = o.franja as Partial<Franja> | null | undefined;
  return {
    camaras: clampCamaras(Number(o.camaras)),
    provincia: provinciaDe(String(o.provincia)).id,
    lugar: lugarDe(String(o.lugar)).id,
    cable: cableDe(String(o.cable)).id,
    franja: f && typeof f.iso === "string" && (f.turno === "m" || f.turno === "t") ? { iso: f.iso, turno: f.turno } : null,
  };
}

export default function A1Instalacion() {
  const [i, irA] = usePaso(IDS);
  const [st, set] = useGuardado<EstadoA1>(CLAVE, () => ({ ...CONFIG_INICIAL, franja: null }), validar);
  const dias = useMemo(() => diasHabiles(), []);
  const est = useMemo(() => estimar(st), [st]);
  const elegida = franjaValida(st.franja, dias);
  const paso = IDS[i];
  return (
    <MarcoMaqueta
      id="A1" etapa={1} pasos={PASOS}
      titulo="Instalación con fecha y precio estimado"
      bajada="El cliente sabe cuánto cuesta instalar y aparta fecha desde la ficha. A Crimson le llega el levantamiento armado, con el costeo hecho."
      hoy="En la ficha, «¿Lo instalamos nosotros?» abre una solicitud de asesoría que llega al CRM; el precio y la fecha se conversan después."
      conPropuesta="La ficha calcula un rango de precio por zona, lugar y cableado, deja apartar una fecha tentativa y crea el levantamiento para el técnico."
      aparte={<Aparte />}
    >
      <div className="ven">
        {paso === "ficha" && <Ficha cantidad={st.camaras} setCantidad={(n) => set((p) => ({ ...p, camaras: clampCamaras(n) }))} onCalcular={() => irA(1)} />}
        {paso === "instalacion" && <Instalacion st={st} set={set} est={est} dias={dias} elegida={elegida} onApartar={() => irA(2)} />}
        {paso === "listo" && <Listo est={est} elegida={elegida ?? primeraLibre(dias)} ejemplo={!elegida} onElegir={() => irA(1)} onCrimson={() => irA(3)} />}
        {paso === "crimson" && <Crimson est={est} elegida={elegida ?? primeraLibre(dias)} ejemplo={!elegida} />}
        {paso === "paquete" && <Paquete />}
      </div>
    </MarcoMaqueta>
  );
}

function Aparte() {
  return (
    <div className="ven ven-aparte">
      <p className="ven-aparte__t">Lo que gana Crimson</p>
      <p>Vende el servicio junto al equipo, con el precio claro desde el principio.</p>
      <Link className="ven-aparte__a" to="/rentabilidad?enfoque=A1">Ver cuánto suma en ¿Es rentable? <span aria-hidden="true">→</span></Link>
    </div>
  );
}

// ---------- 1 · la ficha real, con el bloque nuevo en lugar de "¿Lo instalamos nosotros?" ----------
function Ficha({ cantidad, setCantidad, onCalcular }: { cantidad: number; setCantidad: (n: number) => void; onCalcular: () => void }) {
  const p = PRODUCTOS[FICHA_A1.id];
  return (
    <Pantalla superficie="tienda" direccion={FICHA_A1.direccion}>
      <div className="shop ven-shop">
        <div className="ven-ficha">
          <FotoProducto id={p.id} label={`CAM 01 · ${p.codigo}`} className="vt-gallery__main ven-ficha__foto" tam={800} />
          <div className="vt-info ven-ficha__info">
            <div className="vt-info__meta sh-meta"><span>Marca {p.marca}</span><span>SKU {p.codigo}</span></div>
            <h3 className="ven-ficha__nombre">{p.nombre}</h3>
            <div className="vt-info__price">
              <PriceTag p={{ display_price: p.conIva, display_currency: "CRC" }} big />
              <AvailBadge a={{ state: "bajo_pedido", label: p.disponibilidad }} />
            </div>
            <p className="vt-breakdown">Precio sin IVA {money(p.sinIva)} + IVA 13 % {money(p.conIva - p.sinIva)}</p>
            <div className="vt-buybox">
              <div className="vt-buy">
                <span className="sh-qty vt-qty">
                  <button type="button" onClick={() => setCantidad(cantidad - 1)} aria-label="Quitar uno" disabled={cantidad <= CAMARAS_MIN}>−</button>
                  <input inputMode="numeric" value={cantidad} aria-label="Cantidad" onChange={(e) => setCantidad(Number(e.target.value.replace(/\D/g, "")) || CAMARAS_MIN)} />
                  <button type="button" onClick={() => setCantidad(cantidad + 1)} aria-label="Agregar uno" disabled={cantidad >= CAMARAS_MAX}>+</button>
                </span>
                <button type="button" className="sh-btn sh-btn--crimson vt-buy__add"
                  onClick={() => avisar("Maqueta A1", "En la tienda real este botón agrega la cámara al carrito. Aquí la ficha solo muestra dónde iría la propuesta.")}>
                  <Ico d={IC.bag} />Agregar al carrito
                </button>
              </div>
              <div className="ven-a1box">
                <div className="ven-a1box__top"><span className="ven-a1box__ico"><Ico d={IC.tool} /></span><SelloPropuesta id="A1" tamano="chico" /></div>
                <b className="ven-a1box__t">¿La instalamos nosotros?</b>
                <p className="ven-a1box__p">Elegí zona y fecha: te damos un precio estimado al instante.</p>
                <button type="button" className="sh-btn sh-btn--block ven-a1box__cta" onClick={onCalcular}>Calcular instalación y fecha<Ico d={IC.next} /></button>
                <p className="ven-a1box__hoy">Hoy este botón abre una solicitud de asesoría que llega al CRM (ya funciona).</p>
              </div>
              <button type="button" className="sh-btn sh-btn--ghost sh-btn--block"
                onClick={() => avisar("WhatsApp", "En la tienda real este botón abre WhatsApp con el mensaje listo para Crimson. Desde la vista previa no se envía nada.")}>
                <Ico d={IC.wa} />Consultar por WhatsApp
              </button>
            </div>
            <ul className="vt-assure">
              <li><Ico d={IC.shield} /><span>Garantía de fábrica gestionada por Crimson</span></li>
              <li><Ico d={IC.tool} /><span>Soporte técnico local en Costa Rica</span></li>
              <li><Ico d={IC.doc} /><span>Factura electrónica en cada compra</span></li>
              <li><Ico d={IC.truck} /><span>Envío a todo el país o retiro coordinado</span></li>
            </ul>
          </div>
        </div>
      </div>
    </Pantalla>
  );
}

// ---------- 2 · formulario con resultado en vivo ----------
function Instalacion({ st, set, est, dias, elegida, onApartar }: {
  st: EstadoA1; set: (f: (p: EstadoA1) => EstadoA1) => void; est: Estimado; dias: Dia[];
  elegida: { dia: Dia; turno: Turno } | null; onApartar: () => void;
}) {
  const cta = useRef<HTMLButtonElement>(null);
  const [ctaVisible, setCtaVisible] = useState(false);
  useEffect(() => {
    const el = cta.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([e]) => setCtaVisible(e.isIntersecting));
    io.observe(el);
    return () => io.disconnect();
  }, []);
  const rango = `${colones(est.bajo)} – ${colones(est.alto)}`;
  const detalle = `IVA incluido · ${est.camaras} cámara${est.camaras === 1 ? "" : "s"} · ${est.lugar.titulo} · ${est.fueraGam ? "fuera de la GAM" : "GAM"} · ${est.cable.corto}`;
  const fechaTxt = elegida ? `${mayus(diaLargo(elegida.dia.fecha))}, ${turnoTexto(elegida.turno)}` : "Sin elegir";
  const cam = (n: number) => set((p) => ({ ...p, camaras: clampCamaras(n) }));

  return (
    <div className="ven-a1cfg">
      <Pantalla superficie="tienda" direccion={FICHA_A1.direccion}>
        <div className="ven-cq">
          <div className="ven-a1cfg__grid">
            <form className="ven-form" onSubmit={(e) => e.preventDefault()} aria-label="Datos de tu instalación">
              <fieldset className="ven-q">
                <legend className="ven-q__l"><i aria-hidden="true">01</i>¿Cuántas cámaras vas a instalar?</legend>
                <div className="ven-stepper">
                  <button type="button" onClick={() => cam(st.camaras - 1)} disabled={st.camaras <= CAMARAS_MIN} aria-label="Una cámara menos"><Ico d={IC.minus} /></button>
                  <output aria-live="polite">{st.camaras}<span className="ven-sr"> cámaras</span></output>
                  <button type="button" onClick={() => cam(st.camaras + 1)} disabled={st.camaras >= CAMARAS_MAX} aria-label="Una cámara más"><Ico d={IC.plus} /></button>
                </div>
                <p className="ven-q__hint">De {CAMARAS_MIN} a {CAMARAS_MAX} cámaras. Para más, una asesoría.</p>
              </fieldset>

              <div className="ven-q">
                <label className="ven-q__l" htmlFor="ven-a1-prov"><i aria-hidden="true">02</i>¿En qué provincia?</label>
                <select id="ven-a1-prov" className="ven-select" value={st.provincia} onChange={(e) => set((p) => ({ ...p, provincia: e.target.value }))} aria-describedby="ven-a1-zona">
                  {PROVINCIAS.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                </select>
                <p id="ven-a1-zona" className="ven-zona" data-fuera={est.fueraGam}>
                  {est.fueraGam ? "Fuera de la GAM (+ traslado)" : "Zona: Gran Área Metropolitana"}
                </p>
                <p className="ven-q__hint">Zona de ejemplo por provincia; Crimson la define por cantón.</p>
              </div>

              <fieldset className="ven-q">
                <legend className="ven-q__l"><i aria-hidden="true">03</i>¿Qué lugar es?</legend>
                <div className="ven-lugares">
                  {LUGARES.map((l) => (
                    <label key={l.id} className="ven-opt">
                      <input type="radio" name="ven-a1-lugar" checked={st.lugar === l.id} onChange={() => set((p) => ({ ...p, lugar: l.id }))} />
                      <span><Ico d={ICONO[l.id]} size={22} /><b>{l.titulo}</b><small>{l.detalle}</small></span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="ven-q">
                <legend className="ven-q__l"><i aria-hidden="true">04</i>¿Hay que pasar cable?</legend>
                <div className="ven-chips">
                  {CABLES.map((c) => (
                    <label key={c.id} className="ven-chip">
                      <input type="radio" name="ven-a1-cable" checked={st.cable === c.id} onChange={() => set((p) => ({ ...p, cable: c.id }))} />
                      <span>{c.titulo}</span>
                    </label>
                  ))}
                </div>
                {st.cable === "nose" && <p className="ven-q__hint ven-q__hint--fuerte">{NOTA_NO_SE}</p>}
              </fieldset>

              <fieldset className="ven-q">
                <legend className="ven-q__l"><i aria-hidden="true">05</i>Fecha tentativa</legend>
                <p className="ven-q__hint ven-q__fila"><SelloPropuesta estado="ejemplo" tamano="chico" /><span>Disponibilidad de ejemplo. Elegí un día y una franja; el técnico la confirma.</span></p>
                <ul className="ven-dias">
                  {dias.map((d) => (
                    <li key={d.iso} className="ven-dia">
                      <span className="ven-dia__t" aria-hidden="true">{fecha(d.fecha, "dia")}</span>
                      <div className="ven-dia__f">
                        {(["m", "t"] as Turno[]).map((t) => d.sabado && t === "t"
                          ? <span key={t} className="ven-franja ven-franja--no">Sábado: solo mañana</span>
                          : (
                            <label key={t} className="ven-franja" data-lleno={d.lleno[t]}>
                              <input type="radio" name="ven-a1-franja" disabled={d.lleno[t]}
                                checked={st.franja?.iso === d.iso && st.franja.turno === t}
                                onChange={() => set((p) => ({ ...p, franja: { iso: d.iso, turno: t } }))} />
                              <span>{t === "m" ? "Mañana" : "Tarde"}{d.lleno[t] ? " · Lleno" : ""}<span className="ven-sr">, {diaLargo(d.fecha)}</span></span>
                            </label>
                          ))}
                      </div>
                    </li>
                  ))}
                </ul>
              </fieldset>
            </form>

            <section className="ven-res" aria-labelledby="ven-a1-res-t">
              <p className="ven-res__eyebrow" id="ven-a1-res-t"><i aria-hidden="true" />Instalación estimada</p>
              <p className="ven-res__rango" aria-live="polite">{rango}</p>
              <p className="ven-res__det">{detalle}</p>
              {st.cable === "nose" && <p className="ven-res__nota">{NOTA_NO_SE}</p>}
              <dl className="ven-res__dl">
                <div><dt>Fecha tentativa</dt><dd>{fechaTxt}</dd></div>
                <div><dt>Apartás como</dt><dd>{CLIENTE.completo} · {CLIENTE.correo} <small>(cliente de ejemplo)</small></dd></div>
              </dl>
              <details className="ven-res__como">
                <summary>Cómo lo calculamos</summary>
                <p>Base = ({colones(VISITA)} de visita + {est.camaras} × {colones(POR_CAMARA)} por cámara) × {factorTxt(est.lugar.factor)} por el lugar × {factorTxt(est.cable.factor)} por el cableado{est.traslado ? ` + ${colones(TRASLADO)} de traslado` : ""} = <b>{colones(est.base)} sin IVA</b>.</p>
                <p>Rango = de base × 0,90 a base × 1,20, con IVA de 13 % y redondeado a miles: <b>{rango}</b>.</p>
                <p>El precio final sale del levantamiento que hace el técnico en el sitio.</p>
              </details>
              <p className="ven-res__rotulo">{ROTULO_TARIFAS}</p>
              <button ref={cta} type="button" className="ven-btn ven-btn--crimson ven-btn--block" disabled={!elegida} aria-describedby={elegida ? undefined : "ven-a1-pista"} onClick={onApartar}>
                Apartar fecha tentativa
              </button>
              {!elegida && <p id="ven-a1-pista" className="ven-res__pista">Elegí una franja</p>}
            </section>
          </div>
        </div>
      </Pantalla>

      {/* movil: el rango y el CTA siempre a mano mientras se responde; se esconde cuando el CTA real esta en pantalla */}
      <div className="ven-barra" data-pp-barra="" data-oculta={ctaVisible ? "true" : undefined}>
        <span className="ven-barra__txt"><small>Instalación estimada</small><b>{rango}</b></span>
        <button type="button" className="ven-btn ven-btn--crimson ven-btn--sm" disabled={!elegida} onClick={onApartar}>{elegida ? "Apartar fecha" : "Elegí una franja"}</button>
      </div>
    </div>
  );
}

// ---------- 3 · confirmacion para el cliente ----------
function Listo({ est, elegida, ejemplo, onElegir, onCrimson }: {
  est: Estimado; elegida: { dia: Dia; turno: Turno } | null; ejemplo: boolean; onElegir: () => void; onCrimson: () => void;
}) {
  const p = PRODUCTOS[FICHA_A1.id];
  return (
    <Pantalla superficie="tienda" direccion={FICHA_A1.direccion}>
      <div className="ven-listo">
        <span className="ven-listo__ok" aria-hidden="true"><Ico d={IC.check} size={26} /></span>
        <p className="ven-meta">Instalación apartada · fecha tentativa</p>
        <h3 className="ven-listo__t">
          {elegida ? <>Listo, {CLIENTE.nombre}. Apartamos el {diaLargo(elegida.dia.fecha)}, {turnoTexto(elegida.turno)}.</> : <>Listo, {CLIENTE.nombre}. Tu instalación quedó en espera de fecha.</>}
        </h3>
        {ejemplo && (
          <p className="ven-listo__ej">
            No elegiste franja en «Tu instalación»: mostramos la primera libre como ejemplo.{" "}
            <button type="button" className="ven-link" onClick={onElegir}>Elegir otra</button>
          </p>
        )}
        <dl className="ven-dl ven-listo__dl">
          <div><dt>Instalación estimada</dt><dd><b>{colones(est.bajo)} – {colones(est.alto)}</b> · IVA incluido</dd></div>
          <div><dt>Equipo</dt><dd>{est.camaras} × {p.nombre}</dd></div>
          <div><dt>Lugar y zona</dt><dd>{est.lugar.titulo} · {est.provincia.nombre} ({est.fueraGam ? "fuera de la GAM" : "GAM"})</dd></div>
          <div><dt>Cableado</dt><dd>{mayus(est.cable.corto)}</dd></div>
        </dl>
        <h4 className="ven-listo__h">Qué sigue</h4>
        <ol className="ven-pasos-siguen" role="list">
          <li>Un técnico de Crimson revisa tu solicitud y te escribe para confirmar, en horario hábil.</li>
          <li>Confirmás la fecha y el precio final.</li>
          <li>Pagás el equipo y la instalación juntos o por separado: SINPE Móvil, transferencia o tarjeta.</li>
        </ol>
        <div className="ven-acciones">
          <button type="button" className="ven-btn ven-btn--crimson"
            onClick={() => avisar("Maqueta A1", "En la tienda real, el equipo va al carrito y la instalación queda apartada como fecha tentativa. Esta propuesta no está incluida aún.")}>
            Agregar equipo e instalación al carrito
          </button>
          <button type="button" className="ven-btn ven-btn--ghost" onClick={onCrimson}>Ver cómo le llega a Crimson <span aria-hidden="true">→</span></button>
        </div>
      </div>
    </Pantalla>
  );
}

// ---------- 4 · el levantamiento que le llega a Crimson, con el costeo hecho ----------
function Crimson({ est, elegida, ejemplo }: { est: Estimado; elegida: { dia: Dia; turno: Turno } | null; ejemplo: boolean }) {
  const p = PRODUCTOS[FICHA_A1.id];
  return (
    <Pantalla superficie="admin" seccion="Levantamientos" estado="existente">
      <div className="ven-adm">
        <div className="page-head"><div><div className="meta">07 · Campo</div><h3 className="h1">Levantamientos</h3></div></div>
        <div className="ven-adm__grid">
          <article className="card ven-lev ven-momento" aria-labelledby="ven-lev-t">
            <div className="card__head ven-lev__head">
              <div><span className="meta">Levantamiento técnico · CCTV</span><b id="ven-lev-t" className="ven-lev__n">{DOC.levantamiento} · Pendiente de visita</b></div>
              <span className="badge badge--info ven-nuevo">Nuevo · desde la tienda</span>
            </div>
            <div className="card__body ven-lev__body">
              <dl className="ven-dl ven-dl--2">
                <div><dt>Origen</dt><dd>Tienda en línea · ficha del producto</dd></div>
                <div><dt>Cliente</dt><dd>{CLIENTE.completo} (ejemplo)</dd></div>
                <div><dt>Zona y lugar</dt><dd>{est.provincia.nombre} · {est.fueraGam ? "fuera de la GAM" : "GAM"} · {est.lugar.titulo}</dd></div>
                <div><dt>Fecha tentativa</dt><dd>{elegida ? `${mayus(diaLargo(elegida.dia.fecha))}, ${turnoTexto(elegida.turno)}${ejemplo ? " (primera libre, de ejemplo)" : ""}` : "Por elegir"}</dd></div>
                <div><dt>Equipo</dt><dd>{est.camaras} × {p.nombre}</dd></div>
                <div><dt>Cableado</dt><dd>{mayus(est.cable.corto)}</dd></div>
                <div><dt>Estado</dt><dd>Por confirmar</dd></div>
                <div><dt>Técnico</dt><dd>Sin asignar</dd></div>
              </dl>
              <h4 className="ven-adm__h">Costeo</h4>
              <div className="ven-cq">
                <table className="table ven-tt ven-costeo" role="table" aria-label="Costeo de la instalación">
                  <thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col">Concepto</th><th role="columnheader" scope="col" className="num">Costo</th><th role="columnheader" scope="col" className="num">Precio sin IVA</th></tr></thead>
                  <tbody role="rowgroup">
                    {est.lineas.map((l) => (
                      <tr role="row" key={l.concepto}>
                        <th role="rowheader" scope="row">{l.concepto}</th>
                        <td role="cell" className="num" data-label="Costo">{colones(l.costo)}</td>
                        <td role="cell" className="num" data-label="Precio sin IVA">{colones(l.precio)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot role="rowgroup">
                    <tr role="row" className="ven-tt__total">
                      <th role="rowheader" scope="row">Total</th>
                      <td role="cell" className="num" data-label="Costo">{colones(est.costo)}</td>
                      <td role="cell" className="num" data-label="Precio sin IVA">{colones(est.precio)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <p className="ven-costeo__pie">Margen sobre venta {porcentaje(est.margen)} · Precio = costo ÷ (1 − 0,35)</p>
              <p className="ven-recargo">
                <b>Margen sobre venta, no recargo:</b> con un recargo del 35 % sobre el costo, el precio sería {colones(est.conRecargo.precio)} y el margen real quedaría en {porcentaje(est.conRecargo.margen)}.
              </p>
            </div>
          </article>
          <aside className="card ven-opo" aria-labelledby="ven-opo-t">
            <div className="card__head"><span className="meta">CRM · Oportunidades</span></div>
            <div className="card__body ven-opo__body">
              <b id="ven-opo-t" className="ven-opo__t">Oportunidad {DOC.oportunidad} en el CRM</b>
              <dl className="ven-dl">
                <div><dt>Tipo</dt><dd>Proyecto con instalación</dd></div>
                <div><dt>Solución</dt><dd>CCTV</dd></div>
                <div><dt>Origen</dt><dd>Tienda en línea</dd></div>
                <div><dt>Etapa</dt><dd>En levantamiento</dd></div>
                <div><dt>Monto estimado</dt><dd>{colones(est.precio)} sin IVA</dd></div>
              </dl>
            </div>
          </aside>
        </div>
        <ul className="ven-sellos">
          <SelloLinea estado="existente">Levantamiento con costeo y margen sobre venta</SelloLinea>
          <SelloLinea estado="propuesta" id="A1">Creado solo desde la tienda, con fecha y rango</SelloLinea>
        </ul>
        <p className="ven-rotulo">{ROTULO_TARIFAS}</p>
      </div>
    </Pantalla>
  );
}

// ---------- 5 · A5: el kit real de la portada con la instalacion incluida a precio cerrado ----------
function Paquete() {
  const inst = estimar({ camaras: 4, provincia: "heredia", lugar: "casa", cable: "si" }); // 174 000 sin IVA
  const instIva = conIva(inst.precio);
  const cerrado = KIT_CASA.total + instIva;
  return (
    <div className="ven-paqw">
      <p className="ven-paqw__sello"><SelloPropuesta id="A5" /><span>Variante de A1: usa sus mismas tarifas de ejemplo.</span></p>
      <Pantalla superficie="tienda" direccion="tienda.crimsoncr.com">
        <div className="shop ven-shop">
          <article className="ven-paq ven-cq" aria-labelledby="ven-paq-t">
            <FotoProducto id={53} label="KIT · 4 CAM" className="ven-paq__foto" />
            <div className="ven-paq__info">
              <p className="ven-meta">Kit listo · con instalación</p>
              <h3 id="ven-paq-t" className="ven-paq__t">{KIT_CASA.titulo}</h3>
              <p className="ven-paq__txt">{KIT_CASA.texto}</p>
              <div className="ven-vs">
                <div className="ven-vs__hoy"><b>Hoy</b><p>Equipo {colones(KIT_CASA.total)} · «la instalación se cotiza aparte»</p></div>
                <div className="ven-vs__con"><b>Con A5</b><p>Equipo e instalación juntos, a precio cerrado</p></div>
              </div>
              <dl className="ven-suma">
                <div><dt>Equipo ({KIT_CASA.articulos} artículos, IVA incluido)</dt><dd>{colones(KIT_CASA.total)}</dd></div>
                <div><dt>Instalación de 4 cámaras · Casa · con cableado · GAM</dt><dd>{colones(inst.precio)} + IVA = {colones(instIva)}</dd></div>
              </dl>
              <p className="ven-cerrado">Precio cerrado: <b>{colones(cerrado)}</b> <small>IVA incluido</small></p>
              <details className="ven-det">
                <summary>Qué trae el kit</summary>
                <ul>
                  {KIT_CASA.lineas.map((l) => <li key={l.id}><b>{l.cant} ×</b> {PRODUCTOS[l.id].nombre}</li>)}
                  {KIT_CASA.faltantes.map((f) => <li key={f.nombre}><b>{f.cant} ×</b> {f.nombre} · lo cotizamos</li>)}
                  <li><b>1 ×</b> Instalación de 4 cámaras (visita, configuración y cableado)</li>
                </ul>
              </details>
              <p className="ven-nota">Hoy la portada dice «la instalación se cotiza aparte»; con A5 el kit sale con todo incluido.</p>
              <p className="ven-rotulo">Precio de ejemplo con las tarifas de ejemplo de A1.</p>
              <div className="ven-acciones">
                <button type="button" className="ven-btn ven-btn--crimson"
                  onClick={() => avisar("Maqueta A5", "En la tienda real, el paquete entra al carrito con el equipo y la instalación a precio cerrado. Esta propuesta no está incluida aún.")}>
                  Agregar paquete al carrito
                </button>
                <BotonMeInteresa id="A5" />
              </div>
            </div>
          </article>
        </div>
      </Pantalla>
    </div>
  );
}
