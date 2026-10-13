/* Maqueta A3 · Cotizacion formal para empresas. PROPUESTA no incluida aun.
   Pasos (?paso=): kit -> empresa -> pdf -> aprobacion -> crimson. Kit real "Negocio u oficina · 8 cámaras" (₡800 513),
   PDF de VISTA PREVIA dibujado en la pagina (no se genera ningun archivo), aprobacion en linea con versiones (v1 -> v2
   cuando Laura pide la UPS) y la fila que le llega a Crimson. Estado en la sesion (crimson-demo:venta:a3).
   Montos de documento con 2 decimales e IVA por linea (igual que quote() de la tienda):
   v1 ₡1 195 786,98 · v2 ₡1 305 713,38. Unico "momento": el sello APROBADA (380 ms, por un toque). */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import "../../../modules/store/shop.css";
import "../../../modules/store/vitrina.css";
import { IC, Ico, PriceTag, money } from "../../../modules/store/storeKit";
import { colones, colones2, fecha, hora, MarcoMaqueta, Pantalla, SelloPropuesta, usePaso, type Paso } from "./comun";
import { CAMBIO_A3, DOC, EMPRESA, KIT_NEGOCIO, LEMA, PRODUCTOS, ROTULO_TARIFAS, UPS_V2 } from "./datos";
import { avisar, fechaNum, FotoProducto, num2, SelloLinea, useGuardado } from "./piezas";
import { estimar, IVA } from "./tarifa";
import "./venta.css";

const PASOS: readonly Paso[] = [
  { id: "kit", titulo: "El kit en «Arma tu sistema»", vista: "cliente" },
  { id: "empresa", titulo: "Datos de la empresa", vista: "cliente" },
  { id: "pdf", titulo: "La cotización en PDF", vista: "cliente" },
  { id: "aprobacion", titulo: "Aprobación en línea", vista: "cliente" },
  { id: "crimson", titulo: "Así le llega a Crimson", vista: "crimson" },
];
const IDS = PASOS.map((p) => p.id);
const CLAVE = "crimson-demo:venta:a3";
const MIN = 60_000;
const r2 = (n: number) => Math.round(n * 100) / 100;
const INSTALACION = estimar({ camaras: 8, provincia: "san-jose", lugar: "negocio", cable: "si" }); // 349 800 sin IVA
const VIGENCIAS = [8, 15, 30];

type Empresa = { razon: string; cedula: string; atencion: string; correo: string; instalar: boolean; vigencia: number };
type Version = 1 | 2;
type EstadoA3 = {
  empresa: Empresa;
  v1: number | null;                                        // cuando se genero la v1 (ms)
  cambio: { texto: string; at: number; v2: number } | null; // pedido de cambio de la empresa y v2 de Crimson
  aprobada: { version: Version; nombre: string; at: number } | null;
};
const inicial = (): EstadoA3 => ({ empresa: { ...EMPRESA, instalar: true, vigencia: 15 }, v1: null, cambio: null, aprobada: null });
const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const txt = (x: unknown, d: string) => (typeof x === "string" ? x.slice(0, 160) : d);
function validar(x: unknown): EstadoA3 | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  const e = (o.empresa && typeof o.empresa === "object" ? o.empresa : {}) as Record<string, unknown>;
  const c = o.cambio as Record<string, unknown> | null;
  const a = o.aprobada as Record<string, unknown> | null;
  return {
    empresa: {
      razon: txt(e.razon, EMPRESA.razon), cedula: txt(e.cedula, EMPRESA.cedula), atencion: txt(e.atencion, EMPRESA.atencion), correo: txt(e.correo, EMPRESA.correo),
      instalar: e.instalar !== false, vigencia: VIGENCIAS.includes(Number(e.vigencia)) ? Number(e.vigencia) : 15,
    },
    v1: num(o.v1),
    cambio: c && typeof c === "object" && num(c.at) && num(c.v2) ? { texto: txt(c.texto, CAMBIO_A3), at: num(c.at)!, v2: num(c.v2)! } : null,
    aprobada: a && typeof a === "object" && (a.version === 1 || a.version === 2) && num(a.at) ? { version: a.version, nombre: txt(a.nombre, EMPRESA.atencion), at: num(a.at)! } : null,
  };
}

