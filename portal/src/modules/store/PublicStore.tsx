/* Sitio publico de la tienda: portada por bloques, catalogo, ficha de producto, carrito y checkout. Sin sesion. */
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { Blocks, type Block, type PubProduct } from "./Blocks";

type Home = { store: { name: string; tagline: string; logo_url: string | null; primary: string; secondary: string; font: string; kind: string; whatsapp: string; currency: string; legal: { privacy: string; terms: string } }; nav: { slug: string; title: string }[]; categories: { id: number; name: string }[]; shipping_rates: { name: string; amount: number }[]; payment_methods: { name: string; instructions: string }[] };
type Cart = Record<number, { p: PubProduct; qty: number }>;
const money = (v: string | number, cur = "CRC") => new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(Number(v));

export default function PublicStore() {
  const { slug } = useParams();
  const [home, setHome] = useState<Home | null>(null);
  const [products, setProducts] = useState<PubProduct[]>([]);
  const [page, setPage] = useState<{ title: string; blocks: Block[] } | null>(null);
  const [view, setView] = useState<{ kind: "home" | "catalogo" | "producto" | "checkout"; id?: number }>({ kind: "home" });
  const [detail, setDetail] = useState<(PubProduct & { related: PubProduct[] }) | null>(null);
  const [cart, setCart] = useState<Cart>({});
  const [cat, setCat] = useState<number | "">("");
  const [q, setQ] = useState("");
  const [form, setForm] = useState({ name: "", email: "", phone: "", id_number: "", address: "", shipping_method: "", payment_method: "", coupon_code: "", notes: "" });
  const [quote, setQuote] = useState<{ subtotal: string; discount_total: string; tax_total: string; shipping: string; total: string } | null>(null);
  const [done, setDone] = useState<{ number: string; total: string; instructions: string | null; whatsapp: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const get = async <T,>(path: string): Promise<T> => { const r = await fetch(`/api/public/store/${slug}${path}`); if (!r.ok) throw new Error((await r.json()).detail || "Error"); return r.json(); };
  useEffect(() => {
    get<Home>("").then(async (h) => { setHome(h); if (h.nav[0]) setPage(await get(`/pages/${h.nav[0].slug}`)); }).catch((e) => setErr(e.message));
    get<PubProduct[]>("/products").then(setProducts).catch(() => {});
  }, [slug]);
  useEffect(() => { if (view.kind === "catalogo") { const qs = new URLSearchParams(); if (q) qs.set("q", q); if (cat) qs.set("category_id", String(cat)); get<PubProduct[]>(`/products?${qs}`).then(setProducts); } }, [q, cat, view.kind]);
  useEffect(() => { if (view.kind === "producto" && view.id) get<PubProduct & { related: PubProduct[] }>(`/products/${view.id}`).then(setDetail); }, [view]);

  const items = Object.values(cart);
  const count = items.reduce((a, i) => a + i.qty, 0);
  const cartLines = useMemo(() => items.map((i) => ({ product_id: i.p.id, quantity: String(i.qty) })), [items]);
  useEffect(() => {
    if (view.kind !== "checkout" || !cartLines.length) return;
    fetch(`/api/public/store/${slug}/quote`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines: cartLines, contact: { name: form.name || "x", email: form.email || "x@x.com" }, shipping_method: form.shipping_method || null, coupon_code: form.coupon_code || null }) })
      .then(async (r) => (r.ok ? setQuote(await r.json()) : setQuote(null)));
  }, [view.kind, cartLines, form.shipping_method, form.coupon_code, form.name, form.email, slug]);

  const add = (p: PubProduct, qty = 1) => setCart((c) => ({ ...c, [p.id]: { p, qty: (c[p.id]?.qty || 0) + qty } }));
  const checkout = async () => {
    setErr(null);
    const r = await fetch(`/api/public/store/${slug}/checkout`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lines: cartLines, contact: { name: form.name, email: form.email, phone: form.phone, id_number: form.id_number, address: form.address }, shipping_method: form.shipping_method || null, payment_method: form.payment_method || null, coupon_code: form.coupon_code || null, notes: form.notes }) });
    if (!r.ok) return setErr((await r.json()).detail || "No se pudo completar el pedido");
    setDone(await r.json());
    setCart({});
  };

  if (err && !home) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", color: "var(--text-2)" }}>{err}</div>;
  if (!home) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}><span className="spinner" /></div>;
  const primary = home.store.primary;
  const isShop = home.store.kind === "tienda";

  return (
    <div style={{ minHeight: "100dvh", background: "var(--bg)", fontFamily: home.store.font === "Manrope" ? "var(--sans)" : `"${home.store.font}", var(--sans)` }}>
      <header style={{ position: "sticky", top: 0, zIndex: 10, background: "var(--surface)", borderBottom: "1px solid var(--hair)", padding: "12px 24px", display: "flex", gap: 18, alignItems: "center" }}>
        <button onClick={() => setView({ kind: "home" })} style={{ display: "flex", gap: 10, alignItems: "center", border: 0, background: "none", cursor: "pointer" }}>
          {home.store.logo_url ? <img src={home.store.logo_url} alt="" style={{ height: 28 }} /> : <b style={{ fontFamily: "var(--display)", fontSize: 18 }}>{home.store.name}</b>}
        </button>
        <nav style={{ display: "flex", gap: 14, marginLeft: 8 }}>
          {home.nav.map((n) => <button key={n.slug} onClick={async () => { setPage(await get(`/pages/${n.slug}`)); setView({ kind: "home" }); }} style={{ border: 0, background: "none", cursor: "pointer", fontWeight: 600, color: "var(--text-2)" }}>{n.title}</button>)}
          <button onClick={() => setView({ kind: "catalogo" })} style={{ border: 0, background: "none", cursor: "pointer", fontWeight: 600, color: "var(--text-2)" }}>Catálogo</button>
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
          {home.store.whatsapp && <a href={`https://wa.me/${home.store.whatsapp.replace(/\D/g, "")}`} target="_blank" rel="noopener" style={{ fontSize: 13, color: "var(--text-2)" }}>WhatsApp</a>}
          {isShop && <button onClick={() => setView({ kind: "checkout" })} style={{ background: primary, color: "#fff", border: 0, borderRadius: 10, padding: "9px 16px", fontWeight: 700, cursor: "pointer" }}>Carrito {count > 0 && `(${count})`}</button>}
        </div>
      </header>

      {view.kind === "home" && page && <Blocks blocks={page.blocks} primary={primary} products={products} onCta={(to) => setView({ kind: to === "productos" ? "catalogo" : "home" })} onProduct={(id) => setView({ kind: "producto", id })} />}

      {view.kind === "catalogo" && (
        <section style={{ padding: "36px 24px", maxWidth: 1100, margin: "0 auto" }}>
          <h1 style={{ fontFamily: "var(--display)", fontSize: 34, letterSpacing: "-0.02em" }}>Catálogo</h1>
          <div style={{ display: "flex", gap: 10, margin: "16px 0 22px", flexWrap: "wrap" }}>
            <input className="input" placeholder="Buscar…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} />
            <select className="select" value={cat} onChange={(e) => setCat(e.target.value ? Number(e.target.value) : "")} style={{ maxWidth: 220 }}><option value="">Todas las categorías</option>{home.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 16 }}>
            {products.map((p) => (
              <div key={p.id} style={{ border: "1px solid var(--hair)", borderRadius: 14, overflow: "hidden", background: "var(--surface)" }}>
                <button onClick={() => setView({ kind: "producto", id: p.id })} style={{ border: 0, padding: 0, background: "none", cursor: "pointer", width: "100%" }}>
                  <div style={{ aspectRatio: "4/3", background: "var(--bg-2)", display: "grid", placeItems: "center" }}>{p.image ? <img src={p.image} alt={p.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <span style={{ color: "var(--text-3)", fontSize: 12 }}>Sin imagen</span>}</div>
                </button>
                <div style={{ padding: 12 }}>
                  <b style={{ fontSize: 14, display: "block", minHeight: 38 }}>{p.name}</b>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
                    <span style={{ color: primary, fontWeight: 700, fontFamily: "var(--mono)" }}>{money(p.price, p.currency)}</span>
                    {isShop && <button onClick={() => add(p)} style={{ background: "var(--ink)", color: "#fff", border: 0, borderRadius: 8, padding: "7px 12px", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>Agregar</button>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {view.kind === "producto" && detail && (
        <section style={{ padding: "36px 24px", maxWidth: 1000, margin: "0 auto" }}>
          <button onClick={() => setView({ kind: "catalogo" })} style={{ border: 0, background: "none", cursor: "pointer", color: "var(--text-2)", marginBottom: 14 }}>← Catálogo</button>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 30 }}>
            <div style={{ aspectRatio: "1", background: "var(--bg-2)", borderRadius: 16, display: "grid", placeItems: "center", overflow: "hidden" }}>{detail.image ? <img src={detail.image} alt={detail.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <span style={{ color: "var(--text-3)" }}>Sin imagen</span>}</div>
            <div>
              <h1 style={{ fontFamily: "var(--display)", fontSize: 30, letterSpacing: "-0.02em" }}>{detail.name}</h1>
              <div style={{ color: primary, fontWeight: 700, fontSize: 26, fontFamily: "var(--mono)", margin: "10px 0 16px" }}>{money(detail.price, detail.currency)}</div>
              {detail.description && <p style={{ color: "var(--text-2)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{detail.description}</p>}
              {isShop && <button onClick={() => { add(detail); setView({ kind: "checkout" }); }} style={{ marginTop: 20, background: primary, color: "#fff", border: 0, borderRadius: 10, padding: "13px 24px", fontWeight: 700, cursor: "pointer" }}>Agregar al carrito</button>}
              {!isShop && home.store.whatsapp && <a href={`https://wa.me/${home.store.whatsapp.replace(/\D/g, "")}?text=${encodeURIComponent(`Hola, me interesa ${detail.name}`)}`} target="_blank" rel="noopener" style={{ display: "inline-block", marginTop: 20, background: primary, color: "#fff", borderRadius: 10, padding: "13px 24px", fontWeight: 700 }}>Consultar por WhatsApp</a>}
            </div>
          </div>
          {detail.related.length > 0 && <Blocks blocks={[{ type: "products", title: "Artículos relacionados", limit: 4 }]} primary={primary} products={detail.related} onProduct={(id) => setView({ kind: "producto", id })} />}
        </section>
      )}

      {view.kind === "checkout" && (
        <section style={{ padding: "36px 24px", maxWidth: 940, margin: "0 auto" }}>
          {done ? (
            <div style={{ background: "var(--surface)", border: "1px solid var(--hair)", borderRadius: 16, padding: 30, textAlign: "center" }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: ".18em", color: "var(--text-3)" }}>PEDIDO CONFIRMADO</div>
              <h1 style={{ fontFamily: "var(--display)", fontSize: 34, margin: "8px 0" }}>{done.number}</h1>
              <div style={{ fontSize: 22, fontWeight: 700, color: primary }}>{money(done.total, home.store.currency)}</div>
              {done.instructions && <p style={{ marginTop: 16, color: "var(--text-2)" }}>{done.instructions}</p>}
              {done.whatsapp && <a href={`https://wa.me/${done.whatsapp.replace(/\D/g, "")}?text=${encodeURIComponent(`Hola, acabo de hacer el pedido ${done.number}`)}`} target="_blank" rel="noopener" style={{ display: "inline-block", marginTop: 18, background: "#25D366", color: "#fff", borderRadius: 10, padding: "12px 22px", fontWeight: 700 }}>Enviar comprobante por WhatsApp</a>}
            </div>
          ) : items.length === 0 ? (
            <div style={{ textAlign: "center", padding: 50, color: "var(--text-2)" }}>Tu carrito está vacío. <button onClick={() => setView({ kind: "catalogo" })} style={{ border: 0, background: "none", color: primary, fontWeight: 700, cursor: "pointer" }}>Ver catálogo</button></div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 24, alignItems: "start" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <h1 style={{ fontFamily: "var(--display)", fontSize: 28 }}>Finalizar compra</h1>
                <div className="grid-2">
                  <input className="input" placeholder="Nombre completo" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
                  <input className="input" placeholder="Correo" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
                  <input className="input" placeholder="Teléfono" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
                  <input className="input" placeholder="Cédula (para factura)" value={form.id_number} onChange={(e) => setForm({ ...form, id_number: e.target.value })} />
                </div>
                <input className="input" placeholder="Dirección de entrega" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
                <div className="grid-2">
                  <select className="select" value={form.shipping_method} onChange={(e) => setForm({ ...form, shipping_method: e.target.value })}><option value="">Método de envío…</option>{home.shipping_rates.map((r) => <option key={r.name} value={r.name}>{r.name} · {money(r.amount, home.store.currency)}</option>)}</select>
                  <select className="select" value={form.payment_method} onChange={(e) => setForm({ ...form, payment_method: e.target.value })}><option value="">Método de pago…</option>{home.payment_methods.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}</select>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <input className="input" placeholder="Código de cupón" value={form.coupon_code} onChange={(e) => setForm({ ...form, coupon_code: e.target.value.toUpperCase() })} style={{ maxWidth: 220 }} />
                  <input className="input" placeholder="Notas del pedido" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </div>
                {err && <p style={{ color: "var(--bad)", fontSize: 13 }}>{err}</p>}
              </div>
              <div style={{ background: "var(--surface)", border: "1px solid var(--hair)", borderRadius: 16, padding: 18 }}>
                {items.map((i) => (
                  <div key={i.p.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "8px 0", borderBottom: "1px solid var(--hair)" }}>
                    <div style={{ flex: 1 }}><b style={{ fontSize: 13 }}>{i.p.name}</b><div style={{ fontSize: 12, color: "var(--text-2)" }}>{money(i.p.price, i.p.currency)}</div></div>
                    <input className="input input--mono" style={{ width: 62, height: 30 }} value={i.qty} onChange={(e) => setCart({ ...cart, [i.p.id]: { p: i.p, qty: Math.max(1, Number(e.target.value) || 1) } })} />
                    <button className="x" onClick={() => setCart(Object.fromEntries(Object.entries(cart).filter(([k]) => Number(k) !== i.p.id)))}>✕</button>
                  </div>
                ))}
                {quote && (
                  <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6, fontSize: 13, color: "var(--text-2)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}><span>Subtotal</span><span>{money(quote.subtotal, home.store.currency)}</span></div>
                    {Number(quote.discount_total) > 0 && <div style={{ display: "flex", justifyContent: "space-between", color: primary }}><span>Descuento</span><span>−{money(quote.discount_total, home.store.currency)}</span></div>}
                    <div style={{ display: "flex", justifyContent: "space-between" }}><span>Envío</span><span>{money(quote.shipping, home.store.currency)}</span></div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}><span>IVA</span><span>{money(quote.tax_total, home.store.currency)}</span></div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 19, fontWeight: 700, color: "var(--text)", borderTop: "1px solid var(--hair)", paddingTop: 8, marginTop: 4 }}><span>Total</span><span>{money(quote.total, home.store.currency)}</span></div>
                  </div>
                )}
                <button onClick={checkout} disabled={!form.name || !(form.email || form.phone)} style={{ width: "100%", marginTop: 16, background: primary, color: "#fff", border: 0, borderRadius: 10, padding: "13px 0", fontWeight: 700, cursor: "pointer", opacity: !form.name || !(form.email || form.phone) ? 0.5 : 1 }}>Confirmar pedido</button>
              </div>
            </div>
          )}
        </section>
      )}

      <footer style={{ borderTop: "1px solid var(--hair)", padding: "26px 24px", marginTop: 40, color: "var(--text-3)", fontSize: 12.5, textAlign: "center" }}>
        {home.store.name} · {home.store.tagline}
      </footer>
    </div>
  );
}
