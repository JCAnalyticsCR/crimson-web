/* "Armá tu sistema": 6 pasos -> kit con productos reales de la tienda. Las cantidades salen de las mismas
   reglas del levantamiento tecnico (API /kit). Honesto: "kit sugerido, un tecnico lo valida". El resultado se
   recalcula al cambiar una respuesta (sin boton extra) y se puede llevar al carrito o mandar como asesoria.
   Cada pieza muestra sus datos clave y se puede "Cambiar" por una alternativa que tambien calza: la eleccion viaja al
   API como choices (el servidor la valida y recalcula cantidades) y queda en la direccion del navegador (?nvr=123).
   El enlace para compartir (WhatsApp, asesoria) lleva TODAS las piezas: las sugerencias automaticas rotan cada dia
   entre equivalentes y asi el enlace arma el mismo kit otro dia.
   Los detalles (calidad, color de noche, audio, deteccion, grabacion por movimiento, distancia, crecer, UPS) cambian
   que productos se eligen; el API explica cada decision en `why` ("Por que este kit"). */
import { useEffect, useMemo, useState } from "react";
import { Link, useHref, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { AvailBadge, Crumbs, IC, Ico, KitPhotos, PriceTag, Viewfinder, cannotBuy, money, useHead, usePath, useStore, waLink, type KitPhoto, type VProd } from "./storeKit";

/* note: lo que el cambio arrastra en el resto del kit (un grabador sin PoE suma el switch); ya va contado en diff */
type Alt = { label: string; auto?: boolean; quantity: number; subtotal: string | number; diff: string | number; note?: string | null; specs?: string[]; product: VProd };
/* source: auto (sugerencia del configurador) | pin (fijado en el kit de la tienda) | choice (viene elegido) | extra.
   pinned: el kit de la tienda fija esa pieza. suggested: es lo que el kit lleva sin elegir nada (una eleccion igual a
   la sugerencia de hoy, como las piezas de un enlace compartido, no es "un cambio tuyo").
   Lo opcional lo manda el API actual; el configurador igual pinta una respuesta que no lo traiga (la demo estatica o,
   durante un despliegue, el portal nuevo contra el API anterior) en vez de quedar en blanco. */
type Line = { key?: string; role: string; reason: string; source?: string; pinned?: boolean; suggested?: boolean; quantity: number; unit_price?: string | number; subtotal: string | number; specs?: string[]; product: VProd; alternatives?: Alt[] };
type Summary = {
  indoor: number; outdoor: number; days: number; need_tb: string | number; installed_tb: string | number | null; poe: "grabador" | "switch" | null; switch_ports: number | null; remote: boolean;
  /* lo que sigue lo manda el API actual (una respuesta anterior no lo trae) */
  quality?: string; night?: string; audio?: boolean; smart?: boolean; record?: string; meters?: number;
  channels?: number | null; free_channels?: number | null; brand?: string | null; ups?: { va: number | null; w: number | null; load_w: number } | null;
};
type Why = { tone: "ok" | "warn"; text: string };
type Kit = {
  answers: Answers; place_label: string; cameras: number; storage_tb: string | number; lines: Line[]; missing: { name: string; quantity: number; reason: string; role: string }[];
  notes: string[]; total: string | number; currency: string; photos?: KitPhoto[]; summary?: Summary; choices?: Choices; kit?: { id: string; title: string } | null; why?: Why[];
};
type Answers = {
  place: string; indoor: number; outdoor: number; days: number; remote: boolean;
  quality: string; night: string; audio: boolean; smart: boolean; record: string; distance: string; growth: boolean; ups: boolean;
};
type Choices = Record<string, number>;
/* llaves de las piezas que se pueden cambiar (las mismas del API: services/store_showcase.KIT_ROLES) */
const ROLE_KEYS = ["cam_out", "cam_in", "nvr", "disk", "switch", "ups", "rj45", "cable", "box"];

const PLACES: [string, string, string, string][] = [
  ["casa", "Casa", "Entradas, patio y cochera", IC.home],
  ["negocio", "Negocio u oficina", "Caja, mostrador y bodega", IC.shop],
  ["condominio", "Condominio", "Accesos, parqueo y perímetro", IC.building],
  ["bodega", "Bodega o planta", "Perímetro, andenes y patio", IC.factory],
];
const DAYS = [7, 15, 30, 60];
/* [valor, rotulo, detalle] de cada detalle (los mismos valores que valida el API) */
const QUALITY: [string, string, string][] = [
  ["2mp", "Buena", "2 MP · Full HD"],
  ["4mp", "Muy buena", "4 MP · 2K, la más pedida"],
  ["8mp", "Máxima", "8 MP · 4K, para leer placas y rostros a más distancia"],
];
const NIGHT: [string, string, string][] = [
  ["ir", "Blanco y negro", "Infrarrojo: ve en total oscuridad"],
  ["color", "A color", "Ves la ropa y el color de los carros"],
];
const RECORD: [string, string, string][] = [
  ["continuo", "Todo el día", "Graba las 24 horas"],
  ["movimiento", "Solo con movimiento", "El disco rinde 2 a 3 veces más"],
];
const DISTANCE: [string, string, string][] = [
  ["corta", "Cerca", "Hasta 15 m"],
  ["media", "Lo típico", "Según el lugar"],
  ["larga", "Lejos", "Hasta 70 m"],
];
const pick = (sp: URLSearchParams, k: string, opts: [string, string, string][], d: string) => (opts.some(([v]) => v === sp.get(k)) ? sp.get(k)! : d);
export const KIT_STORAGE = "crimson-shop:kit"; // la asesoria lo lee para adjuntar el kit

const fromQuery = (sp: URLSearchParams): Answers => ({
  place: PLACES.some(([k]) => k === sp.get("place")) ? sp.get("place")! : "casa",
  indoor: clampN(Number(sp.get("indoor") ?? 2)),
  outdoor: clampN(Number(sp.get("outdoor") ?? 2)),
  days: DAYS.includes(Number(sp.get("days"))) ? Number(sp.get("days")) : 15,
  remote: sp.get("remote") !== "false",
  quality: pick(sp, "quality", QUALITY, "4mp"),
  night: pick(sp, "night", NIGHT, "ir"),
  audio: sp.get("audio") === "true",
  smart: sp.get("smart") === "true",
  record: pick(sp, "record", RECORD, "continuo"),
  distance: pick(sp, "distance", DISTANCE, "media"),
  growth: sp.get("growth") === "true",
  ups: sp.get("ups") === "true",
});
function clampN(n: number) { return Math.max(0, Math.min(32, Number.isFinite(n) ? Math.round(n) : 0)); }
const choicesFrom = (sp: URLSearchParams): Choices => Object.fromEntries(ROLE_KEYS.map((k) => [k, Number(sp.get(k))] as const).filter(([, v]) => Number.isInteger(v) && v > 0));
/* lo que este configurador escribe en la direccion (lo demas, p. ej. una campaña, se conserva) */
const OWN_KEYS = ["place", "indoor", "outdoor", "days", "remote", "quality", "night", "audio", "smart", "record", "distance", "growth", "ups", "kit", ...ROLE_KEYS];
/* La consulta que reproduce el kit en pantalla: respuestas, el kit de la tienda (si aplica) y las piezas. Por defecto
   solo las que valieron como eleccion (la direccion del navegador); con all, TODAS las piezas (el enlace para compartir). */
const kitQuery = (k: Kit, all = false) => {
  const q = new URLSearchParams(Object.entries(k.answers).map(([a, v]) => [a, String(v)]));
  if (k.kit?.id) q.set("kit", k.kit.id);
  if (all) k.lines.forEach((l) => { if (l.key && ROLE_KEYS.includes(l.key)) q.set(l.key, String(l.product.id)); });
  else Object.entries(k.choices || {}).forEach(([a, v]) => q.set(a, String(v)));
  return q;
};
/* llave estable de la linea (una respuesta sin `key` cae al rol + producto) */
const lineKey = (l: Line) => l.key ?? `${l.role}-${l.product.id}`;
/* lo cambiaste vos: una eleccion que no es lo que el kit sugiere hoy para esa pieza */
const isMine = (l: Line) => l.source === "choice" && !l.suggested;
const tb = (v: string | number | null | undefined) => { const n = Number(v); return Number.isInteger(n) ? String(n) : n.toLocaleString("es-CR", { maximumFractionDigits: 1 }); };

export default function KitBuilder() {
  const ctx = useStore();
  const to = usePath();
  const nav = useNavigate();
  const loc = useLocation();
  const [sp, setSp] = useSearchParams();
  const [ans, setAns] = useState<Answers>(() => fromQuery(sp));
  const [choices, setChoices] = useState<Choices>(() => choicesFrom(sp)); // lo que cambiaste (sobrevive a otras respuestas si sigue calzando)
  const [kitId, setKitId] = useState<string | null>(() => (/^[a-z0-9-]{1,40}$/.test(sp.get("kit") || "") ? sp.get("kit") : null)); // kit de la tarjeta
  const [open, setOpen] = useState<string | null>(null); // pieza con las alternativas abiertas
  const [kit, setKit] = useState<Kit | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0); // vuelve a pedir el kit aunque nada haya cambiado (tras un fallo)
  const [added, setAdded] = useState(false);
  const [addedN, setAddedN] = useState(0);
  const [inCart, setInCart] = useState(false); // ya agrego algo: "Ir al carrito" queda a mano (pedido 21/10)
  const [alarm, setAlarm] = useState(false); // tambien le interesa una alarma: va en la asesoria y en el WhatsApp (se cotiza aparte)
  const total = ans.indoor + ans.outdoor;

  useHead({ title: `Armá tu sistema de cámaras · ${ctx.store.name}`, description: "En 6 pasos te cotizamos al instante un kit de videovigilancia con productos reales, validado por un técnico.", path: "/arma-tu-sistema" });

  useEffect(() => {
    if (total === 0) { setKit(null); setBusy(false); setErr("Elegí al menos una cámara."); return; }
    setErr(null);
    setBusy(true);
    // La respuesta solo cuenta si esta peticion sigue vigente: al cambiar otra respuesta o salir del configurador, una
    // que llega tarde no pisa el kit nuevo, no apaga el "calculando" de la que sigue en camino ni toca la direccion.
    let vivo = true;
    const t = window.setTimeout(() => {
      // `choices` solo viaja si hay alguna (un API anterior, durante un despliegue, no conoce el campo)
      ctx.api<Kit>("/kit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...ans, ...(Object.keys(choices).length ? { choices } : {}), ...(kitId ? { kit: kitId } : {}) }) })
        .then((k) => { if (!vivo) return; setKit(k); setErr(null); })
        .catch((e) => { if (vivo) setErr(e instanceof TypeError ? "No pudimos actualizar tu kit. Revisá tu conexión e intentá de nuevo." : e.message); })
        .finally(() => { if (vivo) setBusy(false); });
    }, 280);
    return () => { vivo = false; window.clearTimeout(t); };
  }, [JSON.stringify(ans), JSON.stringify(choices), kitId, retry]); // eslint-disable-line react-hooks/exhaustive-deps

  // La direccion del navegador sigue al kit EN PANTALLA: respuestas, el kit de la tienda (si aplica) y solo las
  // elecciones que valieron. Corre con el configurador montado (una respuesta tardia ya no navega) y tambien repone la
  // direccion si un enlace del menu ("Armá tu sistema") la dejo sin parametros con el kit a la vista.
  const spKey = sp.toString();
  useEffect(() => {
    if (!kit) return;
    const q = new URLSearchParams(sp);
    OWN_KEYS.forEach((k) => q.delete(k));
    kitQuery(kit).forEach((v, k) => q.set(k, v));
    if (q.toString() !== spKey) setSp(q, { replace: true });
  }, [kit, spKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Enlace para compartir (WhatsApp y asesoria): sale del kit en pantalla, no de la barra de direcciones, y lleva
  // TODAS sus piezas para que otro dia arme los mismos productos (las automaticas rotan a diario entre equivalentes).
  const shareHref = useHref({ pathname: loc.pathname, search: kit ? `?${kitQuery(kit, true)}` : "" });
  const shareUrl = typeof window !== "undefined" ? new URL(shareHref, window.location.href).toString() : shareHref;

  // otra respuesta = otro kit: deja de ser "el de la tarjeta" (si las respuestas vuelven a ser las suyas, el API lo reconoce)
  const set = <K extends keyof Answers>(k: K, v: Answers[K]) => { setKitId(null); setOpen(null); setAns((a) => ({ ...a, [k]: v })); };
  const choose = (key: string, id: number | null) => {
    setChoices((c) => { const n = { ...c }; if (id === null) delete n[key]; else n[key] = id; return n; });
    setRetry((n) => n + 1); // tocar otra vez la misma opcion despues de un fallo vuelve a pedir el kit
    setOpen(null);
    // el panel de alternativas (y "Volver a la sugerida") desaparecen: el foco queda en "Cambiar" de esa pieza
    document.getElementById(`swap-${key}`)?.focus({ preventScroll: true });
  };
  const priced = useMemo(() => (kit?.lines || []).filter((l) => !l.product.price_on_request && Number(l.product.display_price) > 0), [kit]);
  // desglose del panel: articulos con precio, IVA contenido (por la tarifa de cada producto) y lo que queda por cotizar
  const sums = useMemo(() => {
    const units = priced.reduce((n, l) => n + l.quantity, 0);
    const iva = priced.reduce((t, l) => { const s = Number(l.subtotal) || 0; const r = Number(l.product.tax_rate) || 0; return t + (s - s / (1 + r / 100)); }, 0);
    const pending = (kit?.lines.length || 0) - priced.length + (kit?.missing.length || 0);
    return { units, iva, pending };
  }, [priced, kit]);
  const placeName = PLACES.find(([k]) => k === ans.place)?.[1] || "";
  const label = (opts: [string, string, string][], v: string) => opts.find(([k]) => k === v)?.[1] || "";
  const extras = [ans.ups && "UPS", ans.growth && "Crecer"].filter(Boolean).join(" · ");
  const steps: [string, string, string][] = [
    ["q1", "Lugar", placeName],
    ["q2", "Cámaras", `${total} en total`],
    ["q3", "Imagen", `${label(QUALITY, ans.quality)}${ans.night === "color" ? " · color" : ""}`],
    ["q4", "Grabación", `${ans.days} días${ans.record === "movimiento" ? " · mov." : ""}`],
    ["q5", "Celular", ans.remote ? "Sí" : "No"],
    ["q6", "Extras", extras || "Ninguno"],
  ];
  /* lo que el cliente pidio, en palabras (WhatsApp y asesoria) */
  const detail = (k: Kit) => {
    const a = k.answers;
    const parts = [
      a.quality && `calidad ${label(QUALITY, a.quality).toLowerCase()} (${QUALITY.find(([v]) => v === a.quality)?.[2].split(" · ")[0]})`,
      a.night === "color" && "color de noche",
      a.audio && "con audio",
      a.smart && "que detecte personas y vehículos",
      a.record === "movimiento" && "grabando solo con movimiento",
      a.distance && a.distance !== "media" && `cámaras ${label(DISTANCE, a.distance).toLowerCase()} del grabador`,
      a.growth && "espacio para agregar cámaras",
      a.ups && "con UPS",
    ].filter(Boolean);
    return parts.length ? parts.join(", ") : "";
  };
  const kitText = kit ? [
    `Hola ${ctx.store.name}, armé este kit en la tienda (${kit.place_label}, ${kit.cameras} cámaras, ${kit.answers.days} días de grabación${detail(kit) ? `; ${detail(kit)}` : ""}):`,
    ...kit.lines.map((l) => `• ${l.quantity} × ${l.product.name} (${l.product.code})`),
    ...kit.missing.map((m) => `• ${m.quantity} × ${m.name} (por cotizar)`),
    `Total de referencia: ${money(kit.total, kit.currency)} IVA incluido.`,
    ...(alarm ? ["También me interesa cotizar una alarma (sensores, sirena y panel)."] : []),
    `Mi kit: ${shareUrl}`,
  ].join("\n") : "";

  // mientras se recalcula, lo que hay en pantalla es el kit ANTERIOR: nada se lleva al carrito ni se envia hasta que llegue
  const addAll = () => {
    if (!kit || busy) return;
    // lo que no se puede comprar en linea (sin existencia ni proveedor vigente) no entra al carrito: se consulta
    const ok = priced.filter((l) => !(ctx.isShop && cannotBuy(l.product.availability)));
    ok.forEach((l) => ctx.addToCart(l.product, l.quantity));
    setAddedN(ok.length); // O2-09: el boton dice cuantos productos entraron de verdad
    setAdded(true);
    setInCart(true);
    window.setTimeout(() => setAdded(false), 2200);
  };
  const toAdvice = () => {
    if (!kit || busy) return;
    try {
      sessionStorage.setItem(KIT_STORAGE, JSON.stringify({
        items: kit.lines.map((l) => ({ product_id: l.product.id, quantity: l.quantity, name: l.product.name })),
        text: `Kit sugerido desde "Armá tu sistema"${kit.kit ? ` (kit «${kit.kit.title}»)` : ""}: ${kit.place_label}, ${kit.answers.indoor} cámara(s) adentro y ${kit.answers.outdoor} afuera, ${kit.answers.days} días de grabación${kit.answers.remote ? ", ver desde el celular" : ""}${detail(kit) ? `, ${detail(kit)}` : ""}${alarm ? ". También le interesa una alarma (sensores, sirena y panel)" : ""}.${kit.lines.some(isMine) ? ` Productos que el cliente cambió: ${kit.lines.filter(isMine).map((l) => l.product.name).join(", ")}.` : ""} Enlace del kit: ${shareUrl}`,
      }));
    } catch { /* sin almacenamiento: la asesoria igual se abre */ }
    nav(to("/asesoria?origen=arma-tu-sistema&solucion=cctv&instalar=1"));
  };

  return (
    <div className="sh-wrap vt-kitb">
      <Crumbs items={[{ name: "Tienda", path: "/" }, { name: "Armá tu sistema" }]} />
      <header className="vt-cathead">
        <span className="sh-eyebrow">Configurador · 6 pasos · 2 minutos</span>
        <h1>Armá tu sistema de cámaras en 6 pasos</h1>
        <p>Respondé 6 pasos rápidos y te cotizamos al instante un kit con equipo real de la tienda. Es una sugerencia: antes de instalar, un técnico de Crimson visita tu sitio y confirma que todo calce.</p>
      </header>
      <div className="vt-kitb__grid">
        <form className="vt-q" onSubmit={(e) => e.preventDefault()} aria-label="Preguntas del configurador">
          {/* progreso: cada paso ya tiene respuesta (valores sugeridos); el resumen dice que se eligio y lleva a la pregunta */}
          <ol className="vt-steps" aria-label="Tus respuestas">
            {steps.map(([id, l, v], i) => (
              <li key={id}><a href={`#${id}`}><i aria-hidden="true">{total === 0 && i === 1 ? String(i + 1).padStart(2, "0") : <Ico d={IC.check} size={13} />}</i><span><small>{l}</small><b>{v}</b></span></a></li>
            ))}
          </ol>
          <fieldset className="vt-q__step" id="q1">
            <legend><i>01</i>¿Qué querés proteger?</legend>
            <p className="vt-q__hint">Así sabemos qué tipo de equipo conviene y qué zonas suelen cubrirse en un lugar como el tuyo.</p>
            <div className="vt-q__places">
              {PLACES.map(([k, l, s, ic]) => (
                <label key={k} className="vt-opt">
                  <input type="radio" name="place" checked={ans.place === k} onChange={() => set("place", k)} />
                  <span><Ico d={ic} size={22} /><b>{l}</b><small>{s}</small></span>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="vt-q__step" id="q2">
            <legend><i>02</i>¿Cuántas cámaras y dónde?</legend>
            <p className="vt-q__hint">Una por acceso o área que quieras ver. Afuera van cámaras tipo bullet resistentes al agua; adentro, domos discretos.</p>
            <div className="vt-q__counts">
              <Stepper label="Adentro" value={ans.indoor} onChange={(n) => set("indoor", n)} />
              <Stepper label="Afuera" value={ans.outdoor} onChange={(n) => set("outdoor", n)} />
            </div>
            <p className="vt-q__sub">¿Qué tan lejos del grabador queda la cámara más lejana?</p>
            <Seg name="distance" label="Distancia al grabador" opts={DISTANCE} value={ans.distance} onChange={(v) => set("distance", v)} />
          </fieldset>
          <fieldset className="vt-q__step" id="q3">
            <legend><i>03</i>¿Qué tan nítida querés la imagen?</legend>
            <p className="vt-q__hint">Más megapíxeles, más detalle al hacer zoom en una grabación (y un poco más de disco). Para una casa o negocio, 4 MP es el punto justo.</p>
            <Seg name="quality" label="Calidad de imagen" opts={QUALITY} value={ans.quality} onChange={(v) => set("quality", v)} wide />
            <p className="vt-q__sub">¿Cómo querés verlas de noche?</p>
            <Seg name="night" label="Visión nocturna" opts={NIGHT} value={ans.night} onChange={(v) => set("night", v)} />
            <div className="vt-q__checks">
              <Check checked={ans.audio} onChange={(v) => set("audio", v)} title="Con audio" text="Escuchás lo que pasa (micrófono en la cámara)." />
              <Check checked={ans.smart} onChange={(v) => set("smart", v)} title="Que distinga personas y vehículos" text="Te avisa solo cuando importa: no por hojas, lluvia o animales." />
            </div>
          </fieldset>
          <fieldset className="vt-q__step" id="q4">
            <legend><i>04</i>¿Cuántos días de grabación?</legend>
            <p className="vt-q__hint">Son los días que el grabador guarda los videos antes de borrar los más viejos para seguir grabando encima. Más días, más disco. Para una casa, 15 días suele alcanzar.</p>
            <div className="vt-seg" role="radiogroup" aria-label="Días de grabación">
              {DAYS.map((n) => <label key={n} className="vt-seg__opt"><input type="radio" name="days" checked={ans.days === n} onChange={() => set("days", n)} /><span>{n} días</span></label>)}
            </div>
            <p className="vt-q__sub">¿Graba todo el día o solo cuando hay movimiento?</p>
            <Seg name="record" label="Modo de grabación" opts={RECORD} value={ans.record} onChange={(v) => set("record", v)} />
          </fieldset>
          <fieldset className="vt-q__step" id="q5">
            <legend><i>05</i>¿Querés verlas desde el celular?</legend>
            <p className="vt-q__hint">Mirá tus cámaras en vivo y recibí alertas al instante en la palma de tu mano, aunque no estés en casa. Si no hace falta, igual graban y podés revisar todo en el grabador.</p>
            <div className="vt-seg" role="radiogroup" aria-label="Ver desde el celular">
              <label className="vt-seg__opt"><input type="radio" name="remote" checked={ans.remote} onChange={() => set("remote", true)} /><span>Sí</span></label>
              <label className="vt-seg__opt"><input type="radio" name="remote" checked={!ans.remote} onChange={() => set("remote", false)} /><span>No hace falta</span></label>
            </div>
          </fieldset>
          <fieldset className="vt-q__step" id="q6">
            <legend><i>06</i>¿Algo más para tu sistema?</legend>
            <p className="vt-q__hint">Opcional. Lo sumamos al kit con el equipo que calza.</p>
            <div className="vt-q__checks">
              <Check checked={ans.ups} onChange={(v) => set("ups", v)} title="Respaldo eléctrico (UPS)" text="Sigue grabando si se va la luz y protege el equipo de picos. La elegimos según lo que consume tu sistema." />
              <Check checked={ans.growth} onChange={(v) => set("growth", v)} title="Espacio para agregar cámaras después" text="Un grabador con canales libres: sumás cámaras sin cambiarlo." />
            </div>
          </fieldset>
          <label className="vt-q__extra">
            <input type="checkbox" checked={alarm} onChange={(e) => setAlarm(e.target.checked)} />
            <span><b>También me interesa una alarma</b><small>Sensores de puertas y movimiento, sirena y panel que avisan al celular. La incluimos en tu cotización.</small></span>
          </label>
        </form>

        <section className="vt-kitres" id="mi-kit" aria-labelledby="vt-kit-title" aria-busy={busy}>
          <div className="vt-kitres__head">
            <span className="sh-meta"><i className="vt-rec" />Kit sugerido · {kit ? `${kit.cameras} CAM · ${tb(kit.storage_tb)} TB` : "calculando"}</span>
            <h2 id="vt-kit-title">{kit ? `${kit.place_label} · ${kit.cameras} cámara${kit.cameras === 1 ? "" : "s"}` : "Tu kit"}</h2>
            {kit?.kit && <span className="vt-kitres__from">Kit de la tienda: {kit.kit.title}</span>}
          </div>
          {/* fotos fijas de los equipos reales que arma el kit (las mismas de su tarjeta en la portada) */}
          {kit && !!kit.photos?.length && <div className="vt-kitres__photos" data-busy={busy}><KitPhotos photos={kit.photos} cameras={kit.cameras} eager /></div>}
          {err && <p className="sh-err" role="alert">{err}{total > 0 && <> <button type="button" className="vt-kl__link" onClick={() => setRetry((n) => n + 1)}>Reintentar</button></>}</p>}
          {!kit && !err && <div className="sh-skel" style={{ height: 320 }} />}
          {kit && (
            <>
              {kit.summary && <Includes s={kit.summary} />}
              <ul className="vt-kitres__lines" data-busy={busy}>
                {kit.lines.map((l) => {
                  const priced = Number(l.product.display_price) > 0;
                  const k = lineKey(l);
                  const specs = l.specs ?? [];
                  const alts = l.alternatives ?? [];
                  const mine = isMine(l);
                  const fromKit = l.source === "pin" || (l.source === "choice" && !!l.suggested && !!l.pinned);
                  const isOpen = open === k;
                  return (
                    <li key={k}>
                      <Link to={to(l.product.path)} className="vt-kitres__img"><Viewfinder>{l.product.image ? <img src={l.product.image} alt="" loading="lazy" decoding="async" width={72} height={72} /> : <Ico d={IC.cam} />}</Viewfinder></Link>
                      <div className="vt-kitres__txt">
                        <span className="sh-meta">{l.role}{fromKit ? " · del kit" : mine ? " · elegido por vos" : ""}</span>
                        <Link to={to(l.product.path)} className="vt-kitres__name">{l.product.name}</Link>
                        {specs.length > 0 && <ul className="vt-kl__specs" aria-label="Datos clave">{specs.map((s) => <li key={s}>{s}</li>)}</ul>}
                        <small>{l.reason}</small>
                        <span className="vt-kl__row">
                          <AvailBadge a={l.product.availability} compact />
                          <Link to={to(l.product.path)} className="vt-kl__link">Ver ficha</Link>
                          {alts.length > 0 && (
                            <button type="button" id={`swap-${k}`} className="vt-kl__swap" aria-expanded={isOpen} aria-controls={`alt-${k}`} onClick={() => setOpen(isOpen ? null : k)}>
                              <Ico d={isOpen ? IC.x : IC.compare} size={14} />{isOpen ? "Cerrar" : "Cambiar"}
                            </button>
                          )}
                          {mine && <button type="button" className="vt-kl__link" onClick={() => choose(k, null)}>Volver a la sugerida</button>}
                        </span>
                      </div>
                      <div className="vt-kitres__qty">
                        <b>×{l.quantity}</b>
                        {priced ? <>{l.quantity > 1 && l.unit_price != null && <small>{money(l.unit_price, kit.currency)} c/u</small>}<span>{money(l.subtotal, kit.currency)}</span></> : <span>a consultar</span>}
                      </div>
                      {isOpen && (
                        <div className="vt-alts" id={`alt-${k}`} role="group" aria-label={`Alternativas para ${l.role}`}>
                          <p>Elegí otra opción que también calza en tu sistema. El total se recalcula solo.</p>
                          {alts.map((a) => {
                            const diff = Number(a.diff);
                            return (
                              /* la "Recomendada" es la sugerencia de hoy: elegirla es quitar tu cambio (salvo que el kit fije otra pieza ahi) */
                              <button key={a.product.id} type="button" className="vt-alt" onClick={() => choose(k, a.auto && !l.pinned ? null : a.product.id)}>
                                <span className="vt-alt__img">{a.product.image ? <img src={a.product.image} alt="" loading="lazy" decoding="async" width={48} height={48} /> : <Ico d={IC.cam} />}</span>
                                <span className="vt-alt__txt">
                                  <span className="vt-alt__tag" data-l={a.label}>{a.label}</span>
                                  <b>{a.product.name}</b>
                                  {(a.specs ?? []).length > 0 && <small>{(a.specs ?? []).join(" · ")}</small>}
                                  {a.note && <small className="vt-alt__note">{a.note}</small>}
                                  <AvailBadge a={a.product.availability} compact />
                                </span>
                                <span className="vt-alt__price">
                                  <b>{money(a.subtotal, kit.currency)}</b>
                                  {a.quantity > 1 && <small>{a.quantity} × {money(a.product.display_price, kit.currency)}</small>}
                                  <small className="vt-alt__diff" data-up={diff > 0}>{diff === 0 ? "Mismo precio" : `${diff > 0 ? "+" : "−"}${money(Math.abs(diff), kit.currency)}`}</small>
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </li>
                  );
                })}
                {kit.missing.map((m) => (
                  <li key={m.name} className="vt-kitres__missing">
                    <span className="vt-kitres__img"><Ico d={IC.tool} /></span>
                    <div className="vt-kitres__txt"><span className="sh-meta">{m.role} · lo cotizamos</span><b>{m.name}</b><small>{m.reason}</small></div>
                    <div className="vt-kitres__qty"><b>×{m.quantity}</b></div>
                  </li>
                ))}
              </ul>
              {!!kit.why?.length && (
                <div className="vt-why" data-busy={busy}>
                  <b>Por qué este kit</b>
                  <ul>{kit.why.map((w) => <li key={w.text} data-tone={w.tone}><Ico d={w.tone === "ok" ? IC.check : IC.info} size={14} /><span>{w.text}</span></li>)}</ul>
                </div>
              )}
              <dl className="vt-kitres__sum">
                <div><dt>Equipo con precio</dt><dd>{sums.units} artículo{sums.units === 1 ? "" : "s"}</dd></div>
                {sums.iva > 0 && <div><dt>IVA incluido en el total</dt><dd>{money(sums.iva, kit.currency)}</dd></div>}
                {sums.pending > 0 && <div><dt>Materiales por cotizar</dt><dd>{sums.pending} ítem{sums.pending === 1 ? "" : "s"}</dd></div>}
                <div><dt>Instalación</dt><dd>Se cotiza aparte</dd></div>
              </dl>
              <div className="vt-kitres__total" aria-live="polite" aria-atomic="true"><span>Total de referencia del equipo</span><PriceTag p={{ display_price: kit.total, display_currency: kit.currency }} big /></div>
              <ul className="vt-kitres__notes">{kit.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              <div className="vt-kitres__visit">
                <b><Ico d={IC.tool} size={16} />Recomendado: levantamiento técnico</b>
                <p>Un técnico de Crimson visita tu sitio, mide distancias, revisa dónde hay corriente e internet y confirma el kit exacto antes de instalar. Así no pagás de más ni te falta equipo.</p>
              </div>
              <div className="vt-kitres__cta">
                <button className="sh-btn sh-btn--crimson sh-btn--block" onClick={toAdvice} disabled={busy}><Ico d={IC.tool} />Pedir levantamiento e instalación</button>
                <button className="sh-btn sh-btn--block" data-done={added} onClick={addAll} aria-live="polite" disabled={busy || !priced.length}>
                  <Ico d={added ? IC.check : IC.bag} />{added ? (addedN === priced.length ? `Agregamos ${addedN} producto${addedN === 1 ? "" : "s"}` : `Agregamos ${addedN} de ${priced.length}; ${addedN === 0 ? "todos" : `los otros ${priced.length - addedN}`} se confirman por WhatsApp`) : `Agregar el equipo al ${ctx.isShop ? "carrito" : "pedido de cotización"}`}
                </button>
                {inCart && <button className="sh-btn sh-btn--block vt-gocart" onClick={ctx.openCart}><Ico d={IC.bag} />Ir {ctx.isShop ? "al carrito" : "a la cotización"}</button>}
                {ctx.store.whatsapp && <a className="sh-btn sh-btn--ghost sh-btn--block" href={waLink(ctx.store.whatsapp, kitText)} target="_blank" rel="noopener noreferrer" aria-disabled={busy} onClick={(e) => { if (busy) e.preventDefault(); }}><Ico d={IC.wa} />Enviar el kit por WhatsApp</a>}
              </div>
            </>
          )}
        </section>
      </div>
      {/* movil: el resumen del kit siempre a mano mientras se responden las preguntas */}
      {kit && (
        <div className="vt-kitbar">
          <span><b>{kit.cameras} cámara{kit.cameras === 1 ? "" : "s"}</b> · {money(kit.total, kit.currency)}</span>
          <a className="sh-btn sh-btn--crimson sh-btn--sm" href="#mi-kit">Ver mi kit</a>
        </div>
      )}
    </div>
  );
}

/** "Que incluye tu kit": lo esencial de un vistazo (camaras, dias, disco, PoE, celular). */
function Includes({ s }: { s: Summary }) {
  const poe = s.poe === "grabador" ? "PoE integrado en el grabador" : s.poe === "switch" ? `Switch PoE${s.switch_ports ? ` de ${s.switch_ports} puertos` : ""}` : "Alimentación por cotizar";
  const cam = [s.quality, s.night === "color" ? "a color de noche" : null, s.audio ? "con audio" : null, s.smart ? "detectan personas" : null].filter(Boolean).join(" · ");
  return (
    <div className="vt-incl">
      <b>Qué incluye tu kit</b>
      <ul>
        <li><Ico d={IC.cam} size={16} /><span>{s.outdoor} afuera · {s.indoor} adentro<small>{cam || "cámaras IP con cable de red"}</small></span></li>
        <li><Ico d={IC.clock} size={16} /><span>{s.days} días de grabación<small>{s.record === "movimiento" ? "solo cuando hay movimiento" : "continua, las 24 horas"}</small></span></li>
        <li><Ico d={IC.disk} size={16} /><span>{s.installed_tb != null ? `${tb(s.installed_tb)} TB de disco` : "Disco por cotizar"}<small>necesitás ≈ {tb(s.need_tb)} TB</small></span></li>
        <li><Ico d={IC.bolt} size={16} /><span>{poe}<small>un solo cable por cámara</small></span></li>
        {s.channels != null && <li><Ico d={IC.grid} size={16} /><span>Grabador de {s.channels} canales<small>{s.free_channels ? `${s.free_channels} libre${s.free_channels === 1 ? "" : "s"} para crecer` : "justo para tus cámaras"}</small></span></li>}
        <li><Ico d={IC.phone} size={16} /><span>{s.remote ? "Vista desde el celular" : "Sin vista desde el celular"}<small>{s.remote ? "app gratuita del fabricante" : "se revisa en el grabador"}</small></span></li>
        {s.ups && <li><Ico d={IC.bolt} size={16} /><span>{s.ups.va ? `UPS de ${s.ups.va} VA` : "UPS por cotizar"}<small>para ~{s.ups.load_w} W del sistema</small></span></li>}
      </ul>
    </div>
  );
}

/* grupo de opciones de una respuesta (botones segmentados con detalle) */
function Seg({ name, label, opts, value, onChange, wide }: { name: string; label: string; opts: [string, string, string][]; value: string; onChange: (v: string) => void; wide?: boolean }) {
  return (
    <div className="vt-seg vt-seg--det" data-wide={wide || undefined} role="radiogroup" aria-label={label}>
      {opts.map(([v, l, d]) => (
        <label key={v} className="vt-seg__opt"><input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} /><span><b>{l}</b><small>{d}</small></span></label>
      ))}
    </div>
  );
}

function Check({ checked, onChange, title, text }: { checked: boolean; onChange: (v: boolean) => void; title: string; text: string }) {
  return (
    <label className="vt-q__extra vt-q__extra--in">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span><b>{title}</b><small>{text}</small></span>
    </label>
  );
}

function Stepper({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  return (
    <div className="vt-stepper">
      <span>{label}</span>
      <div>
        <button type="button" onClick={() => onChange(clampN(value - 1))} aria-label={`Una cámara menos ${label.toLowerCase()}`} disabled={value <= 0}><Ico d={IC.minus} /></button>
        <output aria-live="polite" aria-label={`Cámaras ${label.toLowerCase()}`}>{value}</output>
        <button type="button" onClick={() => onChange(clampN(value + 1))} aria-label={`Una cámara más ${label.toLowerCase()}`} disabled={value >= 32}><Ico d={IC.plus} /></button>
      </div>
    </div>
  );
}