// ---------- el documento: lineas y totales (IVA por linea, redondeo a centimos) ----------
type LineaDoc = { codigo: string; descripcion: string; cant: number; unit: number; nueva?: boolean };
function lineasDe(v: Version, instalar: boolean): LineaDoc[] {
  const out: LineaDoc[] = KIT_NEGOCIO.lineas.map((l) => ({ codigo: PRODUCTOS[l.id].codigo, descripcion: PRODUCTOS[l.id].nombre, cant: l.cant, unit: PRODUCTOS[l.id].sinIva }));
  if (v === 2) { const u = PRODUCTOS[UPS_V2]; out.push({ codigo: u.codigo, descripcion: u.nombre, cant: 1, unit: u.sinIva, nueva: true }); }
  if (instalar) out.push({ codigo: "INST", descripcion: "Instalación de 8 cámaras (estimada) · Negocio u oficina · con cableado", cant: 1, unit: INSTALACION.precio });
  return out;
}
function totales(ls: LineaDoc[]) {
  let sub = 0, iva = 0;
  for (const l of ls) { const t = r2(l.cant * l.unit); sub += t; iva += r2(t * IVA); }
  sub = r2(sub); iva = r2(iva);
  return { sub, iva, total: r2(sub + iva) };
}
const versionVigente = (st: EstadoA3): Version => (st.cambio ? 2 : 1);
const nombreCorto = (s: string) => s.trim().split(/\s+/)[0] || "Laura";
const cuando = (ms: number) => {
  const d = new Date(ms);
  return d.toDateString() === new Date().toDateString() ? `hoy ${hora(d)}` : `${fecha(d, "corta")} ${hora(d)}`;
};
function historial(st: EstadoA3, desdeTienda = true): { at: number; texto: string }[] {
  const v1 = st.v1 ?? (st.cambio ? st.cambio.at - 30 * MIN : Date.now());
  const out = [{ at: v1, texto: desdeTienda ? "v1 generada desde la tienda" : "v1 generada" }];
  if (st.cambio) {
    out.push({ at: st.cambio.at, texto: `Cambio pedido por ${nombreCorto(st.empresa.atencion)}` });
    out.push({ at: st.cambio.v2, texto: "v2 enviada por Crimson" });
  }
  if (st.aprobada) out.push({ at: st.aprobada.at, texto: `Aprobada en línea por ${st.aprobada.nombre} · v${st.aprobada.version}` });
  return out;
}

export default function A3Cotizacion() {
  const [i, irA] = usePaso(IDS);
  const [st, set] = useGuardado<EstadoA3>(CLAVE, inicial, validar);
  const paso = IDS[i];
  return (
    <MarcoMaqueta
      id="A3" etapa={1} pasos={PASOS}
      titulo="Cotización formal para empresas"
      bajada="La empresa arma su pedido, descarga una cotización en PDF con la marca de Crimson y la aprueba en línea. Cada cambio queda como versión nueva."
      hoy="La plataforma ya maneja cotizaciones con versiones y aceptación, pero se arman a mano desde el sistema interno."
      conPropuesta="Desde «Arma tu sistema» o el carrito, la empresa genera la cotización formal, la aprueba o pide cambios en línea, y Crimson recibe la aprobación lista para convertir en pedido."
      aparte={<Aparte />}
    >
      <div className="ven">
        {paso === "kit" && <Kit onFormal={() => irA(1)} />}
        {paso === "empresa" && <DatosEmpresa st={st} set={set} onListo={() => irA(2)} />}
        {paso === "pdf" && <Pdf st={st} onAprobar={() => irA(3)} />}
        {paso === "aprobacion" && <Aprobacion st={st} set={set} onCrimson={() => irA(4)} />}
        {paso === "crimson" && <Crimson st={st} onAprobar={() => irA(3)} onReiniciar={() => { set(() => inicial()); irA(0); }} />}
      </div>
    </MarcoMaqueta>
  );
}

function Aparte() {
  return (
    <div className="ven ven-aparte">
      <p className="ven-aparte__t">Lo que gana Crimson</p>
      <p>Las empresas se cotizan solas, a cualquier hora, y cada aprobación llega con su versión, su fecha y el nombre de quien aprobó.</p>
      <p className="ven-aparte__nota">A3 no lleva cifra propia en «¿Es rentable?»: no la sumamos para no inflar.</p>
    </div>
  );
}

