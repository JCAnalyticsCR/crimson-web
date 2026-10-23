import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { GalleryField, type GalleryImage } from "../../ui/MediaPicker";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import { TrashButton } from "../../ui/TrashButton";
import { AvailBadge, availText, qty, type Availability as Avail } from "../../ui/Availability";
import "./productos.css";

/* "Agregar a cotización" (pedido 21/10): la vendedora marca artículos en el catálogo y los manda a una cotización nueva
   o a una suya abierta; el editor los agrega con su precio, IVA y unidad (lo mismo que su buscador) y ella guarda. */
type QuoteRow = { id: number; number: string; status: string; customer_name?: string | null; total?: string | number; currency?: string };
function AgregarACotizacion({ ids, onClose }: { ids: number[]; onClose: () => void }) {
  const { allows } = useSession();
  const nav = useNavigate();
  const [qs, setQs] = useState<QuoteRow[] | null>(null);
  useEffect(() => {
    api<{ items: QuoteRow[] } | QuoteRow[]>("/quotes?limit=30")
      .then((r) => setQs((Array.isArray(r) ? r : r.items).filter((x) => ["creado", "enviada"].includes(x.status))))
      .catch(() => setQs([]));
  }, []);
  const ir = (destino: string) => { onClose(); nav(`${destino}?productos=${ids.join(",")}`); };
  return (
    <Modal title={`Agregar ${ids.length} artículo(s) a una cotización`} onClose={onClose} foot={<button className="btn btn--ghost" onClick={onClose}>Cancelar</button>}>
      {allows("sales.crear") && <button className="btn btn--crimson" style={{ marginBottom: 12 }} onClick={() => ir("/cotizaciones/nueva")}><Icon d={I.plus} />Cotización nueva</button>}
      {allows("sales.editar") && (
        <>
          <div className="meta" style={{ marginBottom: 6 }}>O agregarlos a una abierta</div>
          {qs === null ? <Loading /> : qs.length ? (
            <div style={{ display: "grid", gap: 6, maxHeight: 320, overflowY: "auto" }}>
              {qs.map((x) => (
                <button key={x.id} type="button" className="btn btn--ghost" style={{ justifyContent: "space-between", height: "auto", padding: "8px 10px" }} onClick={() => ir(`/cotizaciones/${x.id}`)}>
                  <span><b className="mono">{x.number}</b> · {x.customer_name || "Sin cliente"}</span>
                  {x.total != null && <span className="money">{fmtMoney(x.total, x.currency || "CRC")}</span>}
                </button>
              ))}
            </div>
          ) : <p className="muted" style={{ margin: 0 }}>No tiene cotizaciones abiertas.</p>}
        </>
      )}
    </Modal>
  );
}

type Tax = { id: number; name: string; rate: number };
type Named = { id: number; name: string };
type Variant = { id?: number; name: string; code: string; price: string; active: boolean; options: Record<string, string> };
/* la ficha: la funcion unica del API (interno, con lo libre por bodega de venta) + lo que ve el cliente en la tienda */
type Availability = Avail & { min_stock: number; label_publico?: string | null; bodegas?: { nombre: string; disponible: number | string }[] };
type Full = Product & {
  cost: number | null; cost_currency: string; margin_pct: number | null; brand: string | null; model: string | null; supplier_sku: string | null; supplier_stock: number | null;
  tax_ids: number[]; description_invoice: string | null; description_store: string | null; min_stock: number; images: GalleryImage[];
  supplier_id: number | null; weight_kg: number | null; registration_number: string | null; cabys_description: string | null; tariff_code: string | null; active: boolean;
  warehouse_id: number | null;
};
type Cabys = { code: string; description: string; tax_rate: number | null; categories: string[] };
/* Costo y margen (solo roles que ven costos): de dónde sale el costo y el margen real sobre la venta. */
type CostInfo = {
  cost?: number | null; cost_currency?: string; cost_source?: "proveedor" | "propio" | null; cost_supplier?: string | null; cost_date?: string | null;
  cost_active?: boolean | null; cost_crc?: number | null; margin_real_pct?: number | null; margin_provisional?: boolean | null; supplier_offers?: number | null; margin_hold?: boolean | null;
  /* precio general para aliados (services/ally_pricing): solo llega a quien ve costos */
  ally_price?: number | null; ally_price_source?: "precio_general" | "recargo" | "precio_aliado" | null;
};
type Row = Product & CostInfo & { images?: GalleryImage[]; warehouse_id?: number | null };
type Offer = {
  supplier_item_id: number; supplier: string | null; code: string; name: string; model: string | null; cost?: number | null; currency?: string; cost_crc?: number | null;
  stock_proveedor: number | null; updated_at: string | null; active: boolean; best: boolean;
};
const marginTone = (m: number) => (m < 20 ? "var(--bad)" : m < 30 ? "var(--warn)" : "var(--ok)");

const blank = {
  name: "", code: "", item_type: "producto", price: "0", currency: "CRC", cabys_code: "", description_invoice: "", description_store: "", unit: "Unid",
  show_on_web: false, min_stock: 0, tax_ids: [] as number[], category_id: "" as string, supplier_id: "" as string, weight_kg: "", images: [] as GalleryImage[],
  registration_number: "", tariff_code: "", cost: "", cost_currency: "USD", margin_pct: "", brand: "", model: "", supplier_sku: "",
  cabys_description: "", warehouse_id: "" as string,
};
type Edit = typeof blank & { id?: number };

const TABS = [["general", "General"], ["imagenes", "Imágenes"], ["variantes", "Variantes"]] as const;

