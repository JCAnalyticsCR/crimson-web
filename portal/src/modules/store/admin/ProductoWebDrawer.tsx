/* Mi Tienda -> Catalogo -> cajon de un producto (O4-04), en /mi-tienda/catalogo/:id. Todo lo web del producto en un
   solo lugar, con un solo guardado (PATCH /store/catalog/{id}) y la barra de siempre:
   - «Cómo se ve»: la tarjeta real de la tienda (storeKit.ProductCard), con el borrador aplicado.
   - «Nombre y textos», «Fotos», «Ficha técnica»: lo que se edita.
   - «Precio» (O4-05: precio web escrito con IVA) y el historial; una oferta solo se muestra con respaldo.
   - «Disponibilidad» (lectura) y «Google» (vista previa con contadores de 60 y 155 caracteres).
   Nunca se toca el nombre fiscal ni el codigo (viven en facturas). Publicar es solo del administrador. */
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useSession } from "../../../app/session";
import { Field, I, Icon, Loading } from "../../../ui/components";
import { GalleryField } from "../../../ui/MediaPicker";
import { ProductCard, StoreProvider, money, type StoreCtx, type VProd } from "../storeKit";
import "../shop.css";
import "../vitrina.css";
import { SaveBar } from "./SaveBar";
import { SerpPreview } from "./SerpPreview";
import { useDraft } from "./useDraft";
import { PrecioTab } from "./PrecioWeb";

export type Detail = {
  id: number; name: string; code: string; brand: string | null; model: string | null; category_id: number | null; categories: { id: number; name: string }[];
  description_store: string; images: { url: string }[]; specs: Record<string, string>; derived_specs: Record<string, string>; datasheet_url: string;
  show_on_web: boolean; web_featured: boolean; currency: string; store_currency: string; tax_rate: number | string;
  price: number | string | null; web_price: number | string | null; compare_price: number | string | null; display_price: number | string | null;
  compare_backed: boolean; compare_min_days: number; price_history: { valid_from: string; price_final: number | string; currency: string; source: string }[];
  margin_hold: boolean;
  availability: { publico: { state: string | null; label: string | null; accion: string | null; plazo: string | null }; interno: Record<string, unknown> };
  path: string; seo: { title: string; description?: string; canonical?: string | null } | null;
};
export type Edit = Pick<Detail, "show_on_web" | "web_featured" | "description_store" | "images" | "specs" | "datasheet_url" | "category_id" | "brand" | "web_price">;
const LABELS: Record<string, string> = { show_on_web: "Publicado", web_featured: "Fijado arriba", description_store: "Descripción", images: "Fotos", specs: "Ficha técnica", datasheet_url: "Ficha PDF", category_id: "Categoría", brand: "Marca", web_price: "Precio web" };
const TABS = ["Cómo se ve", "Nombre y textos", "Precio", "Fotos", "Ficha técnica", "Disponibilidad", "Google"] as const;

const pick = (r: Record<string, unknown>): Edit => {
  const d = r as unknown as Detail;
  return { show_on_web: d.show_on_web, web_featured: d.web_featured, description_store: d.description_store || "", images: d.images || [], specs: d.specs || {}, datasheet_url: d.datasheet_url || "", category_id: d.category_id, brand: d.brand || "", web_price: d.web_price };
};

