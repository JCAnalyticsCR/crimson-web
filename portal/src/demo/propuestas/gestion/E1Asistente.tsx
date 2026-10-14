/* E1 · Asistente tecnico (PROPUESTA, no incluida aun). GUION SIMULADO: en la vista previa no hay ningun asistente
   conectado ni llamadas a IA; las respuestas estan escritas de antemano y usan productos y precios reales del
   catalogo de la demo (el kit sale de POST /kit, que responde mockApi en el navegador). El guion solo avanza con
   toques. "Pasar a WhatsApp" solo muestra el aviso de demostracion. */
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { notifyDemo } from "../../mockApi";
import { MarcoMaqueta, Pantalla, SelloPropuesta, colones, usePaso, useProducto } from "./comun";
import { E1_KPI, PRODUCTOS } from "./datos";
import { IC, Ico, calma } from "./ui";
import "./gestion.css";

const PASOS = [
  { id: "chat", titulo: "La conversación", vista: "cliente" },
  { id: "crimson", titulo: "Así le llega a Crimson", vista: "crimson" },
] as const;
type Pregunta = "kit" | "disco" | "noche" | "celular" | "instalan";
const PREGUNTAS: [Pregunta, string][] = [
  ["kit", "¿Qué necesito para 4 cámaras en mi casa?"],
  ["disco", "¿Cuántos días graba un disco de 2 TB con 4 cámaras?"],
  ["noche", "¿Qué cámara me sirve para ver la entrada de noche?"],
  ["celular", "¿Puedo verlas desde el celular?"],
  ["instalan", "¿Ustedes la instalan?"],
];
type LineaKit = { q: number; nombre: string; path?: string; sub?: number };
type Msg =
  | { de: "cliente"; texto: string }
  | { de: "asistente"; tipo: "saludo" | "celular" | "libre" | "disco" | "noche" | "instalan" | "traspaso" }
  | { de: "asistente"; tipo: "kit"; lineas: LineaKit[]; faltan: string[]; total: number };
const KIT_RESPALDO = (): { lineas: LineaKit[]; faltan: string[]; total: number } => {
  const l: [number, number][] = [[53, 2], [118, 2], [177, 1], [11, 1], [92, 1], [318, 2]];
  const lineas = l.map(([id, q]) => ({ q, nombre: PRODUCTOS[id].nombre, path: PRODUCTOS[id].path, sub: Math.round(PRODUCTOS[id].conIva * q * 100) / 100 }));
  return { lineas, faltan: ["8 conectores RJ45, por cotizar"], total: 427313 };
};
const espera = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function E1Asistente() {
  const [i] = usePaso(PASOS.map((p) => p.id));
  return (
    <MarcoMaqueta id="E1" etapa={2} titulo="Asistente técnico" pasos={PASOS}
      bajada="Responde dudas con el catálogo real a cualquier hora y pasa la conversación a una persona de Crimson cuando hace falta."
      hoy="Las dudas llegan por WhatsApp y se contestan a mano, una por una."
      conPropuesta="Un asistente en la tienda contesta lo frecuente con productos y precios reales y entrega la conversación resumida a una persona."
      aparte={<>
        <p><b>Depende de:</b> servicio de asistente con costo por uso, a definir.</p>
        <p>Solo recomienda productos del catálogo con el precio y la disponibilidad del momento; lo que no sabe, lo pasa a una persona.</p>
        <p><Link to="/propuestas" className="ges-link">Ver todas las propuestas →</Link></p>
      </>}>
      {i === 0 ? <Chat /> : <LadoCrimson />}
    </MarcoMaqueta>
  );
}