export default function Products() {
  const { toast, allows } = useSession();
  const [params, setParams] = useSearchParams();
  const [itemsSt, setItems] = useState<Row[] | null>(null); // null = cargando
  const items = itemsSt ?? [];
  const [taxes, setTaxes] = useState<Tax[]>([]);
  const [cats, setCats] = useState<Named[]>([]);
  const [suppliers, setSuppliers] = useState<Named[]>([]);
  /* ?q= filtra al abrir (los enlaces de Aprobaciones y del resumen de margen llegan con el codigo) */
  const [q, setQ] = useState(params.get("q") || "");
  const [tab, setTab] = useState<string>("general");
  const [edit, setEdit] = useState<Edit | null>(params.get("nuevo") ? { ...blank } : null);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [newCat, setNewCat] = useState<string | null>(null);
  const [avail, setAvail] = useState<Availability | null>(null);
  /* "Aquí no puedo filtrar nada" fue el reclamo de Andrés con 200 productos del proveedor encima. */
  const [f, setF] = useState({ item_type: "", category_id: "", brand: "", supplier_id: "", web: "", sin_cabys: false, warehouse_id: "", proveedor: "" });
  const [offers, setOffers] = useState<Offer[] | null>(null);
  const [costOf, setCostOf] = useState<CostInfo | null>(null);
  const [opts, setOpts] = useState<{ brands: string[]; categories: Named[]; suppliers: Named[]; sin_cabys: number; warehouses: Named[] } | null>(null);
  const [toggling, setToggling] = useState<number | null>(null);
  const verPrecios = allows("catalog.precios");
  const verCostos = allows("catalog.costos"); // el costo del proveedor es solo de administración
  const cotiza = allows("sales.crear") || allows("sales.editar");
  const publica = allows("catalog.publicar"); // la tienda en línea es solo del administrador
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [aCot, setACot] = useState(false);
  const [ajuste, setAjuste] = useState(false);
  /* precio en linea (pedido 23/10): el admin lo corrige desde la lista; mismo permiso que la ficha (catalog.editar) */
  const editaPrecio = verPrecios && allows("catalog.editar");
  const onPrice = (row: Row) => setItems((xs) => xs && xs.map((x) => (x.id === row.id ? { ...x, ...row } : x)));
  /* tablet y teléfono: los filtros se pliegan detrás de un botón (en escritorio siempre a la vista) */
  const [verFiltros, setVerFiltros] = useState(false);
  const marcar = (id: number) => setSel((s0) => { const n = new Set(s0); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: "50" });
    if (q) qs.set("q", q);
    if (f.item_type) qs.set("item_type", f.item_type);
    if (f.category_id) qs.set("category_id", f.category_id);
    if (f.brand) qs.set("brand", f.brand);
    if (f.supplier_id) qs.set("supplier_id", f.supplier_id);
    if (f.web) qs.set("web", f.web);
    if (f.sin_cabys) qs.set("sin_cabys", "true");
    if (f.warehouse_id) qs.set("warehouse_id", f.warehouse_id);
    if (f.proveedor) qs.set("proveedor", f.proveedor);
    return api<{ items: Row[] }>(`/products?${qs}`).then((r) => setItems(r.items));
  }, [q, f]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  useEffect(() => {
    api<Tax[]>("/taxes").then(setTaxes);
    api<Named[]>("/categories").then(setCats).catch(() => setCats([]));
    api<Named[]>("/suppliers").then(setSuppliers).catch(() => setSuppliers([]));
    api<typeof opts>("/products/meta/filters").then(setOpts).catch(() => setOpts(null));
  }, []);

  const open = async (id: number) => {
    const f = await api<Full & CostInfo>(`/products/${id}`);
    setCostOf(verCostos ? f : null);
    setOffers(null);
    if (verCostos) api<{ offers: Offer[] }>(`/supplier-catalog/offers?product_id=${id}`).then((r) => setOffers(r.offers)).catch(() => setOffers([]));
    setEdit({
      id: f.id, name: f.name, code: f.code, item_type: f.item_type, price: String(f.price), currency: f.currency, cabys_code: f.cabys_code || "",
      description_invoice: f.description_invoice || "", description_store: f.description_store || "", unit: f.unit, show_on_web: f.show_on_web, min_stock: f.min_stock,
      tax_ids: f.tax_ids, category_id: f.category_id ? String(f.category_id) : "", supplier_id: f.supplier_id ? String(f.supplier_id) : "",
      weight_kg: f.weight_kg != null ? String(f.weight_kg) : "", images: f.images || [], registration_number: f.registration_number || "", tariff_code: f.tariff_code || "",
      cost: f.cost != null ? String(f.cost) : "", cost_currency: f.cost_currency || "USD", margin_pct: f.margin_pct != null ? String(f.margin_pct) : "",
      brand: f.brand || "", model: f.model || "", supplier_sku: f.supplier_sku || "",
      cabys_description: f.cabys_description || "", warehouse_id: f.warehouse_id ? String(f.warehouse_id) : "",
    });
    setTab("general");
    setAvail(null);
    api<Availability>(`/products/${id}/availability`).then(setAvail).catch(() => setAvail(null));
    api<(Omit<Variant, "price"> & { price: string | number | null })[]>(`/products/${id}/variants`).then((vs) => setVariants(vs.filter((v) => v.active).map((v) => ({ ...v, price: v.price == null ? "" : String(v.price) }))));
  };

  const save = async () => {
    if (!edit) return;
    try {
      const { id, ...b } = edit;
      const body: Record<string, unknown> = {
        ...b, price: Number(b.price), category_id: b.category_id ? Number(b.category_id) : null, supplier_id: b.supplier_id ? Number(b.supplier_id) : null,
        weight_kg: b.weight_kg ? Number(b.weight_kg) : null, cabys_code: b.cabys_code || null, registration_number: b.registration_number || null, tariff_code: b.tariff_code || null,
        cost: b.cost === "" ? null : Number(b.cost), margin_pct: b.margin_pct === "" ? null : Number(b.margin_pct),
        brand: b.brand || null, model: b.model || null, supplier_sku: b.supplier_sku || null,
        cabys_description: b.cabys_description || null, warehouse_id: b.warehouse_id ? Number(b.warehouse_id) : null,
      };
      // quien no ve costos no los manda (la API tampoco los aceptaria): asi guardar nunca borra el costo
      if (!verCostos) { delete body.cost; delete body.cost_currency; delete body.margin_pct; }
      const saved = await api<{ id: number }>(id ? `/products/${id}` : "/products", { method: id ? "PUT" : "POST", json: body });
      if (id || variants.length) {
        await api(`/products/${saved.id}/variants`, { method: "PUT", json: variants.map((v) => ({ id: v.id, name: v.name, code: v.code, price: v.price === "" ? null : Number(v.price), options: v.options, active: v.active })) });
      }
      toast("Producto guardado"); setEdit(null); setVariants([]); setParams({}); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  /* ?ficha=ID abre la ficha del producto una vez (Aprobaciones → Precios por revisar: verificar el costo antes de
     aplicar el precio sugerido) */
  const fichaAbierta = useRef(false);
  useEffect(() => {
    const id = Number(params.get("ficha"));
    if (fichaAbierta.current || !Number.isInteger(id) || id <= 0) return;
    fichaAbierta.current = true;
    open(id).catch((e) => toast(e instanceof Error ? e.message : "No se pudo abrir el producto", "bad"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const copyLink = async () => {
    if (!edit?.id) return;
    try {
      const r = await api<{ url: string; ready: boolean; problems: string[] }>(`/products/${edit.id}/link`);
      await navigator.clipboard.writeText(r.url);
      toast(r.problems.length ? `Link copiado. Ojo: ${r.problems[0]}` : "Link del producto copiado", r.ready ? "ok" : "bad");
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo copiar", "bad"); }
  };

  const createCat = async () => {
    if (!newCat?.trim() || !edit) return;
    try { const c = await api<Named>("/categories", { method: "POST", json: { name: newCat.trim() } }); setCats([...cats, c]); setEdit({ ...edit, category_id: String(c.id) }); setNewCat(null); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  /* Buscador CABYS por descripción: el API hace de proxy a Hacienda (con caché). Los 218 productos de la
     lista del proveedor entraron sin CABYS y escribir 13 dígitos de memoria no es realista. */
  const cabysHits = useRef<Record<string, Cabys>>({});
  const searchCabys = useCallback(async (text: string): Promise<LookupItem[]> => {
    const t = text.trim();
    if (t.length < 3) return [];
    const onlyDigits = /^\d{13}$/.test(t);
    try {
      const rows = await api<Cabys[]>(`/cabys?${onlyDigits ? `codigo=${t}` : `q=${encodeURIComponent(t)}&top=20`}`);
      rows.forEach((r) => { cabysHits.current[r.code] = r; });
      if (!rows.length) return [{ id: -1, label: "Sin resultados en el CABYS", hint: "Pruebe con otra palabra (cámara, cable, switch…)" }];
      return rows.map((r) => ({ id: Number(r.code), label: r.description, hint: `${r.code} · IVA ${r.tax_rate ?? "?"}%` }));
    } catch (e) {
      return [{ id: -1, label: e instanceof Error ? e.message : "Hacienda no respondió", hint: "Puede escribir el código de 13 dígitos a mano" }];
    }
  }, []);
  const pickCabys = (it: LookupItem) => {
    if (it.id < 0 || !edit) return;
    const code = String(it.id).padStart(13, "0");
    const hit = cabysHits.current[code];
    const tax = hit?.tax_rate != null ? taxes.find((t) => Number(t.rate) === Number(hit.tax_rate)) : undefined;
    setEdit({ ...edit, cabys_code: code, cabys_description: hit?.description || it.label, tax_ids: tax ? [tax.id] : edit.tax_ids });
    if (hit?.tax_rate != null) toast(tax ? `CABYS ${code} · impuesto sugerido ${hit.tax_rate}% aplicado` : `CABYS ${code} · Hacienda sugiere IVA ${hit.tax_rate}%, pero no está configurado`, tax ? "ok" : "bad");
  };
  const whName = (id: number | null | undefined) => (id ? opts?.warehouses?.find((w) => w.id === id)?.name || "—" : "—");

  const canEdit = edit?.id ? allows("catalog.editar") : allows("catalog.crear");
  const setV = (i: number, patch: Partial<Variant>) => setVariants(variants.map((v, j) => (j === i ? { ...v, ...patch } : v)));
  const thumb = (p: { images?: GalleryImage[] }) => (p.images || []).find((x) => x.main)?.url || p.images?.[0]?.url;

  /* El interruptor de la columna Web publica o quita el producto de la tienda sin abrir la ficha:
     es lo que se hace todos los dias y no deberia costar cuatro clics. */
  const toggleWeb = async (pr: Product) => {
    if (!publica) return;
    setToggling(pr.id);
    setItems((xs) => xs && xs.map((x) => (x.id === pr.id ? { ...x, show_on_web: !pr.show_on_web } : x)));
    try { await api(`/products/${pr.id}/web`, { method: "PATCH", json: { show_on_web: !pr.show_on_web } }); toast(pr.show_on_web ? "Quitado de la tienda" : "Publicado en la tienda"); }
    catch (e) { setItems((xs) => xs && xs.map((x) => (x.id === pr.id ? { ...x, show_on_web: pr.show_on_web } : x))); toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setToggling(null); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo</div><h1 className="h1">Productos & Servicios</h1></div>
        <div className="page-head__actions">{allows("catalog.editar") && !!opts?.sin_cabys && <Link className="btn btn--soft" to="/productos/cabys" title="Sugerencias de CABYS para confirmar en bloque">Completar CABYS ({opts.sin_cabys})</Link>}{verCostos && allows("catalog.editar") && <button className="btn btn--soft" onClick={() => setAjuste(true)} title="Subir o bajar precios de lo filtrado o marcado, con vista previa">Ajustar precios</button>}{allows("catalog.crear") && <button className="btn btn--crimson" onClick={() => { setEdit({ ...blank, tax_ids: taxes[0] ? [taxes[0].id] : [] }); setVariants([]); setTab("general"); }}><Icon d={I.plus} />Crear producto</button>}</div>
      </div>
      <Card flush>
        <div className="list-head" style={{ flexWrap: "wrap" }}>
          <div className="search" style={{ maxWidth: 340 }}><Icon d={I.search} size={16} /><input placeholder="Nombre, código, CABYS o modelo…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <button type="button" className={`btn btn--sm prd-filtros-btn ${verFiltros ? "btn--crimson" : "btn--ghost"}`} onClick={() => setVerFiltros(!verFiltros)} aria-expanded={verFiltros}><Icon d={I.settings} size={14} />Filtros{[f.item_type, f.category_id, f.brand, f.supplier_id, f.web, f.warehouse_id, f.proveedor].filter(Boolean).length + (f.sin_cabys ? 1 : 0) ? ` (${[f.item_type, f.category_id, f.brand, f.supplier_id, f.web, f.warehouse_id, f.proveedor].filter(Boolean).length + (f.sin_cabys ? 1 : 0)})` : ""}</button>
          <div className={`prd-filtros${verFiltros ? " is-open" : ""}`}>
          <select className="select select--sm" value={f.item_type} onChange={(e) => setF({ ...f, item_type: e.target.value })}><option value="">Todo tipo</option><option value="producto">Productos</option><option value="servicio">Servicios</option></select>
          <select className="select select--sm" value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}><option value="">Toda categoría</option>{(opts?.categories || cats).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          {!!opts?.brands.length && <select className="select select--sm" value={f.brand} onChange={(e) => setF({ ...f, brand: e.target.value })}><option value="">Toda marca</option>{opts.brands.map((b) => <option key={b} value={b}>{b}</option>)}</select>}
          <select className="select select--sm" value={f.supplier_id} onChange={(e) => setF({ ...f, supplier_id: e.target.value })}><option value="">Todo proveedor</option>{(opts?.suppliers || suppliers).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
          {publica && <select className="select select--sm" value={f.web} onChange={(e) => setF({ ...f, web: e.target.value })}><option value="">En la tienda: todos</option><option value="true">Publicados</option><option value="false">Ocultos</option></select>}
          {!!opts?.warehouses?.length && <select className="select select--sm" value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })} title="Bodega o inventario de origen"><option value="">Toda bodega</option>{opts.warehouses.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>}
          <select className="select select--sm" value={f.proveedor} onChange={(e) => setF({ ...f, proveedor: e.target.value })} title="Vinculado a la lista de un proveedor (Inventarios → Proveedores) o con proveedor asignado"><option value="">Con y sin proveedor</option><option value="con">Con proveedor</option><option value="sin">Sin proveedor</option><option value="vinculado">Vinculado a la lista del proveedor</option><option value="sin_vincular">Con proveedor sin vincular (su existencia no cuenta)</option></select>
          {!!opts?.sin_cabys && <button className={`btn btn--sm ${f.sin_cabys ? "btn--crimson" : "btn--ghost"}`} onClick={() => setF({ ...f, sin_cabys: !f.sin_cabys })} title="Sin CABYS no se puede facturar">Sin CABYS ({opts.sin_cabys})</button>}
          </div>
          {(q || f.item_type || f.category_id || f.brand || f.supplier_id || f.web || f.sin_cabys || f.warehouse_id || f.proveedor) && <button className="btn btn--ghost btn--sm" onClick={() => { setQ(""); setF({ item_type: "", category_id: "", brand: "", supplier_id: "", web: "", sin_cabys: false, warehouse_id: "", proveedor: "" }); }}><Icon d={I.x} size={14} />Limpiar</button>}
          <button className="btn btn--ghost btn--sm" onClick={load} style={{ marginLeft: "auto" }}><Icon d={I.refresh} /></button>
        </div>
        {cotiza && sel.size > 0 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "8px 18px", background: "var(--bg-2)", borderBottom: "1px solid var(--hair)" }}>
            <b>{sel.size} seleccionado(s)</b>
            <button className="btn btn--crimson btn--sm" onClick={() => setACot(true)}><Icon d={I.plus} />Agregar a cotización</button>
            <button className="btn btn--ghost btn--sm" onClick={() => setSel(new Set())}>Quitar selección</button>
          </div>
        )}
        {itemsSt === null ? <Loading /> : items.length === 0 ? <Empty hint="Nada con esos filtros. Pruebe limpiarlos o cree el producto." /> : (
          <>
          {/* Telefono (pedido 21/10): tarjetas con la foto grande del producto en vez de la tabla ancha, donde la
              miniatura de 36 px quedaba fuera de vista al deslizar. La tabla sigue igual en tablet y escritorio. */}
          <ul className="prd-cards">{items.map((p) => (
            <li key={p.id} className="prd-card">
              {cotiza && <input type="checkbox" className="prd-card__sel" aria-label={`Agregar ${p.name} a una cotización`} checked={sel.has(p.id)} onChange={() => marcar(p.id)} />}
              <button type="button" className="prd-card__main" onClick={() => open(p.id)}>
                {thumb(p) ? <img className="prd-card__foto" src={thumb(p)} alt="" loading="lazy" /> : <span className="prd-card__foto prd-card__foto--none"><Icon d={I.products} size={22} /></span>}
                <span className="prd-card__txt">
                  <b>{p.name}</b>
                  <small className="mono">{p.code}{p.item_type === "servicio" ? " · servicio" : ""}</small>
                  <small>{p.item_type === "producto" ? availText(p.availability) : "Servicio"}</small>
                  <small>CABYS <span className="mono">{p.cabys_code || "—"}</span> · {whName(p.warehouse_id)}</small>
                  {verPrecios && !editaPrecio && <span className="money prd-card__precio">{fmtMoney(p.price, p.currency)}</span>}
                </span>
              </button>
              {editaPrecio && <span className="prd-card__precio"><PriceInline p={p} onSaved={onPrice} /></span>}
              {/* lo mismo que la tabla deja hacer (con los mismos permisos): costo y margen, publicar en la web y papelera */}
              <span className="prd-card__acc">
                {publica && <button className={`switch${p.show_on_web ? " is-on" : ""}`} disabled={toggling === p.id} onClick={() => toggleWeb(p)} title={p.show_on_web ? "Visible en la tienda · clic para quitarlo" : "Oculto · clic para publicarlo"} aria-label="Publicar en la web" aria-pressed={p.show_on_web}><i /></button>}
                <TrashButton kind="product" id={p.id} number={p.code || p.name} onDone={load} />
              </span>
              {verCostos && <div className="prd-card__costos"><span><small>Costo</small><CostCell p={p} /></span><span><small>Margen</small><MarginCell p={p} /></span><span><small>Aliado</small>{p.ally_price != null ? <span className="money">{fmtMoney(p.ally_price, p.currency)}</span> : <span className="muted">—</span>}</span></div>}
            </li>
          ))}</ul>
          <div className={`prd-tabla${verCostos ? " prd-dense" : ""}`} style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr>{cotiza && <th style={{ width: 32 }} title="Marcá para agregar a una cotización" />}<th style={{ width: 48 }} /><th>Código</th><th>Nombre</th><th>Tipo</th><th>CABYS</th><th>Bodega</th><th className="num" title="Libre en bodegas de venta (sin las de garantías) menos lo reservado">Propio</th><th className="num" title="Existencia del proveedor vinculado (Eurocomp)">Proveedor</th><th title="Cuándo se consultó al proveedor y cuándo cambió de verdad su existencia o su costo">Proveedor consultado</th>{verCostos && <th className="num" title="Costo sin IVA: del proveedor vinculado (con la fecha de su lista) o propio">Costo</th>}{verPrecios && <th className="num" title="Sin IVA">Precio de venta</th>}{verCostos && <th className="num" title="Margen sobre la venta: (precio − costo) ÷ precio">Margen</th>}{verCostos && <th className="num" title="Precio general para aliados, sin IVA (recargo sobre el costo o precio fijo)">Precio aliado</th>}<th>IVA</th>{publica && <th>Web</th>}<th /></tr></thead>
            <tbody>{items.map((p) => (
              <tr key={p.id}>
                {cotiza && <td><input type="checkbox" aria-label={`Agregar ${p.name} a una cotización`} checked={sel.has(p.id)} onChange={() => marcar(p.id)} /></td>}
                <td>{thumb(p) ? <span style={{ display: "block", width: 36, height: 36, borderRadius: 8, background: `center/cover no-repeat url("${thumb(p)}")`, border: "1px solid var(--hair)" }} /> : <span style={{ display: "grid", placeItems: "center", width: 36, height: 36, borderRadius: 8, background: "var(--bg-2)", color: "var(--text-3)" }}><Icon d={I.products} size={16} /></span>}</td>
                <td className="mono muted">{p.code}</td><td style={{ fontWeight: 600 }}>{p.name}</td><td className="muted" style={{ textTransform: "capitalize" }}>{p.item_type}</td><td className="mono muted">{p.cabys_code || "—"}</td><td className="muted" style={{ fontSize: 13 }}>{whName(p.warehouse_id)}</td><StockCells a={p.availability} />{verCostos && <td className="num"><CostCell p={p} /></td>}{verPrecios && <td className="num money">{editaPrecio ? <PriceInline p={p} onSaved={onPrice} /> : fmtMoney(p.price, p.currency)}</td>}{verCostos && <td className="num" style={{ whiteSpace: "nowrap" }}><MarginCell p={p} /></td>}{verCostos && <td className="num money">{p.ally_price != null ? fmtMoney(p.ally_price, p.currency) : <span className="muted">—</span>}</td>}<td className="muted">{p.tax_rate ?? "—"}%</td>{publica && <td><button className={`switch${p.show_on_web ? " is-on" : ""}`} disabled={toggling === p.id} onClick={() => toggleWeb(p)} title={p.show_on_web ? "Visible en la tienda · clic para quitarlo" : "Oculto · clic para publicarlo"} aria-pressed={p.show_on_web}><i /></button></td>}
                <td className="num row-actions"><span className="row-actions__in"><button className="btn btn--ghost btn--sm" onClick={() => open(p.id)}>Ver</button><TrashButton kind="product" id={p.id} number={p.code || p.name} onDone={load} /></span></td>
              </tr>
            ))}</tbody>
          </table>
          </div>
          </>
        )}
      </Card>
      {aCot && <AgregarACotizacion ids={[...sel]} onClose={() => setACot(false)} />}
      {ajuste && <AjustePrecios filtro={{ q, ...f }} ids={[...sel]} cats={opts?.categories || cats} onClose={() => setAjuste(false)} onDone={() => { setAjuste(false); load(); }} />}
      {edit && (
        <Modal title={edit.id ? edit.name || "Producto" : "Nuevo producto"} onClose={() => { setEdit(null); setParams({}); }} wide foot={<>
          {edit.id && <button className="btn btn--ghost" style={{ marginRight: "auto" }} onClick={copyLink} title="Link directo al producto en la tienda"><Icon d={I.link} />Copiar link</button>}
          <button className="btn btn--ghost" onClick={() => setEdit(null)}>{canEdit ? "Cancelar" : "Cerrar"}</button>{canEdit && <button className="btn btn--crimson" onClick={save}>Guardar</button>}
        </>}>
          {!canEdit && <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Solo lectura: tu rol puede consultar el catálogo pero no modificarlo.</p>}
          <div className="tabs" style={{ marginBottom: 14 }}>{TABS.map(([k, l]) => <button key={k} className={tab === k ? "is-active" : ""} onClick={() => setTab(k)}>{l}{k === "variantes" && variants.length ? ` · ${variants.length}` : ""}{k === "imagenes" && edit.images.length ? ` · ${edit.images.length}` : ""}</button>)}</div>

          {tab === "general" && <>
            <div className="grid-3">
              <Field label="Nombre"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="Código"><input className="input" value={edit.code} onChange={(e) => setEdit({ ...edit, code: e.target.value })} /></Field>
              <Field label="Tipo de ítem"><select className="select" value={edit.item_type} onChange={(e) => setEdit({ ...edit, item_type: e.target.value })}><option value="producto">Producto</option><option value="servicio">Servicio</option></select></Field>
              {verPrecios && <Field label="Precio (sin IVA)" hint={verCostos ? marginHint(edit.price, edit.currency, costOf) : undefined}><input className="input input--mono" value={edit.price} onChange={(e) => setEdit({ ...edit, price: e.target.value })} /></Field>}
              <Field label="Divisa"><select className="select" value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option>CRC</option><option>USD</option></select></Field>
              <Field label="Impuesto"><select className="select" value={edit.tax_ids[0] ?? ""} onChange={(e) => setEdit({ ...edit, tax_ids: e.target.value ? [Number(e.target.value)] : [] })}><option value="">Sin impuesto</option>{taxes.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
              <Field label="Buscar CABYS" hint="Por descripción (ej. cámara de video): trae el código y el impuesto sugerido."><Lookup value={edit.cabys_description} placeholder="Buscar en el catálogo de Hacienda…" fetcher={searchCabys} disabled={!canEdit} onSelect={(it, text) => { if (it) pickCabys(it); else setEdit({ ...edit, cabys_description: text }); }} /></Field>
              <Field label="Código CABYS" hint="13 dígitos (Hacienda)."><input className="input input--mono" style={{ textAlign: "left" }} maxLength={13} value={edit.cabys_code} onChange={(e) => setEdit({ ...edit, cabys_code: e.target.value.replace(/\D/g, "") })} /></Field>
              <Field label="Bodega de origen" hint="De qué inventario sale este producto."><select className="select" value={edit.warehouse_id} onChange={(e) => setEdit({ ...edit, warehouse_id: e.target.value })}><option value="">—</option>{(opts?.warehouses || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
              <Field label="Unidad"><input className="input" value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} /></Field>
              <Field label="Stock mínimo"><input className="input input--mono" type="number" value={edit.min_stock} onChange={(e) => setEdit({ ...edit, min_stock: Number(e.target.value) })} /></Field>
              <Field label="Categoría">
                {newCat === null
                  ? <select className="select" value={edit.category_id} onChange={(e) => e.target.value === "__new" ? setNewCat("") : setEdit({ ...edit, category_id: e.target.value })}><option value="">Sin categoría</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}<option value="__new">+ Nueva categoría…</option></select>
                  : <div style={{ display: "flex", gap: 6 }}><input className="input" autoFocus placeholder="Nombre" value={newCat} onChange={(e) => setNewCat(e.target.value)} onKeyDown={(e) => e.key === "Enter" && createCat()} /><button className="btn btn--soft btn--sm" onClick={createCat}>Crear</button><button className="btn btn--ghost btn--sm" onClick={() => setNewCat(null)}><Icon d={I.x} size={14} /></button></div>}
              </Field>
              {verCostos && <Field label="Costo del proveedor" hint="El precio de venta lo calcula el margen al importar la lista."><input className="input input--mono" inputMode="decimal" value={edit.cost} onChange={(e) => setEdit({ ...edit, cost: e.target.value })} /></Field>}
              {verCostos && <Field label="Divisa del costo"><select className="select" value={edit.cost_currency} onChange={(e) => setEdit({ ...edit, cost_currency: e.target.value })}><option>USD</option><option>CRC</option></select></Field>}
              {verCostos && <Field label="Margen %" hint="Con el que se calculó el precio al importar la lista del proveedor."><input className="input input--mono" inputMode="decimal" value={edit.margin_pct} onChange={(e) => setEdit({ ...edit, margin_pct: e.target.value })} /></Field>}
              <Field label="Marca"><input className="input" value={edit.brand} onChange={(e) => setEdit({ ...edit, brand: e.target.value })} placeholder="Hikvision" /></Field>
              <Field label="Modelo"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.model} onChange={(e) => setEdit({ ...edit, model: e.target.value })} placeholder="DS-2CD1047G3-LIU" /></Field>
              <Field label="Código del proveedor"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.supplier_sku} onChange={(e) => setEdit({ ...edit, supplier_sku: e.target.value })} /></Field>
              <Field label="Proveedor"><select className="select" value={edit.supplier_id} onChange={(e) => setEdit({ ...edit, supplier_id: e.target.value })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
              <Field label="Peso (kg)" hint="Para envíos por peso."><input className="input input--mono" value={edit.weight_kg} onChange={(e) => setEdit({ ...edit, weight_kg: e.target.value })} /></Field>
              <Field label="Partida arancelaria"><input className="input input--mono" style={{ textAlign: "left" }} value={edit.tariff_code} onChange={(e) => setEdit({ ...edit, tariff_code: e.target.value })} /></Field>
              <Field label="N.º de registro" hint="Permisos sanitarios u otros."><input className="input" value={edit.registration_number} onChange={(e) => setEdit({ ...edit, registration_number: e.target.value })} /></Field>
            </div>
            <Field label="Descripción · Facturación" hint="Larga; va a cotizaciones y facturas."><textarea className="textarea" value={edit.description_invoice} onChange={(e) => setEdit({ ...edit, description_invoice: e.target.value })} /></Field>
            <Field label="Descripción · Comercio electrónico" hint="Corta; va a Links y a la tienda."><textarea className="textarea" style={{ minHeight: 60 }} value={edit.description_store} onChange={(e) => setEdit({ ...edit, description_store: e.target.value })} /></Field>
            {publica && <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={edit.show_on_web} onChange={(e) => setEdit({ ...edit, show_on_web: e.target.checked })} />Mostrar en sitio web</label>}
            {verCostos && edit.id && <SupplierOffers offers={offers} info={costOf} />}
            {avail && avail.state !== "servicio" && (
              <div className="muted" style={{ fontSize: 12.5, marginTop: 8, display: "grid", gap: 4 }}>
                <span><AvailBadge a={avail} /> {availText(avail)}{avail.supplier_updated_at && ` (${parseTs(avail.supplier_updated_at).toLocaleString("es-CR", { timeZone: "America/Costa_Rica", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })})`}</span>
                {!!avail.bodegas?.length && <span>Por bodega de venta: {avail.bodegas.map((b) => `${b.nombre} ${qty(b.disponible)}`).join(" · ")}</span>}
                {avail.supplier_unlinked_qty ? <span style={{ color: "var(--warn)" }}>Tiene proveedor pero ningún ítem de su lista vinculado: su existencia no cuenta. Vinculalo en <Link to="/inventario?tab=proveedores">Inventarios → Proveedores</Link>.</span> : null}
                {avail.label_publico && <span>Lo que ve el cliente en la tienda: <b>{avail.label_publico}</b></span>}
              </div>
            )}
          </>}

          {tab === "imagenes" && <>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>La imagen principal aparece en la tienda, en el POS y en los links de producto.</p>
            <GalleryField value={edit.images} onChange={(images) => setEdit({ ...edit, images })} />
          </>}

          {tab === "variantes" && <>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Color, resolución, tamaño… Cada variante tiene su código; el precio vacío usa el del producto. Las existencias se llevan a nivel de producto.</p>
            {variants.length > 0 && (
              <table className="table">
                <thead><tr><th>Nombre</th><th>Código</th><th className="num">Precio</th><th>Activa</th><th /></tr></thead>
                <tbody>{variants.map((v, i) => (
                  <tr key={v.id ?? `n${i}`}>
                    <td><input className="input" value={v.name} placeholder="Blanca / 4MP" onChange={(e) => setV(i, { name: e.target.value })} /></td>
                    <td><input className="input input--mono" style={{ textAlign: "left" }} value={v.code} placeholder={`${edit.code}-${i + 1}`} onChange={(e) => setV(i, { code: e.target.value })} /></td>
                    <td className="num"><input className="input input--mono" value={v.price} placeholder={String(edit.price)} onChange={(e) => setV(i, { price: e.target.value })} style={{ maxWidth: 140 }} /></td>
                    <td><input type="checkbox" checked={v.active} onChange={(e) => setV(i, { active: e.target.checked })} /></td>
                    <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setVariants(variants.filter((_, j) => j !== i))} title="Quitar"><Icon d={I.x} size={14} /></button></td>
                  </tr>
                ))}</tbody>
              </table>
            )}
            <button className="btn btn--soft btn--sm" style={{ marginTop: 10 }} onClick={() => setVariants([...variants, { name: "", code: `${edit.code || "VAR"}-${variants.length + 1}`, price: "", active: true, options: {} }])}><Icon d={I.plus} />Agregar variante</button>
          </>}
        </Modal>
      )}
    </>
  );
}

/* Propio (libre en bodegas de venta) · Proveedor · Proveedor consultado: la funcion unica del API */
function StockCells({ a }: { a?: Avail | null }) {
  if (!a || a.state === "servicio") return <><td className="num muted">—</td><td className="num muted">—</td><td className="muted">—</td></>;
  const own = Number(a.own_free ?? 0);
  return (
    <>
      <td className="num mono" style={{ fontWeight: own > 0 ? 700 : undefined, color: own > 0 ? undefined : "var(--text-3)" }} title={Number(a.own_reserved ?? 0) > 0 ? `${qty(a.own_reserved)} reservado` : undefined}>{qty(own)}</td>
      <td className="num mono" title={a.supplier_name || undefined}>{a.supplier_linked ? qty(a.supplier_qty) : a.supplier_unlinked_qty ? <span className="muted" title="Proveedor sin vincular: no cuenta">s/v</span> : <span className="muted">—</span>}</td>
      <td className="muted" style={{ fontSize: 12.5, whiteSpace: "nowrap", color: a.supplier_linked && !a.supplier_fresh ? "var(--bad)" : undefined }}>{a.supplier_linked ? `consultado ${a.supplier_age || "sin fecha"}${a.supplier_fresh ? "" : " · viejo"}${a.supplier_changed_age ? ` · cambió ${a.supplier_changed_age}` : ""}` : "—"}</td>
    </>
  );
}

/* Costo con su origen: "Eurocomp · 13 oct" (lista del proveedor vinculado) o "Propio". */
function CostCell({ p }: { p: Row }) {
  if (p.cost == null) return <span className="muted" title="Sin costo registrado">—</span>;
  const src = p.cost_source === "proveedor" ? `${p.cost_supplier || "Proveedor"}${p.cost_date ? ` · ${fmtDate(p.cost_date)}` : ""}` : `Propio${p.cost_date ? ` · ${fmtDate(p.cost_date)}` : ""}`;
  return (
    <span className="prd-cost">
      <span className="mono" style={{ whiteSpace: "nowrap" }}>{fmtMoney(p.cost, p.cost_currency || "USD")}</span>
      <small style={{ color: p.cost_source === "proveedor" && p.cost_active === false ? "var(--bad)" : undefined }} title={p.cost_active === false ? "El proveedor ya no lo trae en su lista vigente" : undefined}>{src}{p.cost_active === false ? " · ya no viene" : ""}</small>
    </span>
  );
}

function MarginCell({ p }: { p: Row }) {
  /* plan 1.12: el guardian de margen lo saco de la tienda ("Consultar") hasta que alguien aplique un precio */
  const hold = p.margin_hold ? <> <Link className="badge badge--bad" to="/aprobaciones?seccion=precios_por_revisar" title="Bajo el margen mínimo: en la tienda sale «Consultar». Revisalo en Aprobaciones → Precios por revisar.">fuera de venta</Link></> : null;
  if (p.margin_real_pct == null) return hold ?? <span className="muted">—</span>;
  const m = Number(p.margin_real_pct);
  if (p.margin_provisional) return <span className="mono muted" title="Sin costo: es el margen objetivo, no el real. Cargá el costo o vinculalo a un proveedor.">{m.toFixed(1)} % <span className="badge badge--muted">prov.</span>{hold}</span>;
  return <span className="mono" style={{ fontWeight: 700, color: marginTone(m) }}>{m.toFixed(1)} %{hold}</span>;
}

/* Ficha: las ofertas de proveedores vinculadas (Inventarios → Proveedores). La mejor alimenta costo y "Bajo pedido". */
function SupplierOffers({ offers, info }: { offers: Offer[] | null; info: CostInfo | null }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Ofertas de proveedores</b>
        {info?.margin_real_pct != null && <span className="muted" style={{ fontSize: 12.5 }}>Margen sobre venta {info.margin_provisional ? "objetivo (sin costo)" : "real"}: {Number(info.margin_real_pct).toFixed(1)} %{info.cost_source ? ` · costo ${info.cost_source === "proveedor" ? `de ${info.cost_supplier || "proveedor"}` : "propio"}${info.cost_date ? ` del ${fmtDate(info.cost_date)}` : ""}` : ""}</span>}
      </div>
      {info && "ally_price" in info && (
        <p className="muted" style={{ fontSize: 12.5, margin: "4px 0 0" }}>
          Precio para aliados: {info.ally_price != null ? <b>{fmtMoney(info.ally_price)}</b> : "a consultar (sin costo)"}
          {info.ally_price_source === "recargo" ? " (recargo sobre el costo)" : info.ally_price_source === "precio_general" ? " (precio fijo)" : ""} · <Link to="/aliados">Aliados → Precios</Link>
        </p>
      )}
      {offers === null ? <p className="meta">Cargando…</p> : offers.length === 0 ? (
        <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>Sin ofertas vinculadas. En <Link to="/inventario?tab=proveedores">Inventarios → Proveedores</Link> puede vincular este producto con el ítem de la lista del proveedor: el costo y la existencia del proveedor se actualizan solos con cada lista.</p>
      ) : (
        <table className="table" style={{ marginTop: 6 }}>
          <thead><tr><th>Proveedor</th><th>Código</th><th className="num">Existencia</th><th className="num">Costo</th><th className="num">Costo ₡</th><th>Lista</th></tr></thead>
          <tbody>{offers.map((o) => (
            <tr key={o.supplier_item_id} style={o.active ? undefined : { opacity: 0.55 }}>
              <td>{o.supplier}{o.best && <span className="badge badge--ok" style={{ marginLeft: 6 }}>Mejor oferta</span>}{!o.active && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Ya no viene</span>}</td>
              <td className="mono">{o.code}</td>
              <td className="num mono">{o.stock_proveedor ?? "—"}</td>
              <td className="num mono">{o.cost != null ? fmtMoney(o.cost, o.currency || "USD") : "—"}</td>
              <td className="num mono">{o.cost_crc != null ? fmtMoney(Math.round(Number(o.cost_crc)), "CRC") : "—"}</td>
              <td className="muted">{fmtDate(o.updated_at)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  );
}

/* Margen que deja el precio que se está escribiendo, contra el costo del proveedor (solo quien ve costos). */
function marginHint(price: string, currency: string, info: CostInfo | null): string | undefined {
  if (!info || info.cost == null) return "Sin costo registrado: no hay margen que calcular.";
  const v = Number(String(price).replace(",", "."));
  if (!Number.isFinite(v) || v <= 0) return `Costo ${fmtMoney(info.cost, info.cost_currency || "USD")}.`;
  const cost = currency === "CRC" ? (info.cost_crc != null ? Number(info.cost_crc) : null) : (info.cost_currency || "USD") === currency ? Number(info.cost) : null;
  if (cost == null) return `Costo ${fmtMoney(info.cost, info.cost_currency || "USD")} · el margen se recalcula al guardar.`;
  const m = ((v - cost) / v) * 100;
  return `Costo ${fmtMoney(cost, currency)} · margen sobre la venta ${m.toFixed(1)} %${m < 20 ? " (bajo)" : ""}`;
}

const PENCIL = "M4 20h4L19 9l-4-4L4 16z|M13 7l4 4";

/* Precio de venta editable en la lista: clic, escribir, Enter (o salir del campo) guarda; Esc cancela. */
function PriceInline({ p, onSaved }: { p: Row; onSaved: (r: Row) => void }) {
  const { toast } = useSession();
  const [val, setVal] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (val === null || saving) return;
    const n = Number(val.replace(/\s/g, "").replace(",", "."));
    if (!Number.isFinite(n) || n < 0) { toast("Precio no válido", "bad"); return; }
    if (n === Number(p.price)) { setVal(null); return; }
    setSaving(true);
    try { const r = await api<Row>(`/products/${p.id}/price`, { method: "PATCH", json: { price: n } }); onSaved(r); toast(`Precio de ${p.code} actualizado`); setVal(null); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setSaving(false); }
  };
  if (val === null) {
    return <button type="button" className="prd-precio-btn money" onClick={() => setVal(Number(p.price) ? String(Number(p.price)) : "")} title="Clic para cambiar el precio de venta (sin IVA)">{fmtMoney(p.price, p.currency)}<Icon d={PENCIL} size={12} /></button>;
  }
  return (
    <input className="input input--mono prd-precio-input" autoFocus inputMode="decimal" disabled={saving} value={val} aria-label={`Precio de ${p.name} sin IVA (${p.currency})`}
      onChange={(e) => setVal(e.target.value)} onBlur={save} onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setVal(null); }} />
  );
}

type AdjRow = { id: number; code: string; name: string; currency: string; price_before: number | string; price_after: number | string | null; cost_crc: number | string | null; margin_before: number | string | null; margin_after: number | string | null; skip: string | null };

/* Ajuste de precios en bloque (solo quien ve costos): % sobre el precio o margen objetivo sobre el costo, para lo
   marcado o lo filtrado. Primero la vista previa; "Aplicar" deja una entrada en la bitácora con cada cambio. */
function AjustePrecios({ filtro, ids, cats, onClose, onDone }: {
  filtro: { q: string; category_id: string; brand: string; supplier_id: string; web: string }; ids: number[]; cats: { id: number; name: string }[];
  onClose: () => void; onDone: () => void;
}) {
  const { toast } = useSession();
  const [mode, setMode] = useState<"pct" | "margen">("pct");
  const [value, setValue] = useState("10");
  const [cat, setCat] = useState(filtro.category_id);
  const [prev, setPrev] = useState<{ items: AdjRow[]; changes: number; skipped: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const usarMarcados = ids.length > 0;
  const body = (confirm: boolean) => {
    const b: Record<string, unknown> = { mode, value: Number(value.replace(",", ".")), confirm };
    if (usarMarcados) b.ids = ids;
    else {
      if (filtro.q) b.q = filtro.q;
      if (cat) b.category_id = Number(cat);
      if (filtro.brand) b.brand = filtro.brand;
      if (filtro.supplier_id) b.supplier_id = Number(filtro.supplier_id);
      if (filtro.web) b.web = filtro.web === "true";
    }
    return b;
  };
  const run = async (confirm: boolean) => {
    setBusy(true);
    try {
      const r = await api<{ items: AdjRow[]; changes: number; skipped: number; applied: boolean }>("/products/price-adjust", { method: "POST", json: body(confirm) });
      if (confirm) { toast(`${r.changes} precio(s) actualizado(s)`); onDone(); } else setPrev(r);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const catName = cats.find((c) => String(c.id) === cat)?.name || "";
  const alcance = usarMarcados
    ? `${ids.length} producto(s) marcado(s)`
    : [filtro.q && `búsqueda «${filtro.q}»`, cat && `categoría ${catName} (con sus subcategorías)`, filtro.brand && `marca ${filtro.brand}`, filtro.web && (filtro.web === "true" ? "publicados" : "ocultos")].filter(Boolean).join(" · ") || "todo el catálogo activo";
  return (
    <Modal title="Ajustar precios" onClose={onClose} wide foot={<>
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      <button className="btn btn--soft" disabled={busy} onClick={() => run(false)}>Vista previa</button>
      <button className="btn btn--crimson" disabled={busy || !prev || prev.changes === 0} onClick={() => run(true)}>{prev ? `Aplicar a ${prev.changes}` : "Aplicar"}</button>
    </>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Se aplica a: <b>{alcance}</b>. Hasta 500 productos por ajuste; en colones se redondea a la centena hacia arriba. La sincronización con Eurocomp nunca cambia estos precios.</p>
      <div className="grid-3">
        <Field label="Tipo de ajuste"><select className="select" value={mode} onChange={(e) => { const m = e.target.value as "pct" | "margen"; setMode(m); setPrev(null); setValue(m === "pct" ? "10" : "35"); }}><option value="pct">Subir o bajar un porcentaje</option><option value="margen">Margen objetivo sobre la venta</option></select></Field>
        <Field label={mode === "pct" ? "Porcentaje (negativo baja)" : "Margen objetivo %"} hint={mode === "pct" ? "Ej.: 10 sube 10 %; -5 baja 5 %." : "precio = costo ÷ (1 − margen). Sin costo no se toca."}><input className="input input--mono" inputMode="decimal" value={value} onChange={(e) => { setValue(e.target.value); setPrev(null); }} /></Field>
        {!usarMarcados && <Field label="Categoría"><select className="select" value={cat} onChange={(e) => { setCat(e.target.value); setPrev(null); }}><option value="">Toda categoría</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
      </div>
      {prev && (
        <>
          <p style={{ fontSize: 13 }}><b>{prev.changes}</b> cambian · {prev.skipped} sin cambio</p>
          <div style={{ maxHeight: 360, overflowY: "auto" }}>
            <table className="table table--sm table--stack">
              <thead><tr><th>Producto</th><th className="num">Precio actual</th><th className="num">Precio nuevo</th><th className="num">Margen actual</th><th className="num">Margen nuevo</th></tr></thead>
              <tbody>{prev.items.map((r) => (
                <tr key={r.id} style={r.skip ? { opacity: 0.6 } : undefined}>
                  <td data-label="Producto" className="stack-full"><b>{r.name}</b> <span className="muted mono" style={{ fontSize: 12 }}>{r.code}</span>{r.skip && <div className="muted" style={{ fontSize: 12 }}>{r.skip}</div>}</td>
                  <td data-label="Precio actual" className="num mono">{fmtMoney(r.price_before, r.currency)}</td>
                  <td data-label="Precio nuevo" className="num mono" style={{ fontWeight: 700 }}>{r.price_after != null ? fmtMoney(r.price_after, r.currency) : "—"}</td>
                  <td data-label="Margen actual" className="num mono">{r.margin_before != null ? `${Number(r.margin_before).toFixed(1)} %` : "—"}</td>
                  <td data-label="Margen nuevo" className="num mono" style={r.margin_after != null ? { color: marginTone(Number(r.margin_after)), fontWeight: 700 } : undefined}>{r.margin_after != null ? `${Number(r.margin_after).toFixed(1)} %` : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </Modal>
  );
}