// ---------- 1 · el resultado real de "Arma tu sistema", con el boton nuevo ----------
function Kit({ onFormal }: { onFormal: () => void }) {
  const k = KIT_NEGOCIO;
  const subtotales = k.lineas.map((l) => r2(PRODUCTOS[l.id].conIva * l.cant));
  const ivaIncluido = subtotales.reduce((t, s) => t + (s - s / (1 + IVA)), 0);
  const unidades = k.lineas.reduce((n, l) => n + l.cant, 0);
  return (
    <Pantalla superficie="tienda" direccion="tienda.crimsoncr.com/arma-tu-sistema?place=negocio&indoor=4&outdoor=4&days=30">
      <div className="shop ven-shop">
        <div className="ven-kitw">
          <ul className="ven-resp" aria-label="Respuestas de Arma tu sistema">
            <li><small>Lugar</small><b>Negocio u oficina</b></li>
            <li><small>Cámaras</small><b>4 adentro · 4 afuera</b></li>
            <li><small>Grabación</small><b>30 días</b></li>
            <li><small>Celular</small><b>Sí</b></li>
          </ul>
          <section className="vt-kitres ven-kitres" aria-labelledby="ven-kit-t">
            <div className="vt-kitres__head">
              <span className="sh-meta">Kit sugerido · {k.camaras} CAM · {k.tb} TB</span>
              <h3 id="ven-kit-t" className="ven-kitres__h">{k.titulo}</h3>
            </div>
            <ul className="vt-kitres__lines">
              {k.lineas.map((l, n) => (
                <li key={l.id}>
                  <span className="vt-kitres__img"><FotoProducto id={l.id} tam={72} /></span>
                  <div className="vt-kitres__txt"><span className="sh-meta">{l.rol}</span><b>{PRODUCTOS[l.id].nombre}</b><small>{l.razon}</small></div>
                  <div className="vt-kitres__qty"><b>×{l.cant}</b><span>{money(subtotales[n])}</span></div>
                </li>
              ))}
              {k.faltantes.map((f) => (
                <li key={f.nombre} className="vt-kitres__missing">
                  <span className="vt-kitres__img"><Ico d={IC.tool} /></span>
                  <div className="vt-kitres__txt"><span className="sh-meta">{f.rol} · lo cotizamos</span><b>{f.nombre}</b><small>{f.razon}</small></div>
                  <div className="vt-kitres__qty"><b>×{f.cant}</b></div>
                </li>
              ))}
            </ul>
            <dl className="vt-kitres__sum">
              <div><dt>Equipo con precio</dt><dd>{unidades} artículos</dd></div>
              <div><dt>IVA incluido en el total</dt><dd>{money(ivaIncluido)}</dd></div>
              <div><dt>Materiales por cotizar</dt><dd>{k.faltantes.length} ítem</dd></div>
              <div><dt>Instalación</dt><dd>Se cotiza aparte</dd></div>
            </dl>
            <div className="vt-kitres__total"><span>Total de referencia del equipo</span><PriceTag p={{ display_price: k.total, display_currency: "CRC" }} big /></div>
            <ul className="vt-kitres__notes">{k.notas.map((n) => <li key={n}>{n}</li>)}</ul>
            <div className="vt-kitres__cta">
              <button type="button" className="sh-btn sh-btn--crimson sh-btn--block"
                onClick={() => avisar("Vista previa", "En la tienda real este botón lleva a la asesoría con el kit adjunto y crea la oportunidad en el CRM (ya funciona).")}>
                <Ico d={IC.tool} />Quiero que Crimson lo instale
              </button>
              <button type="button" className="sh-btn sh-btn--block"
                onClick={() => avisar("Vista previa", "En la tienda real este botón agrega los 15 artículos con precio al carrito (ya funciona).")}>
                <Ico d={IC.bag} />Agregar el equipo al carrito
              </button>
              <div className="ven-a3cta">
                <SelloPropuesta id="A3" tamano="chico" tono="oscuro" />
                <button type="button" className="sh-btn sh-btn--ghost sh-btn--block ven-a3cta__b" onClick={onFormal}><Ico d={IC.pdf} />Descargar cotización formal (PDF)</button>
                <small>Para empresas: con cédula jurídica, la marca de Crimson y aprobación en línea.</small>
              </div>
            </div>
          </section>
        </div>
      </div>
    </Pantalla>
  );
}