// ---------------------------------------------------------------- la conversacion
function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([{ de: "asistente", tipo: "saludo" }]);
  const [ocupado, setOcupado] = useState(false);
  const [texto, setTexto] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const vivo = useRef(true);
  const p53 = useProducto(53);
  useEffect(() => { vivo.current = true; return () => { vivo.current = false; }; }, []);
  useEffect(() => { const el = log.current; if (el) el.scrollTop = el.scrollHeight; }, [msgs, ocupado]);

  const responder = async (preguntaTxt: string, armar: () => Promise<Msg[]>) => {
    if (ocupado) return;
    setMsgs((m) => [...m, { de: "cliente", texto: preguntaTxt }]);
    setOcupado(true);
    const t0 = performance.now();
    const resp = await armar();
    const falta = (calma() ? 300 : 600) - (performance.now() - t0);
    if (falta > 0) await espera(falta);
    if (!vivo.current) return;
    setMsgs((m) => [...m, ...resp]);
    setOcupado(false);
  };
  const preguntar = (q: Pregunta, t: string) => responder(t, async () => {
    if (q === "kit") {
      try {
        const r = await fetch("/api/public/store/crimson/kit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ place: "casa", indoor: 2, outdoor: 2, days: 15, remote: true }) });
        if (!r.ok) throw new Error("kit");
        const j = await r.json() as { lines: { quantity: number; subtotal: number; product: { name: string; path: string } }[]; missing: { name: string; quantity: number }[]; total: number };
        return [{ de: "asistente", tipo: "kit", lineas: j.lines.map((l) => ({ q: l.quantity, nombre: l.product.name, path: l.product.path, sub: l.subtotal })), faltan: j.missing.map((x) => `${x.quantity} ${x.name.charAt(0).toLowerCase()}${x.name.slice(1)}, por cotizar`), total: Math.round(j.total) }];
      } catch {
        return [{ de: "asistente", tipo: "kit", ...KIT_RESPALDO() }];
      }
    }
    if (q === "instalan") return [{ de: "asistente", tipo: "instalan" }, { de: "asistente", tipo: "traspaso" }];
    return [{ de: "asistente", tipo: q }];
  });
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    const t = texto.trim();
    if (!t || ocupado) return;
    setTexto("");
    responder(t, async () => [{ de: "asistente", tipo: "libre" }]);
  };

  return (
    <Pantalla superficie="tienda" direccion="tienda.crimsoncr.com/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53">
      <div className="ges ges-e1"><div className="ges-e1__in">
        <div className="ges-e1__ficha" aria-hidden="true" inert>
          <div className="ges-e1__foto">{p53?.image ? <img src={p53.image} alt="" width={420} height={420} loading="lazy" decoding="async" /> : <Ico d={IC.cam} size={48} />}</div>
          <div className="ges-e1__info">
            <span className="meta">Hikvision · DS2CD2043G2LI2U</span>
            <b>Camara IP Hikvision 4MP Acusense Bullet IP67</b>
            <span className="ges-e1__precio">{colones(62832)} <small>IVA incluido</small></span>
            <span className="ges-e1__disp">Bajo pedido · 3 a 5 días hábiles</span>
            <span className="ges-e1__add">Agregar al carrito</span>
          </div>
        </div>

        <section className="ges-chat" aria-labelledby="ges-chat-t">
          <div className="ges-chat__head">
            <span className="ges-chat__av" aria-hidden="true"><Ico d={IC.chat} size={18} /></span>
            <span><b id="ges-chat-t">Asistente de la tienda</b><small>responde con el catálogo de Crimson</small></span>
            <SelloPropuesta id="E1" tamano="chico" />
          </div>
          <p className="ges-chat__banda"><b>Guion de demostración:</b> en la vista previa no hay ningún asistente conectado; las respuestas están escritas de antemano.</p>
          <div className="ges-chat__log" ref={log} role="log" aria-live="polite" aria-label="Conversación" tabIndex={0}>
            {msgs.map((m, n) => <Burbuja key={n} m={m} />)}
            {ocupado && (calma()
              ? <div className="ges-msg" data-de="asistente"><span className="ges-typing-txt">Escribiendo…</span></div>
              : <div className="ges-msg" data-de="asistente"><span className="ges-typing" role="status" aria-label="Escribiendo…"><i /><i /><i /></span></div>)}
          </div>
          <div className="ges-chat__sug" role="group" aria-label="Preguntas de ejemplo">
            {PREGUNTAS.map(([q, t]) => <button key={q} type="button" className="ges-sug" disabled={ocupado} onClick={() => preguntar(q, t)}>{t}</button>)}
          </div>
          <form className="ges-chat__form" onSubmit={enviar}>
            <label htmlFor="ges-chat-in" className="ges-sr">Escribí tu pregunta</label>
            <input id="ges-chat-in" className="input" value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Escribí tu pregunta" autoComplete="off" maxLength={300} />
            <button type="submit" className="btn btn--crimson" disabled={ocupado || !texto.trim()}>Enviar</button>
          </form>
        </section>
      </div></div>
    </Pantalla>
  );
}

