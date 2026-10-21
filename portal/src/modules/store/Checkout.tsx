/* Checkout de la tienda en pasos (movil primero): datos -> entrega -> pago -> confirmar.
   El total SIEMPRE lo calcula el API (/quote) con el mismo motor que la factura; el navegador solo manda productos,
   cantidades y elecciones. Al confirmar se manda el total que el comprador vio (expected_total): si el servidor
   calcula otro, no se crea el pedido. La clave de idempotencia hace que un doble clic devuelva el mismo pedido.
   Modo "cotizacion" (tienda solo catalogo): un formulario corto que llega al equipo como mensaje. */
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Field, IC, Ico, local, money, newKey, session, wa, type ApiFn, type StoreInfo } from "./shopkit";
import { track, type GaItem } from "./analytics";
import "./checkout.css";

export type CartItem = { key: string; id: number; variant_id: number | null; name: string; image: string | null; price: number; currency: string; qty: number };
type Method = { code: string; label: string; needs_proof: boolean; simulated: boolean; hint: string; pickup_only?: boolean };
/* display_amount null: la tienda vende en otra moneda y falta el tipo de cambio (plan 1.12): "a consultar", nunca "gratis" */
type Rate = { name: string; zone: "retiro" | "gam" | "fuera_gam" | null; per_kg: boolean; display_amount: string | number | null };
type Cfg = {
  currency: string; methods: Method[]; shipping_rates: Rate[]; provinces: Record<string, string[]>;
  outside_gam: { provinces: string[]; cantons: string[] }; terms_required: boolean; pickup: { address?: string; hours?: string }; whatsapp: string;
  free_shipping_from: string | number | null; auto_invoice: boolean;
};
type Quote = {
  subtotal: number; discount_total: number; tax_total: number; shipping: number; total: number; currency: string; coupon: string | null;
  taxes: { rate: string; amount: number }[]; lines: { name: string; quantity: number; total: number; shipping: boolean }[];
  free_shipping?: boolean; free_shipping_from?: string | number | null;
  /* misma regla que el checkout (services/availability): lo que no se puede comprar en linea y el tope por producto */
  availability?: Record<string, { state: string; label: string; disponible: boolean; max_qty: number | null }>;
};
/* linea que el checkout va a rechazar: no se vende en linea o pasa del tope */
const lineProblem = (q: Quote | null, l: CartItem, cart: CartItem[]): string | null => {
  const a = q?.availability?.[String(l.id)];
  if (!a) return null;
  if (!a.disponible) return "No se puede comprar en línea: consultanos por WhatsApp";
  const total = cart.filter((x) => x.id === l.id).reduce((s, x) => s + x.qty, 0); // las variantes comparten existencia
  if (a.max_qty != null && total > Number(a.max_qty)) return `En línea podemos venderte hasta ${Number(a.max_qty)}`;
  return null;
};
export type Placed = {
  number: string | null; status: string; total: number; currency: string; payment_kind: string | null; payment_method: string | null;
  instructions: string | null; needs_proof: boolean; whatsapp: string; track_token: string; track_url: string;
  email: { to: string | null; status: string } | null; pay: { checkout_url?: string | null; simulated?: boolean; error?: string } | null; duplicate: boolean;
};

const SAVED = "crimson-shop:mis-datos";
const STEPS = ["Tus datos", "Entrega", "Pago", "Confirmar"] as const;
type Inv = "consumidor" | "fisica" | "juridica";
type Form = {
  name: string; email: string; phone: string; inv: Inv; id_number: string; company: string;
  dtype: "envio" | "retiro"; province: string; canton: string; district: string; address: string; rate: string;
  method: string; coupon: string; notes: string; terms: boolean; save: boolean; website: string;
};
const digits = (s: string) => s.replace(/\D/g, "");
const okEmail = (s: string) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(s.trim());

export default function Checkout({ api, store, cart, mode, onPlaced, onBack, setQty, remove, quoteText, onTerms }: {
  api: ApiFn; store: StoreInfo; cart: CartItem[]; mode: "pedido" | "cotizacion"; onPlaced: (p: Placed) => void; onBack: () => void;
  setQty: (k: string, n: number) => void; remove: (k: string) => void; quoteText: (l: CartItem[]) => string; onTerms: () => void;
}) {
  if (mode === "cotizacion") return <QuoteRequest api={api} store={store} cart={cart} onBack={onBack} quoteText={quoteText} />;
  return <OrderCheckout api={api} store={store} cart={cart} onPlaced={onPlaced} onBack={onBack} setQty={setQty} remove={remove} onTerms={onTerms} />;
}