// ---------- 2 · datos de la empresa (precargados, editables en memoria) ----------
type Campo = "razon" | "cedula" | "atencion" | "correo";
function DatosEmpresa({ st, set, onListo }: { st: EstadoA3; set: (f: (p: EstadoA3) => EstadoA3) => void; onListo: () => void }) {
  const e = st.empresa;
  const [err, setErr] = useState<Partial<Record<Campo, string>>>({});
  const [ocupado, setOcupado] = useState(false);
  const t = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(t.current), []);
  const upd = <K extends keyof Empresa>(k: K, v: Empresa[K]) => set((p) => ({ ...p, empresa: { ...p.empresa, [k]: v } }));
  const generar = () => {
    const er: Partial<Record<Campo, string>> = {};
    if (!e.razon.trim()) er.razon = "Escribí la razón social de la empresa.";
    if (e.cedula.replace(/\D/g, "").length !== 10) er.cedula = "Escribí la cédula jurídica: 10 dígitos, por ejemplo 3-101-000000.";
    if (!e.atencion.trim()) er.atencion = "Escribí a quién va dirigida.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e.correo.trim())) er.correo = "Escribí un correo válido, por ejemplo compras@example.com.";
    setErr(er);
    const primero = (Object.keys(er) as Campo[])[0];
    if (primero) { document.getElementById(`ven-a3-${primero}`)?.focus(); return; }
    setOcupado(true);
    t.current = window.setTimeout(() => {
      setOcupado(false);
      set((p) => ({ ...p, v1: Date.now(), cambio: null, aprobada: null })); // una cotizacion nueva arranca en la v1
      onListo();
    }, 500);
  };
  const campo = (k: Campo, etiqueta: string, extra: { type?: string; autoComplete?: string; inputMode?: "email" | "text"; ayuda?: string } = {}) => (
    <div className="ven-campo">
      <label htmlFor={`ven-a3-${k}`}>{etiqueta}</label>
      <input id={`ven-a3-${k}`} className="ven-input" type={extra.type || "text"} autoComplete={extra.autoComplete} inputMode={extra.inputMode}
        value={e[k]} onChange={(ev) => upd(k, ev.target.value)} aria-invalid={err[k] ? true : undefined}
        aria-describedby={[err[k] ? `ven-a3-${k}-err` : "", extra.ayuda ? `ven-a3-${k}-ayuda` : ""].filter(Boolean).join(" ") || undefined} />
      {extra.ayuda && <small id={`ven-a3-${k}-ayuda`} className="ven-campo__ayuda">{extra.ayuda}</small>}
      {err[k] && <small id={`ven-a3-${k}-err`} className="ven-error">{err[k]}</small>}
    </div>
  );
  return (
    <Pantalla superficie="tienda" direccion="tienda.crimsoncr.com/arma-tu-sistema">
      <div className="ven-emp ven-cq">
        <form className="ven-emp__form" noValidate onSubmit={(ev) => { ev.preventDefault(); if (!ocupado) generar(); }} aria-labelledby="ven-emp-t">
          <p className="ven-emp__sello"><SelloPropuesta id="A3" tamano="chico" /><span>Cotización formal</span></p>
          <h3 id="ven-emp-t" className="ven-emp__t">¿A nombre de quién va la cotización?</h3>
          <div className="ven-emp__grid">
            {campo("razon", "Razón social", { autoComplete: "organization" })}
            {campo("cedula", "Cédula jurídica", { ayuda: "Cédula de ejemplo." })}
            {campo("atencion", "Atención", { autoComplete: "name" })}
            {campo("correo", "Correo", { type: "email", autoComplete: "email", inputMode: "email" })}
          </div>
          <fieldset className="ven-q ven-q--plano">
            <legend className="ven-q__l">¿Incluir la instalación estimada?</legend>
            <div className="ven-chips">
              <label className="ven-chip"><input type="radio" name="ven-a3-inst" checked={e.instalar} onChange={() => upd("instalar", true)} /><span>Sí</span></label>
              <label className="ven-chip"><input type="radio" name="ven-a3-inst" checked={!e.instalar} onChange={() => upd("instalar", false)} /><span>No, solo el equipo</span></label>
            </div>
            {e.instalar && <p className="ven-q__hint">8 cámaras · Negocio u oficina · con cableado: <b>{colones(INSTALACION.precio)} sin IVA</b></p>}
          </fieldset>
          <div className="ven-campo ven-campo--corto">
            <label htmlFor="ven-a3-vig">Vigencia</label>
            <select id="ven-a3-vig" className="ven-select" value={e.vigencia} onChange={(ev) => upd("vigencia", Number(ev.target.value))}>
              {VIGENCIAS.map((d) => <option key={d} value={d}>{d} días</option>)}
            </select>
          </div>
          <button type="submit" className="ven-btn ven-btn--crimson ven-emp__cta" aria-disabled={ocupado || undefined}>{ocupado ? "Generando…" : "Generar cotización"}</button>
          <p className="ven-rotulo">{ROTULO_TARIFAS}</p>
        </form>
        <aside className="ven-emp__res" aria-label="Lo que va en la cotización">
          <p className="ven-meta">Va en la cotización</p>
          <b className="ven-emp__kit">{KIT_NEGOCIO.titulo}</b>
          <dl className="ven-dl">
            <div><dt>Equipo (15 artículos)</dt><dd>{colones(KIT_NEGOCIO.total)} con IVA</dd></div>
            <div><dt>Instalación estimada</dt><dd>{e.instalar ? `${colones(INSTALACION.precio)} sin IVA` : "No incluida"}</dd></div>
            <div><dt>Conectores RJ45 ×16</dt><dd>Se cotizan con la visita</dd></div>
          </dl>
        </aside>
      </div>
    </Pantalla>
  );
}