function TarjetaProducto({ id, nota }: { id: number; nota: string }) {
  const p = PRODUCTOS[id];
  const live = useProducto(id);
  return (
    <Link to={p.path} className="ges-ptarj">
      <span className="ges-ptarj__img">{live?.image ? <img src={live.image} alt="" width={64} height={64} loading="lazy" decoding="async" /> : <Ico d={IC.cam} size={24} />}</span>
      <span className="ges-ptarj__t"><b>{p.nombre}</b><span className="money">{colones(Math.round(p.conIva))}</span><small>{nota}</small><small>{p.disponible ? "Disponible" : "Bajo pedido"} · Ver ficha →</small></span>
    </Link>
  );
}

function Burbuja({ m }: { m: Msg }) {
  if (m.de === "cliente") return <div className="ges-msg" data-de="cliente"><span className="ges-sr">Vos: </span>{m.texto}</div>;
  const quien = <span className="ges-sr">Asistente: </span>;
  switch (m.tipo) {
    case "saludo":
      return <div className="ges-msg" data-de="asistente">{quien}¡Hola! Te ayudo a elegir equipo con productos y precios del catálogo. Si preferís, te paso con una persona.</div>;
    case "kit":
      return (
        <div className="ges-msg" data-de="asistente">{quien}
          <p>Para una casa con 2 cámaras adentro y 2 afuera y 15 días de grabación, el kit sugerido es:</p>
          <ul className="ges-msg__kit">
            {m.lineas.map((l, i) => <li key={i}><span className="mono">{l.q} ×</span> {l.path ? <Link to={l.path}>{l.nombre}</Link> : l.nombre}</li>)}
            {m.faltan.map((f) => <li key={f} className="muted"><span className="mono">·</span> {f}</li>)}
          </ul>
          <p>Total de referencia <b>{colones(m.total)}</b> con IVA. Un técnico lo valida antes de cobrar.</p>
          <Link className="ges-msg__a" to="/arma-tu-sistema?place=casa&indoor=2&outdoor=2&days=15&remote=true">Ver el kit en Arma tu sistema →</Link>
        </div>
      );
    case "disco":
      return (
        <div className="ges-msg" data-de="asistente">{quien}
          <p>Unos 21 días en grabación continua, con la misma regla de «Arma tu sistema» (unos 21 GB por cámara al día y 10 % de reserva). Con grabación por movimiento rinde 2 a 3 veces más.</p>
          <TarjetaProducto id={11} nota="con 4 cámaras, unos 21 días" />
          <TarjetaProducto id={10} nota="con 4 cámaras, unos 43 días" />
        </div>
      );
    case "noche":
      return (
        <div className="ges-msg" data-de="asistente">{quien}
          <p>Para ver la entrada de noche, estas dos del catálogo:</p>
          <TarjetaProducto id={215} nota="ColorVu: imagen a color de noche, según el fabricante" />
          <TarjetaProducto id={53} nota="resistente al agua (IP67)" />
        </div>
      );
    case "celular":
      return <div className="ges-msg" data-de="asistente">{quien}Sí. Los grabadores y cámaras IP que vendemos se ven desde la app del fabricante (por ejemplo Hik-Connect). Solo necesitás internet en el sitio.</div>;
    case "instalan":
      return (
        <div className="ges-msg" data-de="asistente">{quien}
          <p>Sí, Crimson instala. Hoy podés pedirlo con «¿Lo instalamos nosotros?»; con la propuesta A1 verías el precio estimado y la fecha al instante.</p>
          <span className="ges-msg__links"><Link to="/asesoria">Asesoría</Link><Link to="/propuestas/a1">Ver la maqueta A1</Link></span>
        </div>
      );
    case "traspaso":
      return (
        <div className="ges-msg ges-msg--traspaso" data-de="asistente">{quien}
          <p>¿Te paso con una persona de Crimson por WhatsApp con este resumen?</p>
          <button type="button" className="btn btn--crimson ges-wa" onClick={() => notifyDemo({ title: "WhatsApp", text: "En la tienda real se abre WhatsApp con este resumen listo para una persona de Crimson. Desde la vista previa no se envía nada." })}>
            <Ico d={IC.wa} />Pasar a WhatsApp con un técnico
          </button>
        </div>
      );
    default:
      return <div className="ges-msg" data-de="asistente">{quien}En esta vista previa las respuestas están escritas de antemano. Elegí una de las preguntas de ejemplo o pasá con una persona.</div>;
  }
}

