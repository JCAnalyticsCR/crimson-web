/* POS — pantalla de cobro rapido, teclado + touch. Ruta privada /pos. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon, Modal } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import { availText, sinExistencia, type Availability } from "../../ui/Availability";
import NuevoCliente from "../../ui/NuevoCliente";

/* ---------- Types ---------- */
type Variant = { id: number; name: string; code: string; price: number | null };
/* stock = libre propio en bodegas de venta (funcion unica del API); availability trae tambien lo del proveedor.
   pos_price / pos_currency: el precio en la moneda de la caja (el API convierte USD/CRC con el tipo del dia). Sin tipo
   de cambio el API manda pos_price null (sin_tc): se muestra en su propia moneda (nunca un dolar como colon) y NO se
   deja agregar al carrito, que suma todo en la moneda de la caja. */
type CatalogItem = {
  id: number; code: string; name: string; price: number; currency: string;
  item_type: string; category_id: number | null; tax_rate: number;
  stock: number | string | null; image: string | null; variants: Variant[];
  pos_price?: number | string; pos_currency?: string; availability?: Availability | null;
  sin_tc?: boolean; // en otra moneda y sin tipo de cambio: no se puede cobrar en la caja
  brand?: string | null;
  stock_by_wh?: Record<string, number>; // existencia propia por bodega de venta (filtro "Bodega")
};
type PosFilters = { categories: { id: number; name: string; parent_id: number | null }[]; warehouses: { id: number; name: string; is_default: boolean }[] };
/* busqueda sin tildes ni mayusculas, con todas las palabras ("camara 4mp hik") */
const fold = (t: string) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const PAGINA = 120; // tarjetas por tanda: el catalogo trae ~1 800 productos y dibujarlos todos traba la caja
type CartLine = {
  key: string; product_id: number; variant_id?: number;
  name: string; unit_price: number; tax_rate: number;
  discount: number; quantity: number; currency: string;
};
type PosPayment = { method: string; amount: number; reference: string };
type CustResult = { id: number; name: string; id_number: string | null };
type SaleResult = {
  invoice_id: number; number: string; doc_type: string;
  total: number; paid: number; change: number; einvoice: string;
};

/* ---------- Constants ---------- */
const METHODS = ["efectivo", "tarjeta", "sinpe", "transferencia"] as const;
const QUICK_BASES = [1000, 5000, 10000, 20000] as const;

/* ---------- Helpers ---------- */
const lineTotal = (l: CartLine) =>
  l.unit_price * l.quantity * (1 - l.discount / 100) * (1 + l.tax_rate / 100);