export default function ProductoWebDrawer({ onSaved }: { onSaved?: () => void }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const { allows } = useSession();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Cómo se ve");
  const [full, setFull] = useState<Detail | null>(null);
  const d = useDraft<Edit>({
    key: `catalogo:producto:${id}`,
    url: `/store/catalog/${id}`,
    method: "PATCH",
    read: (r) => { setFull(r as unknown as Detail); return pick(r); },
    body: (x, changed) => Object.fromEntries(changed.map((k) => [k, k === "web_price" && (x.web_price === "" || x.web_price == null) ? null : x[k]])),
  });
  const cerrar = () => navigate(`/mi-tienda/catalogo${location.search}`);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector(".modal-bg")) cerrar(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }); // eslint-disable-line react-hooks/exhaustive-deps
  const puedePublicar = allows("catalog.publicar");
  const x = d.draft;
  let body: ReactNode = <Loading />;
  if (d.error) body = <p className="mt-lead">{d.error}</p>;
  else if (x && full) {
    const specs = Object.entries(x.specs);
    const setSpecs = (rows: [string, string][]) => d.set({ specs: Object.fromEntries(rows) });
    body = (
      <>
        <div className="pw-tabs" role="tablist" aria-label="Partes de la ficha web">
          {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={`btn btn--sm ${tab === t ? "btn--crimson" : "btn--ghost"}`} onClick={() => setTab(t)}>{t}</button>)}
        </div>
        <div className="mt-stack" role="tabpanel" aria-label={tab}>
          {tab === "Cómo se ve" && <CardPreview full={full} draft={x} />}
          {tab === "Nombre y textos" && (
            <>
              <Field label="Nombre" hint="El nombre fiscal vive en facturas y cotizaciones: no se cambia aquí. El nombre comercial llega con la Ola 3."><input className="input" value={full.name} readOnly aria-readonly="true" /></Field>
              <div className="grid-2">
                <Field label="Marca"><input className="input" value={x.brand || ""} maxLength={60} onChange={(e) => d.set({ brand: e.target.value })} /></Field>
                <Field label="Categoría"><select className="select" value={x.category_id ?? ""} onChange={(e) => d.set({ category_id: e.target.value ? Number(e.target.value) : null })}><option value="">Sin categoría</option>{full.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
              </div>
              <Field label="Descripción para la tienda" hint="Corta y útil: para qué sirve y qué la distingue."><textarea className="textarea" rows={6} maxLength={5000} value={x.description_store} onChange={(e) => d.set({ description_store: e.target.value })} /></Field>
              <div className="mt-row">
                <label className="pw-check"><input type="checkbox" checked={x.show_on_web} disabled={!puedePublicar} onChange={(e) => d.set({ show_on_web: e.target.checked })} />Publicado en la tienda{!puedePublicar && <span className="meta" style={{ textTransform: "none" }}> (solo el administrador)</span>}</label>
                <label className="pw-check"><input type="checkbox" checked={x.web_featured} onChange={(e) => d.set({ web_featured: e.target.checked })} />Fijado arriba</label>
              </div>
            </>
          )}
          {tab === "Precio" && <PrecioTab full={full} draft={x} set={d.set} />}
          {tab === "Fotos" && <Field label="Fotos" hint="La principal es la primera que ve el cliente."><GalleryField value={x.images.map((im, i) => ({ url: im.url, main: i === 0 }))} onChange={(v) => d.set({ images: [...v.filter((im) => im.main), ...v.filter((im) => !im.main)].map((im) => ({ url: im.url })) })} /></Field>}
          {tab === "Ficha técnica" && (
            <>
              <p className="mt-lead">La tienda deduce estas del nombre y la descripción: {Object.entries(full.derived_specs).map(([k, v]) => `${k}: ${v}`).join(" · ") || "ninguna"}. Lo que agregue aquí manda sobre lo deducido.</p>
              {specs.map(([k, v], i) => (
                <div key={i} className="pw-spec">
                  <input className="input" value={k} placeholder="Garantía" aria-label={`Especificación ${i + 1}`} onChange={(e) => setSpecs(specs.map((s, j) => (j === i ? [e.target.value, s[1]] : s)))} />
                  <input className="input" value={v} placeholder="12 meses" aria-label={`Valor ${i + 1}`} onChange={(e) => setSpecs(specs.map((s, j) => (j === i ? [s[0], e.target.value] : s)))} />
                  <button className="x" onClick={() => setSpecs(specs.filter((_, j) => j !== i))} aria-label={`Quitar la especificación ${i + 1}`}><Icon d={I.x} size={14} /></button>
                </div>
              ))}
              <div><button className="btn btn--ghost btn--sm" onClick={() => setSpecs([...specs, ["", ""]])}><Icon d={I.plus} />Especificación</button></div>
              <Field label="Ficha técnica (PDF)" hint="Enlace https:// al PDF del fabricante."><input className="input" value={x.datasheet_url} onChange={(e) => d.set({ datasheet_url: e.target.value })} /></Field>
            </>
          )}
          {tab === "Disponibilidad" && (
            <div className="mt-box">
              <p className="mt-lead"><b>Lo que ve el cliente:</b> {full.availability.publico.label || "sin dato"}{full.availability.publico.accion === "comprar" ? " · se paga en línea" : " · el cliente escribe por WhatsApp"}</p>
              <p className="mt-lead"><b>En bodega (libre):</b> {String(full.availability.interno.libre ?? 0)} · <b>Proveedor:</b> {full.availability.interno.proveedor != null ? String(full.availability.interno.proveedor) : "sin dato vigente"}</p>
              <p className="meta" style={{ textTransform: "none", margin: 0 }}>La disponibilidad sale del inventario y del proveedor; se corrige en Inventarios.</p>
            </div>
          )}
          {tab === "Google" && <SerpPreview title={full.seo?.title || full.name} description={full.seo?.description || x.description_store} url={full.seo?.canonical || full.path} />}
        </div>
      </>
    );
  }
  return (
    <div className="pw-bg" onMouseDown={(e) => e.target === e.currentTarget && cerrar()}>
      <aside className="pw" role="dialog" aria-modal="true" aria-label={full ? `Ficha web: ${full.name}` : "Ficha web"}>
        <header className="pw__head">
          <div style={{ minWidth: 0 }}>
            <div className="meta">{full?.code}</div>
            <h2 className="h2 pw__title">{full?.name || "Cargando…"}</h2>
          </div>
          <button className="x" onClick={cerrar} aria-label="Cerrar"><Icon d={I.x} /></button>
        </header>
        <div className="pw__body">{body}</div>
        <SaveBar drafts={[d]} labels={LABELS} onSaved={onSaved} />
      </aside>
    </div>
  );
}