// ---------------------------------------------------------------- lado de Crimson
function LadoCrimson() {
  const recientes = [
    { cuando: "hace 12 min", q: "¿Qué necesito para 4 cámaras en mi casa?", r: "Pasó a una persona", tono: "warn" },
    { cuando: "hace 1 h", q: "¿El grabador de 8 canales trae disco?", r: "Resuelta por el asistente", tono: "ok" },
    { cuando: "ayer", q: "¿Tienen cámaras con audio para un local?", r: "Sin respuesta en el catálogo: pasó a una persona", tono: "muted" },
  ];
  return (
    <Pantalla superficie="admin" seccion="Inicio">
      <div className="ges ges-adm ges-e1c">
        <div className="page-head"><div><div className="meta">01 · Inicio</div><h3 className="h1">Asistente de la tienda</h3></div></div>
        <section className="card ges-card ges-entregada" aria-labelledby="ges-ent-t">
          <div className="card__head"><h4 id="ges-ent-t" className="ges-card-t">Conversación entregada</h4><SelloPropuesta id="E1" tamano="chico" /></div>
          <div className="card__body">
            <dl className="ges-det__dl">
              <dt>Desde</dt><dd>la ficha de Camara IP Hikvision 4MP Acusense Bullet IP67</dd>
              <dt>Preguntó por</dt><dd>kit de 4 cámaras</dd>
              <dt>Le interesa</dt><dd>instalación</dd>
              <dt>Cuándo</dt><dd>hace 12 min</dd>
            </dl>
            <p className="ges-resumen"><span className="meta">Resumen para la persona de Crimson</span>Casa, 2 cámaras adentro y 2 afuera, 15 días de grabación. Se le mostró el kit sugerido de {colones(427313)} con IVA. Pregunta si Crimson instala.</p>
            <button type="button" className="btn btn--soft btn--sm" onClick={() => notifyDemo({ title: "WhatsApp", text: "En la tienda real, la persona de Crimson sigue esta conversación por WhatsApp con el resumen a la vista. Desde la vista previa no se envía nada." })}><Ico d={IC.wa} />Responder por WhatsApp</button>
          </div>
        </section>
        <p className="ges-banda" role="note"><Ico d={IC.alert} /><b>Datos ficticios: no son conversaciones reales.</b></p>
        <ul className="ges-kpis ges-kpis--4" aria-label="Asistente, últimos 7 días">
          {E1_KPI.map((k) => <li key={k.label} className="ges-kpi"><span className="ges-kpi__l">{k.label}</span><b className="ges-kpi__v">{k.valor}</b><span className="ges-kpi__s">7 días</span></li>)}
        </ul>
        <section className="card ges-card" aria-labelledby="ges-rec-t">
          <div className="card__head"><h4 id="ges-rec-t" className="ges-card-t">Conversaciones recientes</h4></div>
          <ul className="ges-recientes">
            {recientes.map((r) => <li key={r.q}><span className="muted ges-nowrap">{r.cuando}</span><b>{r.q}</b><span className={`badge badge--${r.tono}`}>{r.r}</span></li>)}
          </ul>
        </section>
        <section className="ges-reglas" aria-labelledby="ges-reg-t">
          <h4 id="ges-reg-t">Reglas del asistente</h4>
          <ul>
            <li>Solo recomienda productos del catálogo con su precio y disponibilidad del momento.</li>
            <li>No promete fechas ni descuentos.</li>
            <li>Ante dudas o proyectos, pasa a una persona.</li>
          </ul>
        </section>
      </div>
    </Pantalla>
  );
}
