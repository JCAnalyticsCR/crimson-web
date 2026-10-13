/* Maqueta B1 · «La compra abre el portal del cliente» (PROPUESTA, no incluida aun).
   El recorrido de Maria despues de comprar: correo «Activa tu cuenta» -> activar sin contrasena -> Mis compras
   (con factura, XML, guia y «Volver a comprar»; arriba los pedidos de prueba de esta sesion) -> Mis equipos
   (serie y garantia de cada unidad) -> reporte de falla desde el equipo -> asi le llega el caso a Crimson.
   Pantalla de trabajo: nada se mueve solo; el unico momento es la entrada del caso del lado de Crimson.
   Todo es de ejemplo y local: la foto no sale del navegador, los botones «reales» solo muestran el aviso. */
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { notifyDemo } from "../../mockApi";
import {
  HiloCarmesi, MarcoMaqueta, Pantalla, SelloPropuesta, almacen, colones, fecha, hora, leerPedidosDemo, usePaso,
  type Paso, type PedidoDemo,
} from "./comun";
import {
  EQUIPOS, EQUIPO_FALLA, MARIA, MOTIVOS, ORD097, ORD128, TEXTO_FALLA, TICKET, TIPO_TXT, articulos, diasEntre, fechaAno, haceTanto, momentos,
  sumarDias, type Equipo, type Momentos, type PedidoEj,
} from "./datos";
import { CabezaSeccion, FotoProducto, IC, Ico, LOGO, PortalCliente } from "./piezas";
import "./cliente.css";

const PASOS: readonly Paso[] = [
  { id: "correo", titulo: "El correo de activación", vista: "cliente" },
  { id: "activar", titulo: "Activar la cuenta", vista: "cliente" },
  { id: "compras", titulo: "Mis compras", vista: "cliente" },
  { id: "equipos", titulo: "Mis equipos", vista: "cliente" },
  { id: "falla", titulo: "Reportar una falla", vista: "cliente" },
  { id: "crimson", titulo: "Así le llega a Crimson", vista: "crimson" },
];
const IDS = PASOS.map((p) => p.id);
const N = { correo: 0, activar: 1, compras: 2, equipos: 3, falla: 4, crimson: 5 } as const;

// estado de la maqueta (solo de esta sesion; si el almacenamiento falla, vive en memoria)
const CLAVE = "crimson-demo:cliente:b1";
type EstadoB1 = { activada: boolean; equipo: string; motivo: string; texto: string; enviado: boolean };
const inicial = (serie = EQUIPO_FALLA): EstadoB1 => {
  const eq = EQUIPOS.find((e) => e.serie === serie) ?? EQUIPOS[1];
  return { activada: false, equipo: eq.serie, motivo: MOTIVOS[eq.tipo][0], texto: TEXTO_FALLA[eq.tipo], enviado: false };
};
function leerB1(): EstadoB1 {
  try {
    const x = JSON.parse(almacen.sesion.get(CLAVE) || "null") as Partial<EstadoB1> | null;
    if (!x || typeof x !== "object") return inicial();
    const base = inicial(typeof x.equipo === "string" ? x.equipo : undefined);
    const eq = EQUIPOS.find((e) => e.serie === base.equipo)!;
    return {
      activada: !!x.activada, equipo: base.equipo,
      motivo: typeof x.motivo === "string" && MOTIVOS[eq.tipo].includes(x.motivo) ? x.motivo : base.motivo,
      texto: typeof x.texto === "string" ? x.texto.slice(0, 600) : base.texto, enviado: !!x.enviado,
    };
  } catch { return inicial(); }
}

const PAGADO = ["pagado", "preparando", "listo", "enviado", "entregado"];
const ETIQUETA: Record<string, string> = {
  nuevo: "Pendiente de pago", en_revision: "Comprobante en revisión", pagado: "Pagado", preparando: "Preparando", listo: "Listo para retiro",
  enviado: "Enviado", entregado: "Entregado", cancelado: "Cancelado", reembolsado: "Reembolsado",
};
const fechasDe = (eq: Equipo, m: Momentos) =>
  eq.pedido === "ORD-000128" ? { compra: m.compra128, entrega: m.entrega128, garantia: m.garantia128 } : { compra: m.compra097, entrega: m.entrega097, garantia: m.garantia097 };