/** La tarjeta real de la tienda, con lo que se esta editando (precio y foto del borrador). */
function CardPreview({ full, draft }: { full: Detail; draft: Edit }) {
  const ctx: StoreCtx = {
    slug: "", base: "", hostMode: true, isShop: true, compare: [], toggleCompare: () => {}, addToCart: () => {}, openCart: () => {},
    api: async () => { throw new Error("vista previa"); },
    store: { name: "", tagline: "", logo_url: null, primary: "#e2233a", secondary: "#15131a", font: "Arial", kind: "tienda", whatsapp: "", currency: full.store_currency, legal: { privacy: "", terms: "" } },
  };
  const cat = full.categories.find((c) => c.id === draft.category_id)?.name || null;
  const p: VProd = {
    id: full.id, code: full.code, name: full.name, brand: draft.brand || null, image: draft.images[0]?.url || null, images: draft.images.map((i) => i.url),
    description: draft.description_store, category: cat, category_id: draft.category_id, path: `/p/${full.path}`, tax_rate: Number(full.tax_rate), item_type: "producto",
    display_price: full.display_price ?? 0, display_currency: full.store_currency, price_on_request: full.display_price == null || !Number(full.display_price),
    availability: { state: full.availability.publico.state || "consultar", label: full.availability.publico.label || "", accion: full.availability.publico.accion },
    variants: [], price: full.price ?? 0, currency: full.currency,
  };
  return (
    <div className="mt-stack">
      <p className="mt-lead">Así la ve su cliente en el catálogo{draft.show_on_web ? "" : " (hoy no está publicada)"}. Precio de hoy: {full.display_price != null ? money(full.display_price, full.store_currency) : "solo lo ve quien ve precios"}.</p>
      <div className="shop pw-card" inert>
        <StoreProvider value={ctx}><ProductCard p={p} /></StoreProvider>
      </div>
    </div>
  );
}