// ---------- 3 · el PDF (vista previa dibujada en la pagina: no se genera ningun archivo) ----------
function TablaDoc({ lineas }: { lineas: LineaDoc[] }) {
  return (
    <table className="ven-tt ven-tt--ancha ven-doc__tabla" role="table" aria-label="Detalle de la cotización">
      <thead role="rowgroup">
        <tr role="row">
          <th role="columnheader" scope="col">Código</th><th role="columnheader" scope="col">Descripción</th>
          <th role="columnheader" scope="col" className="ven-num">Cant.</th><th role="columnheader" scope="col" className="ven-num">Precio unit. sin IVA</th>
          <th role="columnheader" scope="col" className="ven-num">Total sin IVA</th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {lineas.map((l) => (
          <tr role="row" key={l.codigo} data-nueva={l.nueva || undefined}>
            <td role="cell" className="ven-doc__cod" data-label="Código">{l.codigo}</td>
            <th role="rowheader" scope="row" className="ven-doc__desc">{l.descripcion}{l.nueva && <span className="ven-mas1">+ 1 línea</span>}</th>
            <td role="cell" className="ven-num" data-label="Cant.">{l.cant}</td>
            <td role="cell" className="ven-num" data-label="Precio unit. sin IVA">{num2(l.unit)}</td>
            <td role="cell" className="ven-num" data-label="Total sin IVA">{num2(r2(l.cant * l.unit))}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
function Totales({ t }: { t: ReturnType<typeof totales> }) {
  return (
    <dl className="ven-tot">
      <div><dt>Subtotal</dt><dd>{colones2(t.sub)}</dd></div>
      <div><dt>IVA 13 %</dt><dd>{colones2(t.iva)}</dd></div>
      <div className="ven-tot__total"><dt>Total</dt><dd>{colones2(t.total)}</dd></div>
    </dl>
  );
}
function Pdf({ st, onAprobar }: { st: EstadoA3; onAprobar: () => void }) {
  const v = versionVigente(st);
  const e = st.empresa;
  const lineas = lineasDe(v, e.instalar);
  const t = totales(lineas);
  const emitida = new Date(st.cambio?.v2 ?? st.v1 ?? Date.now());
  const vence = new Date(emitida.getTime() + e.vigencia * 86_400_000);
  const aviso = "En la tienda real se genera el PDF con la marca de Crimson y se envía a tu correo. En la vista previa no se genera ningún archivo.";
  return (
    <div className="ven-pdfw">
      <Pantalla superficie="pdf" rotulo={`Documento PDF · ${DOC.cotizacion}-v${v}.pdf`}>
        <article className="ven-doc ven-cq" aria-label={`Vista previa del PDF de la cotización ${DOC.cotizacion}, versión ${v}`}>
          <header className="ven-doc__cab">
            <div className="ven-doc__marca">
              <img src={`${import.meta.env.BASE_URL}logo.png`} alt="Crimson Consulting" width={1024} height={237} loading="lazy" decoding="async" />
              <p>{LEMA}</p>
            </div>
            <div className="ven-doc__num">
              <b>Cotización</b>
              <span>{DOC.cotizacion} · Versión {v}</span>
              <span>Fecha: {fechaNum(emitida)}</span>
              <span>Válida hasta: {fechaNum(vence)}</span>
            </div>
          </header>
          <p className="ven-doc__para"><b>Para:</b> {e.razon} · Céd. jurídica {e.cedula} · Atención: {e.atencion}</p>
          <TablaDoc lineas={lineas} />
          <p className="ven-doc__no">No incluye: 16 conectores RJ45 (se cotizan con la visita).</p>
          <Totales t={t} />
          <section className="ven-doc__cond" aria-label="Condiciones">
            <b>Condiciones</b>
            <p>Precios en colones. Disponibilidad sujeta a inventario al aprobar. Pago por transferencia o SINPE Móvil. Garantía de fábrica gestionada por Crimson.{e.instalar ? " La instalación es una estimación: un técnico la confirma en visita." : ""}</p>
          </section>
          <p className="ven-doc__pie">Aprobá esta cotización en línea desde el enlace del correo.</p>
        </article>
      </Pantalla>
      <p className="ven-rotulo">{ROTULO_TARIFAS}</p>
      <div className="ven-acciones">
        <button type="button" className="ven-btn ven-btn--crimson" onClick={() => avisar("Descargar PDF", aviso)}><Ico d={IC.pdf} />Descargar PDF</button>
        <button type="button" className="ven-btn ven-btn--ghost" onClick={() => avisar("Enviar por correo", aviso)}>Enviármela por correo</button>
        <button type="button" className="ven-btn ven-btn--tinta" onClick={onAprobar}>Ver cómo la aprueba {nombreCorto(e.atencion)} <span aria-hidden="true">→</span></button>
      </div>
    </div>
  );
}

// ---------- 4 · aprobacion en linea desde el enlace del correo ----------
function Aprobacion({ st, set, onCrimson }: { st: EstadoA3; set: (f: (p: EstadoA3) => EstadoA3) => void; onCrimson: () => void }) {
  const vigente = versionVigente(st);
  const e = st.empresa;
  const [tab, setTab] = useState<Version>(vigente);
  const [modo, setModo] = useState<null | "cambio" | "aprobar">(null);
  const [texto, setTexto] = useState(CAMBIO_A3);
  const [nombre, setNombre] = useState(e.atencion);
  const [confirmo, setConfirmo] = useState(false);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => { setTab(vigente); }, [vigente]);
  const versiones: Version[] = st.cambio ? [1, 2] : [1];
  const lineas = lineasDe(tab, e.instalar);
  const t = totales(lineas);
  const apr = st.aprobada;

  const teclas = (ev: KeyboardEvent<HTMLButtonElement>, n: number) => {
    if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft" && ev.key !== "Home" && ev.key !== "End") return;
    ev.preventDefault();
    const k = ev.key === "Home" ? 0 : ev.key === "End" ? versiones.length - 1 : (n + (ev.key === "ArrowRight" ? 1 : -1) + versiones.length) % versiones.length;
    setTab(versiones[k]);
    tabs.current[k]?.focus();
  };
  const pedirCambio = () => {
    if (st.cambio) {
      avisar("Pedir otro cambio", "En la tienda real podés pedir más cambios: cada uno crea una versión nueva. El guion de esta maqueta llega hasta la v2.");
      return;
    }
    setModo("cambio");
  };
  const enviarCambio = () => {
    const ahora = Date.now();
    // la maqueta adelanta el reloj: la v2 la prepara una persona de Crimson; aqui llega al instante
    set((p) => ({ ...p, v1: Math.min(p.v1 ?? Infinity, ahora - 55 * MIN), cambio: { texto: texto.trim() || CAMBIO_A3, at: ahora - 25 * MIN, v2: ahora } }));
    setModo(null);
  };
  const aprobar = () => {
    if (!confirmo || !nombre.trim()) return;
    set((p) => ({ ...p, aprobada: { version: vigente, nombre: nombre.trim(), at: Date.now() } }));
    setModo(null);
  };

  return (
    <Pantalla superficie="portal" direccion={`tienda.crimsoncr.com/cotizacion/${DOC.cotizacion}`}>
      <div className="ven-apr ven-cq">
        <p className="ven-meta">Enlace personal del correo · {e.razon}</p>
        <h3 className="ven-apr__t">Cotización {DOC.cotizacion} para {e.razon}</h3>

        <div className="ven-tabs" role="tablist" aria-label="Versiones de la cotización">
          {versiones.map((v, n) => (
            <button key={v} ref={(el) => { tabs.current[n] = el; }} type="button" role="tab" id={`ven-apr-tab-${v}`}
              aria-selected={tab === v} aria-controls="ven-apr-panel" tabIndex={tab === v ? 0 : -1}
              onClick={() => setTab(v)} onKeyDown={(ev) => teclas(ev, n)}>
              Versión {v}{v === vigente ? " · vigente" : ""}
            </button>
          ))}
        </div>
        <div role="tabpanel" id="ven-apr-panel" aria-labelledby={`ven-apr-tab-${tab}`} className="ven-apr__panel" tabIndex={0}>
          {tab === 2 && <p className="ven-apr__nueva"><span className="ven-mas1">+ 1 línea</span>La v2 agrega la UPS que pidió {nombreCorto(e.atencion)}.</p>}
          <ul className="ven-apr__lineas">
            {lineas.map((l) => (
              <li key={l.codigo} data-nueva={l.nueva || undefined}>
                <span className="ven-apr__d">{l.descripcion}<small>{l.codigo} · {l.cant} × {num2(l.unit)}</small></span>
                <b>{num2(r2(l.cant * l.unit))}</b>
              </li>
            ))}
          </ul>
          <Totales t={t} />
        </div>

        {apr ? (
          <div className="ven-apr__ok">
            <p className="ven-sello-apr" role="status">
              <b>APROBADA · v{apr.version}</b>
              <span><span className="ven-sr"> · </span>{fechaNum(new Date(apr.at))} · {apr.nombre}</span>
            </p>
            <p><b>Qué sigue:</b> Crimson recibe la aprobación, aparta el equipo y emite la factura electrónica a nombre de la empresa.</p>
            <button type="button" className="ven-btn ven-btn--tinta" onClick={onCrimson}>Ver cómo le llega a Crimson <span aria-hidden="true">→</span></button>
          </div>
        ) : tab !== vigente ? (
          <p className="ven-apr__vieja">Esta versión fue reemplazada por la v{vigente}: solo se aprueba la vigente.</p>
        ) : modo === "cambio" ? (
          <form className="ven-apr__form" onSubmit={(ev) => { ev.preventDefault(); enviarCambio(); }}>
            <div className="ven-campo">
              <label htmlFor="ven-apr-cambio">¿Qué cambio necesitás?</label>
              <textarea id="ven-apr-cambio" className="ven-input ven-textarea" rows={3} maxLength={300} value={texto} onChange={(ev) => setTexto(ev.target.value)} />
            </div>
            <div className="ven-acciones">
              <button type="submit" className="ven-btn ven-btn--crimson">Enviar</button>
              <button type="button" className="ven-btn ven-btn--ghost" onClick={() => setModo(null)}>Cancelar</button>
            </div>
          </form>
        ) : modo === "aprobar" ? (
          <form className="ven-apr__form" onSubmit={(ev) => { ev.preventDefault(); aprobar(); }}>
            <div className="ven-campo">
              <label htmlFor="ven-apr-nombre">Nombre de quien aprueba</label>
              <input id="ven-apr-nombre" className="ven-input" autoComplete="name" value={nombre} onChange={(ev) => setNombre(ev.target.value)} />
            </div>
            <label className="ven-check">
              <input type="checkbox" checked={confirmo} onChange={(ev) => setConfirmo(ev.target.checked)} />
              <span>Confirmo que puedo aprobar compras en nombre de la empresa</span>
            </label>
            <div className="ven-acciones">
              <button type="submit" className="ven-btn ven-btn--crimson" disabled={!confirmo || !nombre.trim()} aria-describedby={confirmo ? undefined : "ven-apr-pista"}>Aprobar versión {vigente}</button>
              <button type="button" className="ven-btn ven-btn--ghost" onClick={() => setModo(null)}>Cancelar</button>
            </div>
            {!confirmo && <p id="ven-apr-pista" className="ven-campo__ayuda">Marcá la casilla para poder aprobar.</p>}
          </form>
        ) : (
          <div className="ven-acciones">
            <button type="button" className="ven-btn ven-btn--crimson" onClick={() => setModo("aprobar")}>Aprobar versión {vigente}</button>
            <button type="button" className="ven-btn ven-btn--ghost" onClick={pedirCambio}>Pedir un cambio</button>
          </div>
        )}

        <section className="ven-hist" aria-labelledby="ven-hist-t">
          <h4 id="ven-hist-t" className="ven-hist__t">Versiones</h4>
          <ol className="ven-linea">{historial(st).map((h) => <li key={h.texto}><b>{h.texto}</b><span>{cuando(h.at)}</span></li>)}</ol>
          {st.cambio && <p className="ven-campo__ayuda">La maqueta adelanta el reloj: en la tienda real, la v2 la prepara una persona de Crimson.</p>}
        </section>
      </div>
    </Pantalla>
  );
}

// ---------- 5 · la fila que le llega a Crimson ----------
function Crimson({ st, onAprobar, onReiniciar }: { st: EstadoA3; onAprobar: () => void; onReiniciar: () => void }) {
  const apr = st.aprobada;
  const v = apr?.version ?? versionVigente(st);
  const t = totales(lineasDe(v, st.empresa.instalar));
  return (
    <div className="ven-crimw">
      <Pantalla superficie="admin" seccion="Cotizaciones" estado="existente">
        <div className="ven-adm">
          <div className="page-head"><div><div className="meta">02 · Facturación</div><h3 className="h1">Cotizaciones</h3></div></div>
          <article className="card ven-cot" aria-labelledby="ven-cot-t">
            <div className="ven-fila">
              <b id="ven-cot-t" className="ven-fila__n">{DOC.cotizacion}</b>
              <span>{st.empresa.razon}</span>
              <span className="ven-fila__v">v{v}</span>
              <span className={`badge ${apr ? "badge--ok" : "badge--muted"}`}>{apr ? "Aprobada en línea" : "Esperando respuesta"}</span>
              <b className="money ven-fila__m">{colones(t.total)}</b>
              <button type="button" className="btn btn--crimson btn--sm ven-fila__b" disabled={!apr}
                onClick={() => avisar("Convertir en pedido", `En la tienda real se crea el pedido ${DOC.pedidoA3} y se reserva el inventario.`)}>
                Convertir en pedido
              </button>
            </div>
            {!apr && <p className="ven-cot__pista">Todavía no la aprueban. <button type="button" className="ven-link" onClick={onAprobar}>Aprobarla como {nombreCorto(st.empresa.atencion)} <span aria-hidden="true">→</span></button></p>}
            <div className="ven-cot__cuerpo">
              <span className="meta">Historial</span>
              <ol className="ven-linea">{historial(st).map((h) => <li key={h.texto}><b>{h.texto}</b><span>{cuando(h.at)}</span></li>)}</ol>
              <p className="ven-cot__nota">Cada versión y la aprobación quedan en la bitácora de auditoría.</p>
            </div>
          </article>
          <ul className="ven-sellos">
            <SelloLinea estado="existente">Cotizaciones con versiones y aceptación</SelloLinea>
            <SelloLinea estado="propuesta" id="A3">La genera y la aprueba el cliente desde la tienda</SelloLinea>
          </ul>
        </div>
      </Pantalla>
      <p className="ven-reinicio"><button type="button" className="ven-link" onClick={onReiniciar}>Volver a empezar esta maqueta</button></p>
    </div>
  );
}