const volverAComprar = () => notifyDemo({ title: "Volver a comprar", text: "En la tienda real, «Volver a comprar» pone los mismos productos en el carrito con el precio de hoy." });

export default function B1Portal() {
  const [i, irA] = usePaso(IDS);
  const paso = IDS[i];
  const m = useMemo(momentos, []);
  // los pedidos de prueba de la sesion se releen en cada paso (y su estado, que cambia desde Detras de escena)
  const demos = useMemo(() => leerPedidosDemo(), [paso]);
  const [st, setSt] = useState<EstadoB1>(leerB1);
  useEffect(() => { almacen.sesion.set(CLAVE, JSON.stringify(st)); }, [st]);

  // la foto vive solo en memoria como URL local (blob:); se revoca al cambiarla y al salir
  const [foto, setFoto] = useState<{ url: string; nombre: string } | null>(null);
  const fotoUrl = useRef<string | null>(null);
  useEffect(() => () => { if (fotoUrl.current) URL.revokeObjectURL(fotoUrl.current); }, []);
  const ponerFoto = (f: File | null) => {
    if (fotoUrl.current) URL.revokeObjectURL(fotoUrl.current);
    fotoUrl.current = f ? URL.createObjectURL(f) : null;
    setFoto(f && fotoUrl.current ? { url: fotoUrl.current, nombre: f.name } : null);
  };

  const eq = EQUIPOS.find((e) => e.serie === st.equipo) ?? EQUIPOS[1];
  const elegirEquipo = (serie: string) => {
    setSt((s) => (s.equipo === serie ? { ...s, enviado: false } : { ...inicial(serie), activada: s.activada }));
    if (serie !== st.equipo) ponerFoto(null);
    irA(N.falla);
  };
  const demo = demos[0];

  return (
    <MarcoMaqueta
      id="B1" etapa={1}
      titulo="La compra abre el portal del cliente"
      bajada="Cada compra deja al cliente con cuenta: compras, facturas, equipos con garantía y soporte en un solo lugar."
      hoy="El portal del cliente ya existe (roles, reportes con fotos, equipos, mantenimientos, cotizaciones y facturas en PDF), pero el acceso es por solicitud aprobada y las compras de la tienda no aparecen solas."
      conPropuesta="Al comprar, el cliente recibe «Activá tu cuenta»; sus compras, facturas y equipos ya están adentro, y cada equipo tiene su garantía y su botón de soporte."
      pasos={PASOS}
      aparte={<Aparte paso={paso} />}
    >
      <div className="cli">
        <p className="cli-escena">
          <span className="cli-escena__k">Escena</span>
          <span>El pedido ya se entregó: la compra fue {haceTanto(m.compra128)} y la entrega {haceTanto(m.entrega128)}.</span>
        </p>
        {paso === "correo" && <Correo m={m} onActivar={() => irA(N.activar)} />}
        {paso === "activar" && (
          <Activar activada={st.activada} onActivar={() => setSt((s) => ({ ...s, activada: true }))} onSeguir={() => irA(N.compras)} />
        )}
        {paso === "compras" && (
          <>
            <Compras m={m} demos={demos} />
            <HiloCarmesi aqui="portal" numero={demo ? demo.number : "ORD-000128"} token={demo ? demo.token : "ejemplo-entregado"} />
          </>
        )}
        {paso === "equipos" && <Equipos m={m} demos={demos} onFalla={elegirEquipo} />}
        {paso === "falla" && (
          <Falla
            eq={eq} m={m} st={st} foto={foto}
            onCambio={(p) => setSt((s) => ({ ...s, ...p }))} onFoto={ponerFoto}
            onEquipos={() => irA(N.equipos)} onCrimson={() => irA(N.crimson)}
          />
        )}
        {paso === "crimson" && <Crimson eq={eq} m={m} st={st} foto={foto} />}
      </div>
    </MarcoMaqueta>
  );
}