function OrderCheckout({ api, store: S, cart, onPlaced, onBack, setQty, remove, onTerms }: {
  api: ApiFn; store: StoreInfo; cart: CartItem[]; onPlaced: (p: Placed) => void; onBack: () => void;
  setQty: (k: string, n: number) => void; remove: (k: string) => void; onTerms: () => void;
}) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [cfgErr, setCfgErr] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const saved = useMemo(() => { try { return JSON.parse(local.get(SAVED) || "null") as Partial<Form> | null; } catch { return null; } }, []);
  const [f, setF] = useState<Form>({
    name: "", email: "", phone: "", inv: "consumidor", id_number: "", company: "", dtype: "envio", province: "", canton: "", district: "", address: "", rate: "",
    method: "", coupon: "", notes: "", terms: false, website: "", ...(saved || {}), save: !!saved,
  });
  const [coupon, setCoupon] = useState<string | null>(null); // cupon aplicado (el que valido el servidor)
  const [quote, setQuote] = useState<Quote | null>(null);
  const [qErr, setQErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);
  const keyRef = useRef<string | null>(null);
  const errRef = useRef<HTMLUListElement>(null);
  const formTop = useRef<HTMLHeadingElement>(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));

  useEffect(() => { api<Cfg>("/checkout-config").then(({ data }) => setCfg(data)).catch((e) => setCfgErr((e as Error).message)); }, [api]);
  // GA4 (solo con consentimiento): una vez por visita al checkout
  const gaItems = (): GaItem[] => cart.map((l) => ({ item_id: String(l.id), item_name: l.name, price: l.price, quantity: l.qty }));
  const begun = useRef(false);
  useEffect(() => {
    if (begun.current || !cart.length) return;
    begun.current = true;
    track("begin_checkout", { currency: cart[0].currency, value: cart.reduce((a, l) => a + l.price * l.qty, 0), items: gaItems() });
  }, [cart.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // valores por defecto que dependen de la configuracion
  const pickupRate = cfg?.shipping_rates.find((r) => r.zone === "retiro") || null;
  const canPickup = !!cfg && (!!pickupRate || !!cfg.pickup?.address);
  const shipRates = (cfg?.shipping_rates || []).filter((r) => r.zone !== "retiro");
  const canShip = !!cfg && (shipRates.length > 0 || cfg.shipping_rates.length === 0);
  const outside = !!cfg && (cfg.outside_gam.provinces.includes(f.province) || cfg.outside_gam.cantons.includes(f.canton));
  // la tarifa GAM no aplica fuera de la GAM; la de "fuera de GAM" se deja siempre (la lista de cantones es conservadora)
  const rateOk = (r: Rate) => !(r.zone === "gam" && outside);
  // efectivo solo con retiro en sitio (Mi Tienda -> Pagos y entrega): con envio no se ofrece
  // sin retiro posible, el efectivo "solo al retirar" no se ofrece nunca (si no queda ninguno: compra no disponible)
  const usable = (cfg?.methods || []).filter((m) => !(m.pickup_only && !canPickup));
  const methods = usable.filter((m) => !(m.pickup_only && f.dtype !== "retiro"));
  const hiddenCash = usable.some((m) => m.pickup_only) && f.dtype !== "retiro";
  useEffect(() => {
    if (!cfg) return;
    setF((x) => {
      const n = { ...x };
      if (!canShip && canPickup) n.dtype = "retiro";
      if (!canPickup && canShip) n.dtype = "envio";
      const ok = cfg.methods.filter((m) => !(m.pickup_only && n.dtype !== "retiro"));
      if (!ok.some((m) => m.code === n.method)) n.method = ok[0]?.code || "";
      if (n.province && !cfg.provinces[n.province]) { n.province = ""; n.canton = ""; }
      return n;
    });
  }, [cfg, f.dtype]); // eslint-disable-line react-hooks/exhaustive-deps
  // tarifa segun tipo de entrega y zona: retiro -> la de retiro; envio -> la primera valida para esa direccion
  useEffect(() => {
    if (!cfg) return;
    if (f.dtype === "retiro") { if (f.rate !== (pickupRate?.name || "")) set("rate", pickupRate?.name || ""); return; }
    const cur = shipRates.find((r) => r.name === f.rate);
    if (!cur || !rateOk(cur)) { const first = shipRates.find(rateOk); set("rate", first?.name || ""); }
  }, [cfg, f.dtype, f.province, f.canton]); // eslint-disable-line react-hooks/exhaustive-deps

  const lines = useMemo(() => cart.map((l) => ({ product_id: l.id, variant_id: l.variant_id, quantity: String(l.qty) })), [cart]);
  const deliveryOk = f.dtype === "retiro" ? canPickup : !!(f.province && f.canton && f.address.trim().length >= 5);
  const delivery = f.dtype === "retiro" ? { type: "retiro" } : { type: "envio", province: f.province || null, canton: f.canton || null, district: f.district || null, address: f.address || null };

  // cotizacion en vivo (con la direccion solo cuando esta completa: asi el servidor valida la zona)
  useEffect(() => {
    if (!cfg || !lines.length) return;
    const t = window.setTimeout(() => {
      api<Quote>("/quote", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines, shipping_method: f.rate || null, coupon_code: coupon, ...(deliveryOk ? { delivery } : {}) }) })
        .then(({ data }) => { setQuote(data); setQErr(null); })
        .catch((e) => { setQuote(null); setQErr((e as Error).message); });
    }, 220);
    return () => window.clearTimeout(t);
  }, [api, cfg, lines, f.rate, coupon, deliveryOk, f.dtype, f.province, f.canton, f.address]); // eslint-disable-line react-hooks/exhaustive-deps

  // cualquier cambio en lo que se manda = intento nuevo (clave nueva)
  useEffect(() => { keyRef.current = null; }, [f, cart, coupon]);

  const cur = quote?.currency || cfg?.currency || cart[0]?.currency || S.currency || "CRC";
  const idLen = f.inv === "fisica" ? 9 : 10;
  const stepErrors = (s: number): string[] => {
    const e: string[] = [];
    if (s === 0) {
      if (f.name.trim().length < 2) e.push("Escribí tu nombre completo");
      if (!okEmail(f.email)) e.push("Escribí un correo válido: ahí te llega la confirmación");
      if (digits(f.phone).length < 8) e.push("Escribí un teléfono de 8 dígitos");
      if (f.inv !== "consumidor" && digits(f.id_number).length !== idLen) e.push(f.inv === "fisica" ? "La cédula física tiene 9 dígitos" : "La cédula jurídica tiene 10 dígitos");
      if (f.inv === "juridica" && f.company.trim().length < 2) e.push("Escribí la razón social");
    }
    if (s === 1) {
      if (f.dtype === "envio") {
        if (!f.province || !f.canton) e.push("Elegí provincia y cantón");
        if (f.address.trim().length < 5) e.push("Escribí la dirección exacta (otras señas)");
        if (cfg && cfg.shipping_rates.length && !f.rate) e.push("No hay una tarifa de envío para esa zona. Escribinos por WhatsApp.");
      }
    }
    if (s === 2 && !f.method) e.push("Elegí cómo vas a pagar");
    if (s === 3 && cfg?.terms_required && !f.terms) e.push("Aceptá los términos y condiciones para continuar");
    return e;
  };
  const errs = stepErrors(step);
  const goStep = (n: number) => { setStep(n); setTouched(false); setErr(null); window.setTimeout(() => formTop.current?.focus({ preventScroll: false }), 0); window.scrollTo({ top: 0 }); };
  const next = () => { if (errs.length) { setTouched(true); window.setTimeout(() => errRef.current?.scrollIntoView({ block: "center" }), 0); return; } goStep(Math.min(3, step + 1)); };

  const applyCoupon = async () => {
    const code = f.coupon.trim().toUpperCase();
    if (!code) { setCoupon(null); return; }
    try {
      await api<Quote>("/quote", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines, shipping_method: f.rate || null, coupon_code: code }) });
      setCoupon(code); setErr(null);
    } catch (e) { setErr((e as Error).message); }
  };

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (step < 3) { next(); return; }
    for (let s = 0; s < 4; s++) {
      if (!stepErrors(s).length) continue;
      if (s !== step) goStep(s);
      setTouched(true);
      window.setTimeout(() => errRef.current?.scrollIntoView({ block: "center" }), 0);
      return;
    }
    if (!quote || busy) return;
    setBusy(true); setErr(null);
    keyRef.current = keyRef.current || newKey();
    if (f.save) local.set(SAVED, JSON.stringify({ name: f.name, email: f.email, phone: f.phone, inv: f.inv, id_number: f.id_number, company: f.company, province: f.province, canton: f.canton, district: f.district, address: f.address }));
    else local.del(SAVED);
    const contact = {
      name: f.name.trim(), email: f.email.trim(), phone: f.phone.trim(),
      ...(f.inv !== "consumidor" ? { id_type: f.inv, id_number: digits(f.id_number) } : {}),
      ...(f.inv === "juridica" ? { company: f.company.trim() } : {}),
    };
    try {
      const { data } = await api<Placed>("/checkout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines, contact, delivery, shipping_method: f.rate || null, payment_method: f.method, coupon_code: coupon, notes: f.notes.trim() || null, accept_terms: f.terms, idempotency_key: keyRef.current, expected_total: String(quote.total), website: f.website || null }),
      });
      if (!data.number) { setErr("No pudimos registrar el pedido. Escribinos por WhatsApp."); return; }
      if (!data.duplicate) track("purchase", { transaction_id: data.number, currency: data.currency, value: Number(data.total), items: gaItems() });
      session.set(`crimson-shop:placed:${data.number}`, JSON.stringify({ email: data.email, pay: data.pay }));
      if (data.pay?.checkout_url) { onPlaced(data); window.location.assign(data.pay.checkout_url); return; }
      onPlaced(data);
    } catch (x) {
      const m = (x as Error).message;
      setErr(m);
      if (/total cambi/i.test(m)) keyRef.current = null; // se vuelve a cotizar y el comprador confirma de nuevo
    } finally { setBusy(false); }
  };

  if (cfgErr) return <div className="sh-wrap sh-empty"><b>{cfgErr}</b><button className="sh-btn sh-btn--ghost" onClick={onBack}>Volver a la tienda</button></div>;
  if (!cart.length) return <div className="sh-wrap sh-empty"><b>Tu carrito está vacío.</b><button className="sh-btn sh-btn--ghost" onClick={onBack}>Ver catálogo</button></div>;
  if (!cfg) return <div className="sh-wrap" style={{ paddingTop: 24 }}><div className="sh-skel" style={{ height: 320 }} /></div>;
  if (!usable.length) return <div className="sh-wrap sh-empty"><b>La compra en línea no está disponible en este momento.</b>{cfg.whatsapp && <a className="sh-btn sh-btn--wa" href={wa(cfg.whatsapp, `Hola ${S.name}, quiero comprar:\n${cart.map((l) => `• ${l.qty} × ${l.name}`).join("\n")}`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Pedir por WhatsApp</a>}</div>;

  const method = methods.find((m) => m.code === f.method);
  const cantons = f.province ? cfg.provinces[f.province] || [] : [];
  const totalTxt = quote ? money(quote.total, cur) : "…";
  const summary = <Summary quote={quote} qErr={qErr} cart={cart} cur={cur} setQty={setQty} remove={remove} />;

  return (
    <div className="sh-wrap ck">
      <nav className="sh-crumbs" aria-label="Ruta"><button onClick={onBack}><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Ico d={IC.back} size={14} />Seguir comprando</span></button></nav>
      <ol className="ck-steps" aria-label="Pasos del pedido">
        {STEPS.map((s, i) => (
          <li key={s} aria-current={i === step ? "step" : undefined} data-done={i < step}>
            <button type="button" onClick={() => (i < step ? goStep(i) : undefined)} disabled={i >= step} aria-label={`Paso ${i + 1}: ${s}${i < step ? " (completo, volver)" : ""}`}>
              <span className="ck-steps__n">{i < step ? <Ico d={IC.check} size={14} /> : i + 1}</span><span className="ck-steps__t">{s}</span>
            </button>
          </li>
        ))}
      </ol>
      <details className="ck-mini">
        <summary><span>Resumen · {cart.reduce((a, l) => a + l.qty, 0)} artículo(s)</span><b>{totalTxt}</b></summary>
        {summary}
      </details>

      <form className="sh-checkout" onSubmit={submit} noValidate>
        <div className="sh-form">
          <h1 ref={formTop} tabIndex={-1} style={{ outline: "none" }}><span className="ck-stepno">Paso {step + 1} de 4</span>{STEPS[step]}</h1>
          {/* honeypot: invisible para personas, los bots lo llenan */}
          <div className="ck-hp" aria-hidden="true"><label>Sitio web<input tabIndex={-1} autoComplete="off" value={f.website} onChange={(e) => set("website", e.target.value)} /></label></div>

          {step === 0 && (
            <fieldset className="sh-fieldset"><legend>Contacto</legend>
              <Field label="Nombre completo"><input autoComplete="name" value={f.name} onChange={(e) => set("name", e.target.value)} required /></Field>
              <div className="sh-2">
                <Field label="Correo" hint="Ahí te llega la confirmación y el enlace para seguir tu pedido."><input type="email" inputMode="email" autoComplete="email" value={f.email} onChange={(e) => set("email", e.target.value)} required /></Field>
                <Field label="Teléfono / WhatsApp"><input type="tel" inputMode="tel" autoComplete="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} placeholder="8888 8888" required /></Field>
              </div>
              <div className="sh-field"><span className="ck-label">Factura electrónica</span>
                <div className="ck-seg" role="radiogroup" aria-label="Factura electrónica">
                  {([["consumidor", "Consumidor final"], ["fisica", "A mi nombre"], ["juridica", "A una empresa"]] as [Inv, string][]).map(([k, l]) => (
                    <label key={k} className="ck-seg__opt"><input type="radio" name="inv" checked={f.inv === k} onChange={() => set("inv", k)} /><span>{l}</span></label>
                  ))}
                </div>
              </div>
              {f.inv === "juridica" && <Field label="Razón social"><input autoComplete="organization" value={f.company} onChange={(e) => set("company", e.target.value)} /></Field>}
              {f.inv !== "consumidor" && <Field label={f.inv === "fisica" ? "Cédula física (9 dígitos)" : "Cédula jurídica (10 dígitos)"}><input inputMode="numeric" value={f.id_number} onChange={(e) => set("id_number", e.target.value)} placeholder={f.inv === "fisica" ? "1 2345 6789" : "3 101 123456"} /></Field>}
              <label className="ck-check"><input type="checkbox" checked={f.save} onChange={(e) => set("save", e.target.checked)} /><span>Guardar mis datos en este dispositivo para la próxima compra <small>(no necesitás cuenta)</small></span></label>
            </fieldset>
          )}

          {step === 1 && (
            <>
              <fieldset className="sh-fieldset"><legend>¿Cómo lo recibís?</legend>
                <div className="sh-choice">
                  {canShip && <label className="sh-opt"><input type="radio" name="dtype" checked={f.dtype === "envio"} onChange={() => set("dtype", "envio")} /><span><Ico d={IC.truck} size={16} /> Envío a domicilio<small>Te lo llevamos a la dirección que indiqués.</small></span><span /></label>}
                  {canPickup && <label className="sh-opt"><input type="radio" name="dtype" checked={f.dtype === "retiro"} onChange={() => set("dtype", "retiro")} /><span><Ico d={IC.store} size={16} /> Retiro en sitio<small>{cfg.pickup?.address || "Te avisamos cuando esté listo."}{cfg.pickup?.hours ? ` · ${cfg.pickup.hours}` : ""}</small></span><span className="sh-meta">{pickupRate && pickupRate.display_amount == null ? "a consultar" : pickupRate && Number(pickupRate.display_amount) ? money(pickupRate.display_amount as number, cur) : "gratis"}</span></label>}
                </div>
              </fieldset>
              {f.dtype === "envio" && (
                <fieldset className="sh-fieldset"><legend>Dirección de entrega</legend>
                  <div className="sh-2">
                    <Field label="Provincia"><select autoComplete="address-level1" value={f.province} onChange={(e) => setF((x) => ({ ...x, province: e.target.value, canton: "" }))}><option value="">Elegí…</option>{Object.keys(cfg.provinces).map((p) => <option key={p}>{p}</option>)}</select></Field>
                    <Field label="Cantón"><select autoComplete="address-level2" value={f.canton} onChange={(e) => set("canton", e.target.value)} disabled={!f.province}><option value="">{f.province ? "Elegí…" : "Primero la provincia"}</option>{cantons.map((c) => <option key={c}>{c}</option>)}</select></Field>
                  </div>
                  <Field label="Distrito"><input autoComplete="address-level3" value={f.district} onChange={(e) => set("district", e.target.value)} /></Field>
                  <Field label="Dirección exacta (otras señas)"><textarea autoComplete="street-address" value={f.address} onChange={(e) => set("address", e.target.value)} placeholder="Del parque 200 m norte, casa blanca de portón negro" /></Field>
                  {Number(cfg.free_shipping_from) > 0 && <p className="ck-ok" style={{ fontWeight: 500 }}><Ico d={IC.truck} size={14} /> Envío gratis en compras desde {money(cfg.free_shipping_from as number, cur)}</p>}
                  {shipRates.length > 0 && (
                    <div className="sh-field"><span className="ck-label">Tarifa de envío</span>
                      <div className="sh-choice">{shipRates.map((r) => {
                        const ok = rateOk(r);
                        return (
                          <label key={r.name} className="sh-opt" data-off={!ok}>
                            <input type="radio" name="rate" checked={f.rate === r.name} disabled={!ok} onChange={() => set("rate", r.name)} />
                            <span>{r.name}{!ok && <small>Tu dirección está fuera de la GAM</small>}</span>
                            <span className="sh-meta">{r.per_kg ? "según peso" : r.display_amount == null ? "a consultar" : Number(r.display_amount) ? money(r.display_amount, cur) : "gratis"}</span>
                          </label>
                        );
                      })}</div>
                    </div>
                  )}
                </fieldset>
              )}
            </>
          )}

          {step === 2 && (
            <>
              <fieldset className="sh-fieldset"><legend>¿Cómo vas a pagar?</legend>
                <div className="sh-choice">{methods.map((m) => (
                  <label key={m.code} className="sh-opt"><input type="radio" name="pay" checked={f.method === m.code} onChange={() => set("method", m.code)} />
                    <span>{m.label}{m.simulated && <em className="ck-tag">Modo de prueba</em>}<small>{m.hint}</small></span><span /></label>
                ))}</div>
                {hiddenCash && <p className="sh-note" style={{ margin: 0 }}>El pago en efectivo es solo para retiro en sitio.</p>}
                {method?.needs_proof && <p className="sh-note" style={{ margin: 0 }}>Al confirmar te mostramos los datos para pagar. Tu pedido se confirma cuando subís el comprobante: hacelo cuanto antes.</p>}
                {method?.code === "tarjeta" && (method.simulated
                  ? <p className="ck-sim" role="note"><b>Pago simulado.</b> Esta tienda todavía no tiene ONVO conectado: el pago con tarjeta es solo de prueba y no se cobra nada.</p>
                  : <p className="sh-note" style={{ margin: 0 }}><Ico d={IC.lock} size={14} /> Te llevamos a la página segura de ONVO. Nunca vemos los datos de tu tarjeta.</p>)}
              </fieldset>
              <fieldset className="sh-fieldset"><legend>Cupón</legend>
                <div className="ck-coupon">
                  <input aria-label="Código de cupón" value={f.coupon} onChange={(e) => set("coupon", e.target.value.toUpperCase())} autoCapitalize="characters" placeholder="CÓDIGO" />
                  {coupon ? <button type="button" className="sh-btn sh-btn--ghost sh-btn--sm" onClick={() => { setCoupon(null); set("coupon", ""); }}>Quitar</button>
                    : <button type="button" className="sh-btn sh-btn--sm" onClick={applyCoupon} disabled={!f.coupon.trim()}>Aplicar</button>}
                </div>
                {coupon && <p className="ck-ok" role="status"><Ico d={IC.check} size={14} /> Cupón {coupon} aplicado</p>}
              </fieldset>
            </>
          )}

          {step === 3 && (
            <>
              <div className="sh-fieldset ck-review">
                <div><span className="sh-meta">Contacto</span><b>{f.name}</b><span>{f.email} · {f.phone}</span>{f.inv !== "consumidor" && <span>Factura a {f.inv === "juridica" ? f.company : f.name} · céd. {digits(f.id_number)}</span>}<button type="button" className="ck-edit" onClick={() => goStep(0)}>Cambiar</button></div>
                <div><span className="sh-meta">Entrega</span>{f.dtype === "retiro" ? <><b>Retiro en sitio</b><span>{cfg.pickup?.address}</span></> : <><b>{f.rate || "Envío"}</b><span>{[f.address, f.district, f.canton, f.province].filter(Boolean).join(", ")}</span></>}<button type="button" className="ck-edit" onClick={() => goStep(1)}>Cambiar</button></div>
                <div><span className="sh-meta">Pago</span><b>{method?.label}</b>{coupon && <span>Cupón {coupon}</span>}<button type="button" className="ck-edit" onClick={() => goStep(2)}>Cambiar</button></div>
              </div>
              <fieldset className="sh-fieldset"><legend>Nota del pedido</legend>
                <Field label="¿Algo que debamos saber? (opcional)"><textarea value={f.notes} maxLength={1000} onChange={(e) => set("notes", e.target.value)} placeholder="Horario para recibir, referencia, instalación…" /></Field>
              </fieldset>
              <label className="ck-check">
                <input type="checkbox" checked={f.terms} onChange={(e) => set("terms", e.target.checked)} />
                <span>Acepto los {S.legal?.terms ? <button type="button" className="ck-link" onClick={onTerms}>términos y condiciones</button> : "términos y condiciones"} de compra{cfg.terms_required ? "" : " (opcional)"}</span>
              </label>
            </>
          )}

          {(touched && errs.length > 0) && <ul ref={errRef} className="sh-err ck-errs" role="alert">{errs.map((x) => <li key={x}>{x}</li>)}</ul>}
          {err && <p className="sh-err" role="alert" style={{ margin: 0 }}>{err}</p>}
          <div className="ck-actions">
            {step > 0 && <button type="button" className="sh-btn sh-btn--ghost" onClick={() => goStep(step - 1)}><Ico d={IC.back} size={16} />Atrás</button>}
            <button type="submit" className="sh-btn sh-btn--crimson ck-go" disabled={busy || (step === 3 && (!quote || cart.some((l) => !!lineProblem(quote, l, cart))))}>
              {step < 3 ? <>Continuar<Ico d={IC.next} size={16} /></> : busy ? "Enviando…" : <>Confirmar pedido · {totalTxt}</>}
            </button>
          </div>
        </div>
        <aside className="sh-summary sh-panel ck-aside" aria-label="Resumen del pedido">{summary}</aside>
      </form>
    </div>
  );
}

function Summary({ quote, qErr, cart, cur, setQty, remove }: { quote: Quote | null; qErr: string | null; cart: CartItem[]; cur: string; setQty: (k: string, n: number) => void; remove: (k: string) => void }) {
  return (
    <div className="ck-sum">
      <span className="sh-meta">Tu pedido · {cart.reduce((a, l) => a + l.qty, 0)} artículo(s)</span>
      <ul className="ck-lines">
        {cart.map((l) => (
          <li key={l.key}>
            <div className="sh-line__img">{l.image ? <img src={l.image} alt="" loading="lazy" decoding="async" /> : null}</div>
            <div style={{ minWidth: 0 }}>
              <div className="sh-line__name">{l.name}</div>
              {lineProblem(quote, l, cart) && <div className="sh-note" style={{ margin: "2px 0", color: "var(--crimson-dark, #9b1c2c)" }}>{lineProblem(quote, l, cart)}</div>}
              <div className="ck-qty">
                <button type="button" onClick={() => setQty(l.key, l.qty - 1)} aria-label={`Quitar uno de ${l.name}`} disabled={l.qty <= 1}>−</button>
                <span aria-label="Cantidad">{l.qty}</span>
                <button type="button" onClick={() => setQty(l.key, l.qty + 1)} aria-label={`Agregar uno de ${l.name}`}>+</button>
                <button type="button" className="sh-rm" onClick={() => remove(l.key)}>Quitar</button>
              </div>
            </div>
            <b className="sh-line__total">{money(l.qty * l.price, l.currency)}</b>
          </li>
        ))}
      </ul>
      {quote ? (
        <div className="ck-totals">
          <div className="sh-totrow"><span>Subtotal (sin IVA)</span><b>{money(quote.subtotal, cur)}</b></div>
          {Number(quote.discount_total) > 0 && <div className="sh-totrow"><span>Descuento{quote.coupon ? ` (${quote.coupon})` : ""}</span><b>−{money(quote.discount_total, cur)}</b></div>}
          <div className="sh-totrow"><span>Envío (sin IVA)</span><b>{quote.free_shipping ? "Gratis" : Number(quote.shipping) > 0 ? money(quote.shipping, cur) : "—"}</b></div>
          {!quote.free_shipping && Number(quote.free_shipping_from) > 0 && <p className="sh-note" style={{ margin: 0 }}>Envío gratis en compras desde {money(quote.free_shipping_from as number, cur)}.</p>}
          {(quote.taxes?.length ? quote.taxes : [{ rate: "13", amount: quote.tax_total }]).map((t) => <div key={t.rate} className="sh-totrow"><span>IVA {t.rate} %</span><b>{money(t.amount, cur)}</b></div>)}
          <div className="sh-totrow sh-totrow--big"><span>Total</span><b>{money(quote.total, cur)}</b></div>
          <p className="sh-note" style={{ margin: 0 }}>Calculado por la tienda con los precios vigentes. Te enviamos la factura electrónica en un máximo de 1 día hábil.</p>
        </div>
      ) : <p className="sh-note" role="status">{qErr || "Calculando total…"}</p>}
    </div>
  );
}

function QuoteRequest({ api, store: S, cart, onBack, quoteText }: { api: ApiFn; store: StoreInfo; cart: CartItem[]; onBack: () => void; quoteText: (l: CartItem[]) => string }) {
  const [f, setF] = useState({ name: "", email: "", phone: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const ok = f.name.trim().length > 1 && okEmail(f.email);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ok || busy) return;
    setBusy(true); setErr(null);
    try {
      const msg = `${quoteText(cart)}${f.notes ? `\n\nNotas: ${f.notes}` : ""}`;
      await api("/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: f.name, email: f.email, phone: f.phone || null, message: msg.slice(0, 3900) }) });
      setSent(true);
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  if (sent) return <div className="sh-wrap"><div className="sh-done" role="status"><span className="sh-eyebrow">Solicitud recibida</span><h1>¡Gracias, {f.name.split(" ")[0]}!</h1><p className="sh-note" style={{ margin: 0, fontSize: 15 }}>El equipo ya tiene tu lista y te responde a {f.email} con precio final y disponibilidad.</p><button className="sh-btn sh-btn--ghost" onClick={onBack}>Volver a la tienda</button></div></div>;
  if (!cart.length) return <div className="sh-wrap sh-empty"><b>Tu cotización está vacía.</b><button className="sh-btn sh-btn--ghost" onClick={onBack}>Ver catálogo</button></div>;
  return (
    <div className="sh-wrap ck">
      <nav className="sh-crumbs" aria-label="Ruta"><button onClick={onBack}><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Ico d={IC.back} size={14} />Seguir comprando</span></button></nav>
      <form className="sh-checkout" onSubmit={submit} noValidate>
        <div className="sh-form">
          <h1>Solicitar cotización</h1>
          <fieldset className="sh-fieldset"><legend>Tus datos</legend>
            <Field label="Nombre completo"><input autoComplete="name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <div className="sh-2">
              <Field label="Correo"><input type="email" autoComplete="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
              <Field label="Teléfono / WhatsApp"><input type="tel" autoComplete="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
            </div>
            <Field label="¿Algo más que debamos saber? (instalación, plazos…)"><textarea value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
          </fieldset>
        </div>
        <aside className="sh-summary sh-panel" aria-label="Resumen">
          <span className="sh-meta">Tu lista · {cart.reduce((a, l) => a + l.qty, 0)} artículo(s)</span>
          <ul className="ck-lines">{cart.map((l) => <li key={l.key}><div className="sh-line__img">{l.image ? <img src={l.image} alt="" /> : null}</div><div className="sh-line__name">{l.qty} × {l.name}</div><span /></li>)}</ul>
          <p className="sh-note" style={{ margin: 0 }}>Te respondemos con precio final, disponibilidad y tiempos de entrega.</p>
          {err && <p className="sh-err" role="alert" style={{ margin: 0 }}>{err}</p>}
          <button className="sh-btn sh-btn--crimson sh-btn--block" type="submit" disabled={!ok || busy}>{busy ? "Enviando…" : "Enviar solicitud"}</button>
          {S.whatsapp && <a className="sh-btn sh-btn--wa sh-btn--block" href={wa(S.whatsapp, quoteText(cart))} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Mejor por WhatsApp</a>}
        </aside>
      </form>
    </div>
  );
}