export default function Pos() {
  const { toast, allows } = useSession();
  const [nuevoCliente, setNuevoCliente] = useState(false);

  /* catalog */
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [catLoading, setCatLoading] = useState(true);
  const [q, setQ] = useState("");
  /* filtros (26/10): categoria (con sus subcategorias), bodega, disponibilidad y tipo */
  const [opts, setOpts] = useState<PosFilters | null>(null);
  const [fCat, setFCat] = useState("");
  const [fWh, setFWh] = useState("");
  const [fDisp, setFDisp] = useState("");
  const [fTipo, setFTipo] = useState("");
  const [cuantos, setCuantos] = useState(PAGINA);

  /* cart */
  const [cart, setCart] = useState<CartLine[]>([]);
  const [docType, setDocType] = useState<"TE" | "FE">("TE");
  const [customer, setCustomer] = useState<CustResult | null>(null);
  const [custQ, setCustQ] = useState("");
  const [custResults, setCustResults] = useState<CustResult[]>([]);

  /* payments */
  const [payments, setPayments] = useState<PosPayment[]>([
    { method: "efectivo", amount: 0, reference: "" },
  ]);
  const [tip] = useState(0);

  /* modals */
  const [variantItem, setVariantItem] = useState<CatalogItem | null>(null);
  const [receipt, setReceipt] = useState<SaleResult | null>(null);
  const [busy, setBusy] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);

  /* load catalog */
  useEffect(() => {
    api<CatalogItem[]>("/pos/catalog")
      .then((items) => setCatalog(items.map((i) => ({ ...i, price: Number(i.price), tax_rate: Number(i.tax_rate), pos_price: i.pos_price != null ? Number(i.pos_price) : Number(i.price), pos_currency: i.pos_price != null ? i.pos_currency || i.currency : i.currency, sin_tc: i.pos_price == null }))))
      .catch((e) => toast(e instanceof Error ? e.message : "Error al cargar catálogo", "bad"))
      .finally(() => setCatLoading(false));
  }, [toast]);

  useEffect(() => { api<PosFilters>("/pos/filters").then(setOpts).catch(() => setOpts(null)); }, []);
  useEffect(() => { setCuantos(PAGINA); }, [q, fCat, fWh, fDisp, fTipo]);

  /* "/" shortcut */
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  /* customer debounce search */
  const searchCustomers = useCallback(async (query: string) => {
    if (!query.trim()) { setCustResults([]); return; }
    try {
      const r = await api<{ items: CustResult[] }>(`/customers?q=${encodeURIComponent(query)}&limit=10`);
      setCustResults(r.items);
    } catch { setCustResults([]); }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => searchCustomers(custQ), 250);
    return () => clearTimeout(t);
  }, [custQ, searchCustomers]);

  /* ---------- Derived values ---------- */
  const catSet = (() => {
    if (!fCat || !opts) return null;
    const ids = new Set([Number(fCat)]);
    let creció = true;
    while (creció) { creció = false; for (const c of opts.categories) if (c.parent_id != null && ids.has(c.parent_id) && !ids.has(c.id)) { ids.add(c.id); creció = true; } }
    return ids;
  })();
  const palabras = fold(q).split(/\s+/).filter(Boolean);
  const propia = (i: CatalogItem) => Number(i.stock) > 0;
  const proveedor = (i: CatalogItem) => i.availability?.state === "bajo_pedido" || Number(i.availability?.supplier_available ?? 0) > 0;
  const filtered = catalog.filter((item) => {
    if (palabras.length) {
      const hay = fold(`${item.name} ${item.code} ${item.brand || ""}`);
      if (!palabras.every((w) => hay.includes(w))) return false;
    }
    if (catSet && !(item.category_id != null && catSet.has(item.category_id))) return false;
    if (fTipo && item.item_type !== fTipo) return false;
    if (fWh === "proveedor" && !proveedor(item)) return false;
    if (fWh && fWh !== "proveedor" && !(Number(item.stock_by_wh?.[fWh] ?? 0) > 0)) return false;
    if (fDisp === "propia" && !propia(item)) return false;
    if (fDisp === "proveedor" && !proveedor(item)) return false;
    if (fDisp === "disponible" && !(propia(item) || proveedor(item) || item.item_type !== "producto")) return false;
    if (fDisp === "sin" && (item.item_type !== "producto" || propia(item) || proveedor(item))) return false;
    return true;
  });
  const hayFiltro = !!(fCat || fWh || fDisp || fTipo);
  const padres = (opts?.categories || []).filter((c) => c.parent_id == null);
  const hijas = (id: number) => (opts?.categories || []).filter((c) => c.parent_id === id);

  const currency = cart[0]?.currency ?? "CRC";
  const grossTotal = cart.reduce((s, l) => s + lineTotal(l), 0) + tip;
  const totalPaid = payments.reduce((s, p) => s + Number(p.amount || 0), 0);
  const change = Math.max(0, totalPaid - grossTotal);

  /* ---------- Cart helpers ---------- */
  /* precio en la moneda de la caja: el del producto ya viene convertido; el de una variante se convierte igual */
  const posPrice = (item: CatalogItem, v?: Variant) => {
    const base = Number(item.pos_price ?? item.price);
    if (v?.price == null) return base;
    const factor = Number(item.price) > 0 ? base / Number(item.price) : 1;
    return Math.round(Number(v.price) * factor * 100000) / 100000;
  };
  const addToCart = (item: CatalogItem, variantId?: number) => {
    if (item.sin_tc) {
      // el carrito suma en la moneda de la caja: sin tasa, un precio en otra moneda se sumaria como si fuera de la caja
      toast(`${item.name}: no hay tipo de cambio de ${item.currency} cargado. Cargalo en Ajustes → Facturación para cobrarlo en caja.`, "bad");
      setVariantItem(null);
      return;
    }
    const variant = variantId != null ? item.variants.find((v) => v.id === variantId) : undefined;
    const price = posPrice(item, variant);
    if (item.item_type === "producto" && sinExistencia(item.availability) && !cart.some((l) => l.product_id === item.id)) {
      toast(`${item.name}: sin existencia propia ni del proveedor. Se puede vender, pero no hay respaldo en bodega.`, "bad");
    }
    const name = variant ? `${item.name} — ${variant.name}` : item.name;
    const key = `${item.id}-${variantId ?? 0}`;
    setCart((prev) => {
      const idx = prev.findIndex((l) => l.key === key);
      if (idx >= 0) return prev.map((l, i) => (i === idx ? { ...l, quantity: l.quantity + 1 } : l));
      return [...prev, { key, product_id: item.id, variant_id: variantId, name, unit_price: price, tax_rate: Number(item.tax_rate), discount: 0, quantity: 1, currency: item.pos_currency || item.currency }];
    });
    setVariantItem(null);
  };

  const handleProductClick = (item: CatalogItem) => {
    if (item.variants.length > 0 && !item.sin_tc) { setVariantItem(item); return; }
    addToCart(item);
  };

  const updateLine = (key: string, patch: Partial<CartLine>) =>
    setCart((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const removeLine = (key: string) => setCart((prev) => prev.filter((l) => l.key !== key));

  /* ---------- Payment helpers ---------- */
  const setPaymentAmount = (idx: number, amount: number) => {
    if (idx < 0) return;
    setPayments((prev) => prev.map((p, i) => (i === idx ? { ...p, amount } : p)));
  };

  const efectivoIdx = payments.findIndex((p) => p.method === "efectivo");

  /* ---------- Cobrar ---------- */
  const cobrar = async () => {
    if (cart.length === 0) { toast("Agregá al menos un producto", "bad"); return; }
    if (docType === "FE" && !customer) { toast("Seleccioná un cliente para la factura", "bad"); return; }
    if (totalPaid < grossTotal) { toast("El monto cobrado no cubre el total", "bad"); return; }
    setBusy(true);
    try {
      const body = {
        doc_type: docType,
        ...(customer ? { customer_id: customer.id } : {}),
        lines: cart.map((l) => ({
          product_id: l.product_id,
          ...(l.variant_id != null ? { variant_id: l.variant_id } : {}),
          quantity: l.quantity,
          ...(l.discount > 0 ? { discount_value: l.discount } : {}),
        })),
        payments: payments
          .filter((p) => p.amount > 0)
          .map((p) => ({
            method: p.method,
            amount: p.amount,
            ...(p.reference ? { reference: p.reference } : {}),
          })),
        ...(tip > 0 ? { tip } : {}),
        emit: true,
      };
      const r = await api<SaleResult>("/pos/sale", { method: "POST", json: body });
      setReceipt(r);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Error al cobrar", "bad");
    } finally {
      setBusy(false);
    }
  };

  const resetSale = () => {
    setCart([]); setDocType("TE"); setCustomer(null); setCustQ("");
    setPayments([{ method: "efectivo", amount: 0, reference: "" }]); setReceipt(null);
    setTimeout(() => searchRef.current?.focus(), 60);
  };

  /* ---------- Render ---------- */
  return (
    <>
      <div className="page-head">
        <div>
          <div className="meta">POS · Punto de venta</div>
          <h1 className="h1">Cobrar</h1>
        </div>
        <div className="page-head__actions">
          <span className="meta" style={{ fontSize: 11 }}>
            Presioná{" "}
            <kbd style={{ fontFamily: "var(--mono)", fontSize: 10, padding: "2px 6px", border: "1px solid var(--hair-2)", borderRadius: 5 }}>/</kbd>
            {" "}para buscar · Enter agrega el primero
          </span>
        </div>
      </div>

      <div className="pos-grid">
        {/* ===== Left — product catalog ===== */}
        <Card flush>
          <div className="list-head">
            <div className="search" style={{ flex: 1 }}>
              <Icon d={I.search} size={16} />
              <input
                ref={searchRef}
                placeholder="Buscar por nombre o código…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && filtered.length > 0) {
                    handleProductClick(filtered[0]);
                    setQ("");
                  }
                }}
              />
              {q && <button className="x" onClick={() => setQ("")}><Icon d={I.x} size={13} /></button>}
            </div>
          </div>
          <div className="pos-filtros">
            <select className="select select--sm" value={fCat} onChange={(e) => setFCat(e.target.value)} aria-label="Categoría">
              <option value="">Todas las categorías</option>
              {padres.map((c) => [
                <option key={c.id} value={c.id}>{c.name}</option>,
                ...hijas(c.id).map((h) => <option key={h.id} value={h.id}>{"  · "}{h.name}</option>),
              ])}
            </select>
            <select className="select select--sm" value={fWh} onChange={(e) => setFWh(e.target.value)} aria-label="Bodega">
              <option value="">Todas las bodegas</option>
              {(opts?.warehouses || []).map((w) => <option key={w.id} value={String(w.id)}>{w.name}</option>)}
              <option value="proveedor">Proveedor (bajo pedido)</option>
            </select>
            <select className="select select--sm" value={fDisp} onChange={(e) => setFDisp(e.target.value)} aria-label="Disponibilidad">
              <option value="">Toda existencia</option>
              <option value="disponible">Disponible (propia o proveedor)</option>
              <option value="propia">En bodega propia</option>
              <option value="proveedor">Solo en el proveedor</option>
              <option value="sin">Sin existencia</option>
            </select>
            <select className="select select--sm" value={fTipo} onChange={(e) => setFTipo(e.target.value)} aria-label="Tipo">
              <option value="">Productos y servicios</option>
              <option value="producto">Solo productos</option>
              <option value="servicio">Solo servicios</option>
            </select>
            <span className="pos-filtros__n meta">{filtered.length} resultado{filtered.length !== 1 ? "s" : ""}</span>
            {(hayFiltro || q) && <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setQ(""); setFCat(""); setFWh(""); setFDisp(""); setFTipo(""); }}><Icon d={I.x} size={13} />Limpiar</button>}
          </div>

          {catLoading ? (
            <div style={{ padding: 44, display: "grid", placeItems: "center" }}>
              <span className="spinner" />
            </div>
          ) : filtered.length === 0 ? (
            <Empty hint="No hay productos que coincidan." />
          ) : (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(148px, 1fr))",
                gap: 1,
                background: "var(--hair)",
              }}
            >
              {filtered.slice(0, cuantos).map((item) => (
                <button
                  key={item.id}
                  onClick={() => handleProductClick(item)}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-2)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "var(--surface)")}
                  style={{
                    display: "flex", flexDirection: "column", gap: 7,
                    padding: "10px 11px", background: "var(--surface)",
                    border: 0, cursor: "pointer", textAlign: "left",
                    transition: "background .12s",
                  }}
                >
                  {item.image ? (
                    <div style={{ width: "100%", aspectRatio: "4/3", borderRadius: 8, overflow: "hidden", background: "var(--bg-2)" }}>
                      <img src={item.image} alt={item.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                    </div>
                  ) : (
                    <div style={{ width: "100%", aspectRatio: "4/3", borderRadius: 8, background: "var(--bg-2)", display: "grid", placeItems: "center", color: "var(--text-3)" }}>
                      <Icon d={I.products} size={22} />
                    </div>
                  )}
                  <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.3, flex: 1 }}>{item.name}</div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 4 }}>
                    <span className="money" style={{ color: "var(--crimson)", fontSize: 12.5, fontWeight: 700 }} title={item.sin_tc ? `Sin tipo de cambio de ${item.currency}: no se puede cobrar en caja` : item.currency !== item.pos_currency ? `${fmtMoney(item.price, item.currency)} convertido` : undefined}>
                      {fmtMoney(item.pos_price ?? item.price, item.pos_currency || item.currency)}
                    </span>
                    {item.item_type === "producto" && item.stock != null && (() => {
                      const own = Number(item.stock) > 0;
                      const prov = item.availability?.state === "bajo_pedido";
                      return (
                        <span title={availText(item.availability)} style={{
                          fontSize: 10, padding: "1px 5px", borderRadius: 999, fontFamily: "var(--mono)",
                          background: own ? "var(--ok-soft)" : prov ? "var(--warn-soft)" : "var(--bad-soft)",
                          color: own ? "var(--ok)" : prov ? "var(--warn)" : "var(--bad)",
                        }}>
                          {own ? Number(item.stock) : prov ? `prov. ${Number(item.availability?.supplier_available ?? 0)}` : "0"}
                        </span>
                      );
                    })()}
                    {item.variants.length > 0 && (
                      <span className="meta" style={{ fontSize: 9.5, color: "var(--text-3)" }}>
                        {item.variants.length} var.
                      </span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
          {!catLoading && filtered.length > cuantos && (
            <div style={{ padding: 12, textAlign: "center" }}>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setCuantos((n) => n + PAGINA)}>Ver más ({filtered.length - cuantos})</button>
            </div>
          )}
        </Card>

        {/* ===== Right — cart + payments ===== */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10, position: "sticky", top: "calc(var(--top-h) + var(--preview-h, 0px) + 20px)" }}>

          {/* Doc type + customer */}
          <Card>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className="meta" style={{ flex: "none" }}>Tipo</span>
              <div className="tabs" style={{ flex: 1 }}>
                {(["TE", "FE"] as const).map((t) => (
                  <button key={t} className={docType === t ? "is-active" : ""} onClick={() => setDocType(t)}>
                    {t === "TE" ? "Tiquete" : "Factura"}
                  </button>
                ))}
              </div>
            </div>

            {/* el cliente: obligatorio en factura, opcional en tiquete. El que crea un vendedor queda en su cartera. */}
              <div style={{ marginTop: 10, position: "relative" }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <div className="search" style={{ flex: 1, minWidth: 0 }}>
                  <Icon d={I.customers} size={15} />
                  <input
                    placeholder={docType === "FE" ? "Buscar cliente…" : "Cliente (opcional)…"}
                    value={customer ? customer.name : custQ}
                    onChange={(e) => { setCustomer(null); setCustQ(e.target.value); }}
                    onFocus={() => { if (customer) { setCustomer(null); setCustQ(""); } }}
                  />
                  {customer && (
                    <button className="x" onClick={() => { setCustomer(null); setCustQ(""); }}>
                      <Icon d={I.x} size={13} />
                    </button>
                  )}
                </div>
                {allows("crm.crear") && !customer && (
                  <button className="btn btn--soft btn--sm" style={{ flex: "none" }} title="Crear el cliente sin salir de la caja" onClick={() => setNuevoCliente(true)}>
                    <Icon d={I.plus} size={14} />Nuevo
                  </button>
                )}
                </div>
                {!customer && custResults.length > 0 && (
                  <div style={{
                    position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 20,
                    background: "var(--surface)", border: "1px solid var(--hair-2)",
                    borderRadius: 10, boxShadow: "var(--shadow)", overflow: "hidden",
                  }}>
                    {custResults.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => { setCustomer(c); setCustResults([]); setCustQ(""); }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-2)")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                        style={{ display: "block", width: "100%", padding: "9px 12px", border: 0, background: "transparent", textAlign: "left", cursor: "pointer" }}
                      >
                        <b style={{ fontSize: 13 }}>{c.name}</b>
                        {c.id_number && <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>{c.id_number}</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
          </Card>

          {/* Cart lines */}
          <Card
            flush
            title={cart.length > 0 ? `Carrito (${cart.length})` : "Carrito"}
            extra={
              cart.length > 0 ? (
                <button className="btn btn--ghost btn--sm" onClick={() => setCart([])}>
                  <Icon d={I.x} size={13} />Vaciar
                </button>
              ) : undefined
            }
          >
            {cart.length === 0 ? (
              <div style={{ padding: "24px 14px", textAlign: "center", color: "var(--text-3)", fontSize: 13 }}>
                Clic en un producto para agregar
              </div>
            ) : (
              <>
                {cart.map((line) => (
                  <div key={line.key} style={{ padding: "8px 12px", borderBottom: "1px solid var(--hair)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 6 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.3, flex: 1 }}>{line.name}</span>
                      <button className="x" style={{ flexShrink: 0 }} onClick={() => removeLine(line.key)}>
                        <Icon d={I.x} size={13} />
                      </button>
                    </div>
                    <div style={{ display: "flex", gap: 5, alignItems: "center", marginTop: 5 }}>
                      <button
                        className="btn btn--ghost btn--sm"
                        style={{ width: 26, height: 26, padding: 0, fontSize: 15 }}
                        onClick={() => updateLine(line.key, { quantity: Math.max(1, line.quantity - 1) })}
                      >−</button>
                      <input
                        className="input input--mono"
                        style={{ width: 44, height: 26, padding: "0 4px", textAlign: "center", fontSize: 12 }}
                        value={line.quantity}
                        onChange={(e) => updateLine(line.key, { quantity: Math.max(1, Number(e.target.value) || 1) })}
                      />
                      <button
                        className="btn btn--ghost btn--sm"
                        style={{ width: 26, height: 26, padding: 0, fontSize: 15 }}
                        onClick={() => updateLine(line.key, { quantity: line.quantity + 1 })}
                      >+</button>
                      <span className="muted" style={{ fontSize: 10, marginLeft: 2 }}>%dto</span>
                      <input
                        className="input input--mono"
                        style={{ width: 42, height: 26, padding: "0 4px", fontSize: 12 }}
                        type="number" min={0} max={100} step={1}
                        value={line.discount || ""}
                        placeholder="0"
                        onChange={(e) => updateLine(line.key, { discount: Math.min(100, Math.max(0, Number(e.target.value) || 0)) })}
                      />
                      <span className="money" style={{ marginLeft: "auto", fontSize: 12.5, fontWeight: 700 }}>
                        {fmtMoney(lineTotal(line), line.currency)}
                      </span>
                    </div>
                  </div>
                ))}

                {/* Totals preview */}
                <div style={{ padding: "8px 12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--text-3)", marginBottom: 4 }}>
                    <span>Subtotal s/IVA</span>
                    <span className="money">{fmtMoney(cart.reduce((s, l) => s + l.unit_price * l.quantity * (1 - l.discount / 100), 0), currency)}</span>
                  </div>
                  {tip > 0 && (
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--text-3)", marginBottom: 4 }}>
                      <span>Propina</span><span className="money">{fmtMoney(tip, currency)}</span>
                    </div>
                  )}
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 17, fontWeight: 700, paddingTop: 7, borderTop: "1px solid var(--hair)" }}>
                    <span>Total</span>
                    <span className="money" style={{ color: "var(--crimson)" }}>{fmtMoney(grossTotal, currency)}</span>
                  </div>
                </div>
              </>
            )}
          </Card>

          {/* Payments */}
          {cart.length > 0 && (
            <Card title="Cobro">
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {payments.map((p, idx) => (
                  <div key={idx} style={{ display: "flex", gap: 5, alignItems: "center" }}>
                    <select
                      className="select"
                      style={{ flex: 1, height: 32, padding: "0 8px", fontSize: 12 }}
                      value={p.method}
                      onChange={(e) => setPayments((prev) => prev.map((x, i) => (i === idx ? { ...x, method: e.target.value } : x)))}
                    >
                      {METHODS.map((m) => (
                        <option key={m} value={m}>{m[0].toUpperCase() + m.slice(1)}</option>
                      ))}
                    </select>
                    <input
                      className="input input--mono"
                      type="number" min={0} step={100}
                      style={{ width: 96, height: 32, padding: "0 8px", fontSize: 12 }}
                      value={p.amount || ""}
                      placeholder="Monto"
                      onChange={(e) => setPaymentAmount(idx, Number(e.target.value) || 0)}
                    />
                    {p.method !== "efectivo" && (
                      <input
                        className="input"
                        style={{ flex: 1, height: 32, padding: "0 8px", fontSize: 11 }}
                        value={p.reference}
                        placeholder="Ref."
                        onChange={(e) => setPayments((prev) => prev.map((x, i) => (i === idx ? { ...x, reference: e.target.value } : x)))}
                      />
                    )}
                    {payments.length > 1 && (
                      <button className="x" onClick={() => setPayments((prev) => prev.filter((_, i) => i !== idx))}>
                        <Icon d={I.x} size={12} />
                      </button>
                    )}
                  </div>
                ))}

                <button
                  className="btn btn--ghost btn--sm"
                  style={{ alignSelf: "flex-start" }}
                  onClick={() => setPayments((prev) => [...prev, { method: "efectivo", amount: 0, reference: "" }])}
                >
                  <Icon d={I.plus} size={13} />Agregar forma de pago
                </button>

                {/* Quick cash buttons */}
                {efectivoIdx >= 0 && (
                  <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
                    <button className="btn btn--soft btn--sm" onClick={() => setPaymentAmount(efectivoIdx, grossTotal)}>
                      Exacto
                    </button>
                    {QUICK_BASES.map((base) => {
                      const amt = Math.ceil(grossTotal / base) * base;
                      if (amt <= grossTotal) return null;
                      return (
                        <button key={base} className="btn btn--soft btn--sm" onClick={() => setPaymentAmount(efectivoIdx, amt)}>
                          {fmtMoney(amt, currency)}
                        </button>
                      );
                    })}
                  </div>
                )}

                {/* Vuelto */}
                {totalPaid > 0 && (
                  <div style={{
                    display: "flex", justifyContent: "space-between",
                    padding: "8px 12px", borderRadius: 8,
                    background: change > 0 ? "var(--ok-soft)" : totalPaid >= grossTotal ? "var(--surface-2)" : "var(--bad-soft)",
                  }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>Vuelto</span>
                    <span className="money" style={{
                      fontWeight: 700,
                      color: change > 0 ? "var(--ok)" : totalPaid >= grossTotal ? "var(--text-2)" : "var(--bad)",
                    }}>
                      {fmtMoney(change, currency)}
                    </span>
                  </div>
                )}

                <button
                  className="btn btn--crimson"
                  style={{ marginTop: 2, fontSize: 15 }}
                  disabled={busy || cart.length === 0}
                  onClick={cobrar}
                >
                  {busy ? <span className="spinner" style={{ width: 15, height: 15 }} /> : <Icon d={I.pay} />}
                  Cobrar {fmtMoney(grossTotal, currency)}
                </button>
              </div>
            </Card>
          )}
        </div>
      </div>

      {nuevoCliente && (
        <NuevoCliente
          needId={docType === "FE"}
          emailHint={docType === "FE" ? "Ahí le llega la factura electrónica" : "Para mandarle el comprobante"}
          onClose={() => setNuevoCliente(false)}
          onCreated={(c) => { setCustomer({ id: c.id, name: c.name, id_number: c.id_number ?? null }); setCustQ(""); setCustResults([]); setNuevoCliente(false); }}
        />
      )}

      {/* ===== Variant chooser modal ===== */}
      {variantItem && (
        <Modal title={variantItem.name} onClose={() => setVariantItem(null)}>
          <p className="muted" style={{ fontSize: 13 }}>Elija una variante para agregar al carrito:</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {variantItem.variants.map((v) => (
              <button
                key={v.id}
                className="btn btn--soft"
                style={{ justifyContent: "space-between" }}
                onClick={() => { addToCart(variantItem, v.id); setQ(""); }}
              >
                <span>{v.name}</span>
                <span className="money" style={{ fontSize: 13 }}>
                  {fmtMoney(posPrice(variantItem, v), variantItem.pos_currency || variantItem.currency)}
                </span>
              </button>
            ))}
          </div>
        </Modal>
      )}

      {/* ===== Receipt modal ===== */}
      {receipt && (
        <Modal
          title="Venta registrada"
          onClose={resetSale}
          foot={
            <>
              <AuthLink path={`/invoices/${receipt.invoice_id}/pdf`} className="btn btn--ghost btn--sm">
                Imprimir
              </AuthLink>
              <Link className="btn btn--soft" to={`/facturas/${receipt.invoice_id}`}>
                Ver comprobante
              </Link>
              <button className="btn btn--crimson" onClick={resetSale}>
                Nueva venta
              </button>
            </>
          }
        >
          <div style={{ textAlign: "center", padding: "12px 0 20px" }}>
            <div className="meta">Comprobante emitido</div>
            <div style={{ fontFamily: "var(--display)", fontSize: 34, fontWeight: 700, letterSpacing: "-0.02em", margin: "6px 0 4px" }}>
              {receipt.number}
            </div>
            <div className="money" style={{ fontSize: 28, color: "var(--crimson)", fontWeight: 700 }}>
              {fmtMoney(receipt.total, currency)}
            </div>
            {receipt.change > 0 && (
              <div style={{ marginTop: 12, display: "inline-block", padding: "8px 18px", borderRadius: 8, background: "var(--ok-soft)", color: "var(--ok)", fontWeight: 700, fontSize: 14 }}>
                Vuelto: {fmtMoney(receipt.change, currency)}
              </div>
            )}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
              <span className="muted">Tipo</span>
              <span>{receipt.doc_type === "TE" ? "Tiquete electrónico" : "Factura electrónica"}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
              <span className="muted">Estado DGT</span>
              <span>{receipt.einvoice}</span>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
