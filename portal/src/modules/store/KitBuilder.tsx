/* "Arma tu sistema": 4 preguntas -> kit con productos reales de la tienda. Las cantidades salen de las mismas
   reglas del levantamiento tecnico (API /kit). Honesto: "kit sugerido, un tecnico lo valida". El resultado se
   recalcula al cambiar una respuesta (sin boton extra) y se puede llevar al carrito o mandar como asesoria. */
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AvailBadge, Crumbs, IC, Ico, PriceTag, Viewfinder, cannotBuy, money, useHead, usePath, useStore, waLink, type VProd } from "./storeKit";

type Line = { role: string; reason: string; quantity: number; subtotal: string | number; product: VProd };
type Kit = { answers: Answers; place_label: string; cameras: number; storage_tb: string | number; lines: Line[]; missing: { name: string; quantity: number; reason: string; role: string }[]; notes: string[]; total: string | number; currency: string };
type Answers = { place: string; indoor: number; outdoor: number; days: number; remote: boolean };

const PLACES: [string, string, string, string][] = [
  ["casa", "Casa", "Entradas, patio y cochera", IC.home],
  ["negocio", "Negocio u oficina", "Caja, mostrador y bodega", IC.shop],
  ["condominio", "Condominio", "Accesos, parqueo y perímetro", IC.building],
  ["bodega", "Bodega o planta", "Perímetro, andenes y patio", IC.factory],
];
const DAYS = [7, 15, 30, 60];
export const KIT_STORAGE = "crimson-shop:kit"; // la asesoria lo lee para adjuntar el kit

const fromQuery = (sp: URLSearchParams): Answers => ({
  place: PLACES.some(([k]) => k === sp.get("place")) ? sp.get("place")! : "casa",
  indoor: clampN(Number(sp.get("indoor") ?? 2)),
  outdoor: clampN(Number(sp.get("outdoor") ?? 2)),
  days: DAYS.includes(Number(sp.get("days"))) ? Number(sp.get("days")) : 15,
  remote: sp.get("remote") !== "false",
});
function clampN(n: number) { return Math.max(0, Math.min(32, Number.isFinite(n) ? Math.round(n) : 0)); }