// ---------------------------------------------------------------- columna «Que pasa aqui»
const QUE_PASA: Record<string, { t: string; p: string; ya?: string }> = {
  correo: {
    t: "La cuenta nace con la compra",
    p: "Al confirmarse el pedido, la plataforma crea la cuenta de María con el correo de la compra y le manda este enlace. No tiene que pedir acceso ni esperar a que alguien lo apruebe.",
    ya: "Hoy el acceso al portal es por solicitud que Crimson aprueba, y ya funciona.",
  },
  activar: {
    t: "Sin contraseña que recordar",
    p: "El enlace es personal y de un solo uso. María entra con un enlace a su correo cada vez y queda como administradora de su cuenta: puede invitar a alguien de su casa o empresa.",
    ya: "Los roles de administradora y de usuaria del portal ya existen.",
  },
  compras: {
    t: "Sus compras, sin buscar por número",
    p: "Cada pedido de la tienda aparece solo, con su estado, su factura electrónica en PDF y XML, y la guía del envío. «Volver a comprar» deja la siguiente venta a un toque.",
    ya: "Si hiciste un pedido de prueba en esta vista previa, aparece de primero.",
  },
  equipos: {
    t: "Cada unidad, con su serie y su garantía",
    p: "Al despachar el pedido, cada equipo se crea con su serie y la fecha de fin de garantía. Desde aquí María reporta una falla o pide que Crimson se lo instale.",
    ya: "Los equipos del cliente con serie y garantía ya existen en el portal; lo nuevo es que se creen solos desde la tienda.",
  },
  falla: {
    t: "El reporte nace del equipo",
    p: "María no tiene que explicar qué compró ni cuándo: el reporte sale con la compra, la serie y la garantía ya identificadas, y con la foto si la agrega.",
    ya: "Los reportes con fotos desde el portal ya existen.",
  },
  crimson: {
    t: "Entra a la mesa de soporte que ya existe",
    p: "El caso llega con todo lo que el técnico necesita y corre con su prioridad y su tiempo de respuesta. Nadie tiene que preguntar «¿cuándo lo compró?».",
  },
};
function Aparte({ paso }: { paso: string }) {
  const q = QUE_PASA[paso] ?? QUE_PASA.correo;
  return (
    <div className="cli cli-aparte">
      <p className="cli-meta">Qué pasa aquí</p>
      <h3 className="cli-aparte__h">{q.t}</h3>
      <p>{q.p}</p>
      {q.ya && <p className="cli-aparte__ya"><span aria-hidden="true">✓ </span>{q.ya}</p>}
      <div className="cli-aparte__gana">
        <p className="cli-meta">Lo que gana Crimson</p>
        <p>El cliente vuelve a su cuenta para recomprar, pedir soporte o sumar un plan, y cada equipo vendido queda registrado con su garantía.</p>
        <Link to="/rentabilidad?enfoque=B1" className="cli-flecha">Ver cuánto suma en ¿Es rentable? →</Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 1 · correo
function Correo({ m, onActivar }: { m: Momentos; onActivar: () => void }) {
  const enviado = new Date(m.compra128.getTime() + 3 * 60000);
  return (
    <Pantalla superficie="correo">
      <article className="cli-mail" aria-labelledby="cli-mail-asunto">
        <dl className="cli-mail__head">
          <div><dt>De</dt><dd>Tienda Crimson</dd></div>
          <div><dt>Para</dt><dd>{MARIA.correo}</dd></div>
          <div><dt>Fecha</dt><dd>{fecha(enviado, "dia")} · {hora(enviado)}</dd></div>
        </dl>
        <h3 id="cli-mail-asunto" className="cli-mail__asunto">María, tu pedido ORD-000128 ya tiene su cuenta en Crimson</h3>
        <div className="cli-mail__body">
          <img className="cli-mail__logo" src={LOGO} alt="Crimson" width={121} height={28} decoding="async" />
          <p className="cli-mail__hola">Hola, María: gracias por tu compra. Activá tu cuenta para ver tus compras, tus facturas y la garantía de tus equipos, y pedir soporte cuando lo necesités.</p>
          <div className="cli-mail__ped">
            <span className="cli-meta">Tu pedido</span>
            <b className="cli-mono">ORD-000128</b>
            <span>{articulos(ORD128)} artículos · {colones(ORD128.total)}</span>
          </div>
          <button type="button" className="cli-btn cli-btn--crimson cli-mail__cta" onClick={onActivar}>Activar mi cuenta</button>
          <p className="cli-mail__fine">Este enlace es personal, sirve una sola vez y vence en 7 días (plazo de ejemplo). Si no hiciste esta compra, ignorá este correo.</p>
        </div>
      </article>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 2 · activar (sin ningun campo de contrasena)
function Activar({ activada, onActivar, onSeguir }: { activada: boolean; onActivar: () => void; onSeguir: () => void }) {
  const listo = useRef<HTMLHeadingElement>(null);
  const antes = useRef(activada);
  useEffect(() => {
    if (activada && !antes.current) listo.current?.focus();
    antes.current = activada;
  }, [activada]);
  return (
    <Pantalla superficie="portal" ancho="telefono" direccion="app.crimsoncr.com/activar">
      <div className="cli-act">
        <img className="cli-act__logo" src={LOGO} alt="Crimson" width={104} height={24} decoding="async" />
        {!activada ? (
          <>
            <h3 className="cli-act__h">Hola, María.</h3>
            <p className="cli-act__p">Tu cuenta usa este correo: <b>{MARIA.correo}</b>.</p>
            <fieldset className="cli-opciones">
              <legend>¿Cómo querés entrar?</legend>
              <label className="cli-opcion">
                <input type="radio" name="cli-acceso" checked onChange={() => undefined} />
                <span>Entrar con un enlace a mi correo cada vez<small>Te mandamos un enlace cada vez que querás entrar.</small></span>
              </label>
              <label className="cli-opcion" data-off="true">
                <input type="radio" name="cli-acceso" disabled />
                <span>Con contraseña<small>En la vista previa no se crean contraseñas.</small></span>
              </label>
            </fieldset>
            <button type="button" className="cli-btn cli-btn--crimson cli-btn--block" onClick={onActivar}>Activar mi cuenta</button>
          </>
        ) : (
          <div className="cli-ok" role="status">
            <span className="cli-ok__ico" aria-hidden="true"><Ico d={IC.check} size={20} /></span>
            <h3 ref={listo} tabIndex={-1} className="cli-act__h">Listo, María. Tu cuenta quedó activa.</h3>
            <p className="cli-act__p">Sos la persona administradora: podés invitar a alguien de tu casa o empresa.</p>
            <button type="button" className="cli-btn cli-btn--ink cli-btn--block" onClick={onSeguir}>Ver mis compras →</button>
          </div>
        )}
      </div>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 3 · mis compras
function Compras({ m, demos }: { m: Momentos; demos: PedidoDemo[] }) {
  return (
    <Pantalla superficie="portal" direccion="app.crimsoncr.com/mis-compras">
      <PortalCliente actual="compras">
        <CabezaSeccion rotulo="Portal del cliente" titulo="Mis compras">
          <span className="cli-meta">{demos.length + 2} pedidos</span>
        </CabezaSeccion>
        {demos.length > 0 && (
          <ul className="cli-pedidos" aria-label="Tus pedidos de prueba de esta vista previa">
            {demos.map((d) => <PedidoDeSesion key={d.token} d={d} />)}
          </ul>
        )}
        <ul className="cli-pedidos" aria-label="Pedidos de ejemplo de María">
          <Pedido p={ORD128} compra={m.compra128} estado="Entregado" mi="/?pedido=ejemplo-entregado" />
          <Pedido p={ORD097} compra={m.compra097} estado="Entregado" />
        </ul>
      </PortalCliente>
    </Pantalla>
  );
}

function Pedido({ p, compra, estado, mi }: { p: PedidoEj; compra: Date; estado: string; mi?: string }) {
  const [abierto, setAbierto] = useState(false);
  const det = `cli-det-${p.numero}`;
  const n = articulos(p);
  return (
    <li className="cli-ped">
      <div className="cli-ped__top">
        <div className="cli-ped__id">
          <b className="cli-mono">{p.numero}</b>
          <span className="cli-estado" data-s="entregado">{estado}</span>
        </div>
        <b className="cli-ped__total">{colones(p.total)}</b>
      </div>
      <p className="cli-ped__meta">
        {haceTanto(compra)} · {fechaAno(compra)} · {n === 1 ? `${p.lineas[0].cantidad} × ${p.lineas[0].nombre}` : `${n} artículos`}
      </p>
      <div className="cli-ped__fotos" aria-hidden="true">{p.lineas.map((l) => <FotoProducto key={l.id} id={l.id} tam="sm" />)}</div>
      <ul className="cli-docs" aria-label={`Documentos del pedido ${p.numero}`}>
        <li><button type="button" className="cli-doc" onClick={() => notifyDemo({ title: `Factura ${p.factura}`, text: `En el portal real se descarga el PDF de la factura electrónica ${p.factura} con la marca de Crimson. En la vista previa no se genera ningún archivo.` })}><Ico d={IC.pdf} size={16} />Factura {p.factura} (PDF)</button></li>
        <li><button type="button" className="cli-doc" onClick={() => notifyDemo({ title: "XML de la factura", text: `En el portal real se descarga el XML de la factura ${p.factura}, el comprobante electrónico que pide Hacienda. En la vista previa no se genera ningún archivo.` })}><Ico d={IC.doc} size={16} />XML</button></li>
        {p.guia && <li className="cli-guia"><Ico d={IC.truck} size={16} /><span>Guía <span className="cli-mono">{p.guia}</span> · {p.transportista}</span></li>}
      </ul>
      <div className="cli-ped__acts">
        <button type="button" className="cli-btn cli-btn--crimson cli-btn--sm" onClick={volverAComprar}>Volver a comprar</button>
        <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" aria-expanded={abierto} aria-controls={det} onClick={() => setAbierto((x) => !x)}>
          {abierto ? "Ocultar detalle" : "Ver detalle"}
        </button>
      </div>
      <div id={det} className="cli-ped__det" hidden={!abierto}>
        <ul className="cli-lineas">
          {p.lineas.map((l) => <li key={l.id}><span>{l.cantidad} × {l.nombre}</span><b>{colones(l.total)}</b></li>)}
          <li><span>{p.envio.nombre}</span><b>{colones(p.envio.total)}</b></li>
        </ul>
        <dl className="cli-totales">
          <div><dt>Subtotal (sin IVA)</dt><dd>{colones(p.subtotal)}</dd></div>
          <div><dt>Envío (sin IVA)</dt><dd>{colones(p.envioSinIva)}</dd></div>
          <div><dt>IVA 13 %</dt><dd>{colones(p.iva)}</dd></div>
          <div className="cli-totales__big"><dt>Total</dt><dd>{colones(p.total)}</dd></div>
        </dl>
        <p className="cli-note">Pago: {p.pago} · {p.entrega}</p>
        {mi && <Link className="cli-flecha" to={mi}>Verlo como en «Mi pedido», que ya existe →</Link>}
      </div>
    </li>
  );
}

function PedidoDeSesion({ d }: { d: PedidoDemo }) {
  const n = (d.lines || []).filter((l) => !l.shipping).reduce((a, l) => a + (Number(l.quantity) || 0), 0);
  const pagado = PAGADO.includes(d.status);
  return (
    <li className="cli-ped cli-ped--demo">
      <span className="cli-ped__chip">Tu pedido de prueba</span>
      <div className="cli-ped__top">
        <div className="cli-ped__id">
          <b className="cli-mono">{d.number}</b>
          <span className="cli-estado" data-s={d.status}>{ETIQUETA[d.status] ?? d.status_label}</span>
        </div>
        <b className="cli-ped__total">{colones(Number(d.total) || 0)}</b>
      </div>
      <p className="cli-ped__meta">{d.created_at ? `${fecha(d.created_at)} · ${hora(d.created_at)} · ` : ""}{n} {n === 1 ? "artículo" : "artículos"}{d.payment_method ? ` · ${d.payment_method}` : ""}</p>
      <p className="cli-note">{pagado ? "La factura electrónica salió sola al confirmarse el pago." : "La factura electrónica sale sola cuando Crimson confirma el pago."}</p>
      <div className="cli-ped__acts">
        <Link className="cli-btn cli-btn--ghost cli-btn--sm" to={`/?pedido=${encodeURIComponent(d.token)}`}>Ver en «Mi pedido»</Link>
        <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={volverAComprar}>Volver a comprar</button>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------- 4 · mis equipos
function Equipos({ m, demos, onFalla }: { m: Momentos; demos: PedidoDemo[]; onFalla: (serie: string) => void }) {
  const despachado = demos.find((d) => d.status === "enviado" || d.status === "entregado");
  return (
    <Pantalla superficie="portal" direccion="app.crimsoncr.com/mis-equipos">
      <PortalCliente actual="equipos">
        <CabezaSeccion rotulo="Instalaciones" titulo="Mis equipos">
          <span className="cli-meta">{EQUIPOS.length} equipos · 2 compras</span>
        </CabezaSeccion>
        <ul className="cli-eqs">
          {EQUIPOS.map((e) => <TarjetaEquipo key={e.serie} e={e} m={m} onFalla={onFalla} />)}
        </ul>
        <p className="cli-note">Cada equipo se crea solo al despachar el pedido, con su serie. Plazos de ejemplo: el real depende de la marca y el modelo.</p>
        {despachado && <p className="cli-note">Los equipos de tu pedido de prueba {despachado.number} aparecerían aquí con su serie al despacharse; en la vista previa no se crean.</p>}
      </PortalCliente>
    </Pantalla>
  );
}

function TarjetaEquipo({ e, m, onFalla }: { e: Equipo; m: Momentos; onFalla: (serie: string) => void }) {
  const f = fechasDe(e, m);
  const total = Math.max(1, diasEntre(f.entrega, f.garantia));
  const usado = Math.min(total, Math.max(0, diasEntre(f.entrega, m.ahora)));
  const quedan = total - usado;
  const pct = Math.max(1.5, (usado / total) * 100);
  const instalable = e.tipo === "camara" || e.tipo === "grabador";
  const tit = `cli-eq-${e.serie}`;
  return (
    <li className="cli-eq">
      <FotoProducto id={e.id} />
      <div className="cli-eq__main">
        <p className="cli-eq__tipo">{TIPO_TXT[e.tipo]} · Serie <span className="cli-mono">{e.serie}</span></p>
        <h4 id={tit} className="cli-eq__n">{e.nombre}</h4>
        {e.nota && <p className="cli-eq__nota">{e.nota}</p>}
        <p className="cli-eq__ped">Comprado en {e.pedido} · entregado el {fechaAno(f.entrega)}</p>
        <div className="cli-gar">
          <div className="cli-gar__row"><span>Garantía hasta <b>{fechaAno(f.garantia)}</b></span><span className="cli-badge cli-badge--ok">Vigente</span></div>
          <div className="cli-gar__bar" aria-hidden="true"><i style={{ width: `${pct}%` }} /></div>
          <small>Quedan {quedan} días de garantía</small>
        </div>
        {e.tipo === "ups" && (
          <p className="cli-eq__b3"><SelloPropuesta id="B3" tamano="chico" /><span>Aviso de revisión de batería</span></p>
        )}
      </div>
      <div className="cli-eq__acts">
        <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={() => onFalla(e.serie)} aria-describedby={tit}>Reportar una falla</button>
        {instalable && (
          <Link className="cli-instalar" to="/propuestas/a1" aria-label="¿La instalamos? Ver la maqueta A1, instalación con fecha y precio">
            <span>¿La instalamos?</span><b aria-hidden="true">A1</b>
          </Link>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------- 5 · reportar una falla desde el equipo
function Falla({ eq, m, st, foto, onCambio, onFoto, onEquipos, onCrimson }: {
  eq: Equipo; m: Momentos; st: EstadoB1; foto: { url: string; nombre: string } | null;
  onCambio: (p: Partial<EstadoB1>) => void; onFoto: (f: File | null) => void; onEquipos: () => void; onCrimson: () => void;
}) {
  const f = fechasDe(eq, m);
  const listo = !!st.motivo && st.texto.trim().length >= 10;
  const [errFoto, setErrFoto] = useState<string | null>(null);
  const ok = useRef<HTMLHeadingElement>(null);
  const antes = useRef(st.enviado);
  useEffect(() => {
    if (st.enviado && !antes.current) ok.current?.focus();
    antes.current = st.enviado;
  }, [st.enviado]);

  const enviar = (e: FormEvent) => { e.preventDefault(); if (listo) onCambio({ enviado: true }); };
  const elegirFoto = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    e.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/")) { setErrFoto("Elegí una imagen (JPG, PNG o similar)."); return; }
    setErrFoto(null);
    onFoto(file);
  };

  return (
    <Pantalla superficie="portal" direccion={`app.crimsoncr.com/mis-equipos/${eq.serie.toLowerCase()}/reportar`}>
      <PortalCliente actual="soporte">
        <CabezaSeccion rotulo="Soporte" titulo="Reportar una falla" />
        <div className="cli-falla-eq">
          <FotoProducto id={eq.id} />
          <div>
            <b className="cli-falla-eq__n">{eq.nombre}</b>
            <p>Serie <span className="cli-mono">{eq.serie}</span> · Comprada el {fechaAno(f.compra)} · En garantía hasta {fechaAno(f.garantia)}</p>
          </div>
          <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={onEquipos}>Cambiar de equipo</button>
        </div>

        {!st.enviado ? (
          <form className="cli-form" onSubmit={enviar} noValidate>
            <fieldset className="cli-chips">
              <legend>¿Qué pasa?</legend>
              {MOTIVOS[eq.tipo].map((x) => (
                <label key={x} className="cli-chip">
                  <input type="radio" name="cli-motivo" value={x} checked={st.motivo === x} onChange={() => onCambio({ motivo: x })} />
                  <span>{x}</span>
                </label>
              ))}
            </fieldset>
            <label className="cli-field">
              <span className="cli-field__l">Contanos un poco más</span>
              <textarea className="cli-input cli-input--area" rows={3} maxLength={600} value={st.texto} onChange={(e) => onCambio({ texto: e.target.value })} aria-describedby="cli-falla-pista" />
            </label>
            <div className="cli-field">
              <span className="cli-field__l" id="cli-foto-l">Foto (opcional)</span>
              <div className="cli-foto-in">
                <label className="cli-file">
                  <input type="file" accept="image/*" onChange={elegirFoto} aria-labelledby="cli-foto-l cli-foto-btn" aria-describedby="cli-foto-nota" />
                  <span id="cli-foto-btn"><Ico d={IC.cam} size={16} />{foto ? "Cambiar foto" : "Agregar foto"}</span>
                </label>
                {foto && (
                  <figure className="cli-foto-prev">
                    <img src={foto.url} alt={`Foto adjunta: ${foto.nombre}`} width={120} height={90} />
                    <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={() => onFoto(null)}>Quitar foto</button>
                  </figure>
                )}
              </div>
              <small id="cli-foto-nota" className="cli-note">La foto no sale de tu navegador.</small>
              {errFoto && <small className="cli-err" role="alert">{errFoto}</small>}
            </div>
            <button type="submit" className="cli-btn cli-btn--crimson" disabled={!listo}>Enviar reporte</button>
            <small id="cli-falla-pista" className="cli-note">{listo ? "Llega a Crimson con el equipo, la compra y la garantía." : "Elegí qué pasa y escribí al menos una frase."}</small>
          </form>
        ) : (
          <div className="cli-ok" role="status">
            <span className="cli-ok__ico" aria-hidden="true"><Ico d={IC.check} size={20} /></span>
            <h4 ref={ok} tabIndex={-1} className="cli-ok__h">Recibimos tu reporte {TICKET}.</h4>
            <p>Te respondemos en horario hábil.</p>
            <div className="cli-row">
              <button type="button" className="cli-btn cli-btn--ink" onClick={onCrimson}>Ver cómo le llega a Crimson →</button>
              <button type="button" className="cli-btn cli-btn--ghost" onClick={() => onCambio({ enviado: false })}>Editar el reporte</button>
            </div>
          </div>
        )}
      </PortalCliente>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 6 · asi le llega a Crimson
function Crimson({ eq, m, st, foto }: { eq: Equipo; m: Momentos; st: EstadoB1; foto: { url: string; nombre: string } | null }) {
  const f = fechasDe(eq, m);
  const respuesta = sumarDias(m.ahora, 1);
  respuesta.setHours(10, 0, 0, 0);
  const asignar = () => notifyDemo({ title: "Asignar técnico", text: "En la plataforma real, aquí se asigna el técnico y el caso corre con su tiempo de respuesta. En la vista previa no se asigna nada." });
  return (
    <Pantalla superficie="admin" seccion="Soporte" estado="existente">
      <div className="cli cli-adm">
        <CabezaSeccion rotulo="12 · Soporte" titulo="Casos de soporte">
          <span className="cli-badge cli-badge--info">1 nuevo</span>
        </CabezaSeccion>
        <article className="cli-caso cli-momento" aria-labelledby="cli-caso-t">
          <div className="cli-caso__top">
            <span className="cli-badge cli-badge--info">Nuevo</span>
            <span className="cli-badge cli-badge--crimson">Desde el portal · equipo identificado</span>
          </div>
          <h4 id="cli-caso-t" className="cli-caso__h">Caso <span className="cli-mono">{TICKET}</span> · {st.motivo}</h4>
          <dl className="cli-kv">
            <div><dt>Cliente</dt><dd>{MARIA.nombre}</dd></div>
            <div><dt>Equipo</dt><dd>{eq.nombre}</dd></div>
            <div><dt>Serie</dt><dd className="cli-mono">{eq.serie}</dd></div>
            <div><dt>Compra</dt><dd><span className="cli-mono">{eq.pedido}</span> ({haceTanto(f.compra)})</dd></div>
            <div><dt>Garantía</dt><dd>Vigente hasta {fechaAno(f.garantia)}</dd></div>
            <div><dt>Fotos</dt><dd>{foto ? "1 foto" : "Sin foto (podés agregar una en el paso anterior)"}</dd></div>
            <div><dt>Prioridad</dt><dd>Normal</dd></div>
            <div><dt>Tipo y canal</dt><dd>Garantía · portal del cliente</dd></div>
            <div><dt>Primera respuesta</dt><dd>Antes de {fecha(respuesta, "dia")}, {hora(respuesta)} (plazo de ejemplo)</dd></div>
            <div><dt>Responsable</dt><dd>Sin asignar</dd></div>
          </dl>
          <blockquote className="cli-caso__txt">«{st.texto.trim() || TEXTO_FALLA[eq.tipo]}»</blockquote>
          {foto && <img className="cli-caso__foto" src={foto.url} alt={`Foto del reporte: ${foto.nombre}`} width={160} height={120} />}
          <div className="cli-row"><button type="button" className="cli-btn cli-btn--crimson cli-btn--sm" onClick={asignar}>Asignar técnico</button></div>
        </article>
        <ul className="cli-sellos">
          <li><SelloPropuesta estado="existente" tamano="chico" /><span>Casos de soporte con fotos y equipos del cliente</span></li>
          <li><SelloPropuesta id="B1" tamano="chico" /><span>Nace del equipo, con la compra y la garantía ya identificadas</span></li>
        </ul>
      </div>
    </Pantalla>
  );
}