export default function KitBuilder() {
  const ctx = useStore();
  const to = usePath();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [ans, setAns] = useState<Answers>(() => fromQuery(sp));
  const [kit, setKit] = useState<Kit | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState(false);
  const total = ans.indoor + ans.outdoor;

  useHead({ title: `Arma tu sistema de cámaras · ${ctx.store.name}`, description: "Respondé 4 preguntas y te armamos un kit de videovigilancia con productos reales, validado por un técnico.", path: "/arma-tu-sistema" });

  useEffect(() => {
    if (total === 0) { setKit(null); setErr("Elegí al menos una cámara."); return; }
    setErr(null);
    setBusy(true);
    const t = window.setTimeout(() => {
      ctx.api<Kit>("/kit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ans) })
        .then((k) => { setKit(k); setErr(null); })
        .catch((e) => setErr(e.message))
        .finally(() => setBusy(false));
      setSp(new URLSearchParams(Object.entries(ans).map(([k, v]) => [k, String(v)])), { replace: true });
    }, 280);
    return () => window.clearTimeout(t);
  }, [ans.place, ans.indoor, ans.outdoor, ans.days, ans.remote]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = <K extends keyof Answers>(k: K, v: Answers[K]) => setAns((a) => ({ ...a, [k]: v }));
  const priced = useMemo(() => (kit?.lines || []).filter((l) => !l.product.price_on_request && Number(l.product.display_price) > 0), [kit]);
  // desglose del panel: articulos con precio, IVA contenido (por la tarifa de cada producto) y lo que queda por cotizar
  const sums = useMemo(() => {
    const units = priced.reduce((n, l) => n + l.quantity, 0);
    const iva = priced.reduce((t, l) => { const s = Number(l.subtotal) || 0; const r = Number(l.product.tax_rate) || 0; return t + (s - s / (1 + r / 100)); }, 0);
    const pending = (kit?.lines.length || 0) - priced.length + (kit?.missing.length || 0);
    return { units, iva, pending };
  }, [priced, kit]);
  const placeName = PLACES.find(([k]) => k === ans.place)?.[1] || "";
  const steps: [string, string, string][] = [
    ["q1", "Lugar", placeName],
    ["q2", "Cámaras", `${total} en total`],
    ["q3", "Grabación", `${ans.days} días`],
    ["q4", "Celular", ans.remote ? "Sí" : "No"],
  ];
  const kitText = kit ? [
    `Hola ${ctx.store.name}, armé este kit en la tienda (${kit.place_label}, ${kit.cameras} cámaras, ${kit.answers.days} días de grabación):`,
    ...kit.lines.map((l) => `• ${l.quantity} × ${l.product.name} (${l.product.code})`),
    ...kit.missing.map((m) => `• ${m.quantity} × ${m.name} (por cotizar)`),
    `Total de referencia: ${money(kit.total, kit.currency)} IVA incluido.`,
  ].join("\n") : "";

  const addAll = () => {
    if (!kit) return;
    // lo que no se puede comprar en linea (sin existencia ni proveedor vigente) no entra al carrito: se consulta
    priced.filter((l) => !(ctx.isShop && cannotBuy(l.product.availability))).forEach((l) => ctx.addToCart(l.product, l.quantity));
    setAdded(true);
    window.setTimeout(() => setAdded(false), 2200);
  };
  const toAdvice = () => {
    if (!kit) return;
    try {
      sessionStorage.setItem(KIT_STORAGE, JSON.stringify({
        items: kit.lines.map((l) => ({ product_id: l.product.id, quantity: l.quantity, name: l.product.name })),
        text: `Kit sugerido desde "Arma tu sistema": ${kit.place_label}, ${kit.answers.indoor} cámara(s) adentro y ${kit.answers.outdoor} afuera, ${kit.answers.days} días de grabación${kit.answers.remote ? ", ver desde el celular" : ""}.`,
      }));
    } catch { /* sin almacenamiento: la asesoria igual se abre */ }
    nav(to("/asesoria?origen=arma-tu-sistema&solucion=cctv&instalar=1"));
  };

  return (
    <div className="sh-wrap vt-kitb">
      <Crumbs items={[{ name: "Tienda", path: "/" }, { name: "Arma tu sistema" }]} />
      <header className="vt-cathead">
        <span className="sh-eyebrow">Configurador · 4 preguntas</span>
        <h1>Arma tu sistema de cámaras</h1>
        <p>Respondé y te mostramos al instante un kit con equipo real de la tienda. Es una sugerencia: antes de instalar, un técnico de Crimson revisa que se ajuste a tu sitio.</p>
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
          </fieldset>
          <fieldset className="vt-q__step" id="q3">
            <legend><i>03</i>¿Cuántos días de grabación?</legend>
            <div className="vt-seg" role="radiogroup" aria-label="Días de grabación">
              {DAYS.map((n) => <label key={n} className="vt-seg__opt"><input type="radio" name="days" checked={ans.days === n} onChange={() => set("days", n)} /><span>{n} días</span></label>)}
            </div>
          </fieldset>
          <fieldset className="vt-q__step" id="q4">
            <legend><i>04</i>¿Querés verlas desde el celular?</legend>
            <div className="vt-seg" role="radiogroup" aria-label="Ver desde el celular">
              <label className="vt-seg__opt"><input type="radio" name="remote" checked={ans.remote} onChange={() => set("remote", true)} /><span>Sí</span></label>
              <label className="vt-seg__opt"><input type="radio" name="remote" checked={!ans.remote} onChange={() => set("remote", false)} /><span>No hace falta</span></label>
            </div>
          </fieldset>
        </form>

        <section className="vt-kitres" id="mi-kit" aria-labelledby="vt-kit-title" aria-busy={busy}>
          <div className="vt-kitres__head">
            <span className="sh-meta"><i className="vt-rec" />Kit sugerido · {kit ? `${kit.cameras} CAM · ${kit.storage_tb} TB` : "calculando"}</span>
            <h2 id="vt-kit-title">{kit ? `${kit.place_label} · ${kit.cameras} cámara${kit.cameras === 1 ? "" : "s"}` : "Tu kit"}</h2>
          </div>
          {err && <p className="sh-err" role="alert">{err}</p>}
          {!kit && !err && <div className="sh-skel" style={{ height: 320 }} />}
          {kit && (
            <>
              <ul className="vt-kitres__lines" data-busy={busy}>
                {kit.lines.map((l) => (
                  <li key={l.role + l.product.id}>
                    <Link to={to(l.product.path)} className="vt-kitres__img"><Viewfinder>{l.product.image ? <img src={l.product.image} alt="" loading="lazy" decoding="async" width={72} height={72} /> : <Ico d={IC.cam} />}</Viewfinder></Link>
                    <div className="vt-kitres__txt">
                      <span className="sh-meta">{l.role}</span>
                      <Link to={to(l.product.path)} className="vt-kitres__name">{l.product.name}</Link>
                      <small>{l.reason}</small>
                      <AvailBadge a={l.product.availability} compact />
                    </div>
                    <div className="vt-kitres__qty"><b>×{l.quantity}</b>{Number(l.product.display_price) > 0 ? <span>{money(l.subtotal, kit.currency)}</span> : <span>a consultar</span>}</div>
                  </li>
                ))}
                {kit.missing.map((m) => (
                  <li key={m.name} className="vt-kitres__missing">
                    <span className="vt-kitres__img"><Ico d={IC.tool} /></span>
                    <div className="vt-kitres__txt"><span className="sh-meta">{m.role} · lo cotizamos</span><b>{m.name}</b><small>{m.reason}</small></div>
                    <div className="vt-kitres__qty"><b>×{m.quantity}</b></div>
                  </li>
                ))}
              </ul>
              <dl className="vt-kitres__sum">
                <div><dt>Equipo con precio</dt><dd>{sums.units} artículo{sums.units === 1 ? "" : "s"}</dd></div>
                {sums.iva > 0 && <div><dt>IVA incluido en el total</dt><dd>{money(sums.iva, kit.currency)}</dd></div>}
                {sums.pending > 0 && <div><dt>Materiales por cotizar</dt><dd>{sums.pending} ítem{sums.pending === 1 ? "" : "s"}</dd></div>}
                <div><dt>Instalación</dt><dd>Se cotiza aparte</dd></div>
              </dl>
              <div className="vt-kitres__total"><span>Total de referencia del equipo</span><PriceTag p={{ display_price: kit.total, display_currency: kit.currency }} big /></div>
              <ul className="vt-kitres__notes">{kit.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              <div className="vt-kitres__cta">
                <button className="sh-btn sh-btn--crimson sh-btn--block" onClick={toAdvice}><Ico d={IC.tool} />Quiero que Crimson lo instale</button>
                <button className="sh-btn sh-btn--block" data-done={added} onClick={addAll} disabled={!priced.length}>
                  <Ico d={added ? IC.check : IC.bag} />{added ? `Agregado (${priced.length} productos)` : `Agregar el equipo al ${ctx.isShop ? "carrito" : "pedido de cotización"}`}
                </button>
                {added && <button className="vt-link" onClick={ctx.openCart}>Ver {ctx.isShop ? "carrito" : "cotización"} →</button>}
                {ctx.store.whatsapp && <a className="sh-btn sh-btn--ghost sh-btn--block" href={waLink(ctx.store.whatsapp, kitText)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Enviar el kit por WhatsApp</a>}
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
