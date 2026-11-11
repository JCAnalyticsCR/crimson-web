/* Mi Tienda: personalizacion + constructor de bloques con vista previa en vivo (mismo render que el sitio publico). */
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";
import { Blocks } from "./Blocks";
import { GalleryField, ImageField } from "../../ui/MediaPicker";
import CheckoutSettings from "./CheckoutSettings";
import StoreShowcaseAdmin from "./StoreShowcaseAdmin";
import { formatCRCInput } from "../../lib/parseCRC";
import { businessLines, type Business } from "./storeKit";
import { FREE_CONFIRM, checkRate, freeEverywhere, suspiciousAmount, type Rate, type RateText } from "./rates";

export type Block = { type: string; [k: string]: unknown };
type Page = { id: number; slug: string; title: string; blocks: Block[]; published: boolean; in_nav: boolean; position: number };
type Cfg = { slug: string; name: string; tagline: string; logo_url: string | null; primary: string; secondary: string; font: string; domain: string | null; published: boolean; kind: string; shipping_rates: Rate[]; whatsapp: string; currency: string; legal: { privacy: string; terms: string }; business?: Business };

const NEW_BLOCK: Record<string, Block> = {
  hero: { type: "hero", title: "Tecnología que protege", text: "Videovigilancia, redes y control de acceso", cta: { label: "Ver catálogo", to: "productos" } },
  text: { type: "text", title: "Sobre nosotros", text: "Escribí aquí el contenido de la sección." },
  image_text: { type: "image_text", title: "Instalación profesional", text: "Equipo propio y respaldo local.", image: "", reverse: false },
  cta: { type: "cta", title: "¿Querés que evaluemos tu caso?", label: "Escribinos", to: "contacto" },
  columns: { type: "columns", cols: [{ title: "Seguridad", text: "CCTV y accesos" }, { title: "Redes", text: "WiFi y fibra" }, { title: "Soporte", text: "Mantenimiento" }] },
  products: { type: "products", title: "Productos destacados", limit: 6 },
  gallery: { type: "gallery", title: "Instalaciones", images: [] },
  form: { type: "form", title: "Contacto", fields: ["nombre", "correo", "mensaje"] },
};
const LABELS: Record<string, string> = { hero: "Portada (hero)", text: "Texto", image_text: "Imagen + texto", cta: "Llamado a la acción", columns: "Columnas", products: "Productos", gallery: "Galería", form: "Formulario" };

export default function Store() {
  const { toast, me } = useSession();
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [pagesSt, setPages] = useState<Page[] | null>(null); // null = cargando
  const pages = pagesSt ?? [];
  const [sel, setSel] = useState<Page | null>(null);
  const [newPage, setNewPage] = useState<{ slug: string; title: string } | null>(null);
  const [add, setAdd] = useState(false);
  const load = () => { api<Cfg>("/store").then(setCfg); api<Page[]>("/store/pages").then((r) => { setPages(r); setSel((s) => (s ? r.find((x) => x.id === s.id) || r[0] || null : r[0] || null)); }); };
  useEffect(load, []);

  const saveCfg = (patch: Partial<Cfg>): Promise<boolean> => api<Cfg>("/store", { method: "PUT", json: patch }).then((c) => { setCfg(c); toast("Tienda actualizada"); return true; }).catch((e) => { toast(e.message, "bad"); return false; });
  const savePage = (p: Page) => api<Page>(`/store/pages/${p.id}`, { method: "PUT", json: { slug: p.slug, title: p.title, blocks: p.blocks, published: p.published, in_nav: p.in_nav, position: p.position } }).then((r) => { setSel(r); setPages(pages.map((x) => (x.id === r.id ? r : x))); toast("Página guardada"); }).catch((e) => toast(e.message, "bad"));
  const createPage = () => { if (!newPage) return; api<Page>("/store/pages", { method: "POST", json: { ...newPage, blocks: [NEW_BLOCK.hero], published: false } }).then(() => { setNewPage(null); load(); }).catch((e) => toast(e.message, "bad")); };
  const delPage = (p: Page) => { if (!confirm(`¿Eliminar la página “${p.title}”?`)) return; api(`/store/pages/${p.id}`, { method: "DELETE" }).then(() => { setSel(null); load(); }); };
  const setBlocks = (blocks: Block[]) => sel && setSel({ ...sel, blocks });
  const move = (i: number, dir: -1 | 1) => { if (!sel) return; const b = [...sel.blocks]; const j = i + dir; if (j < 0 || j >= b.length) return; [b[i], b[j]] = [b[j], b[i]]; setBlocks(b); };
  const publicUrl = cfg ? `/tienda/${cfg.slug}` : "#";

  return (
    <>
      <div className="page-head">
        <div><div className="meta">10 · Mi Tienda</div><h1 className="h1">Mi Tienda</h1></div>
        <div className="page-head__actions">
          {/* solo el administrador: la API de la Tienda de pruebas responde 404 a cualquier otro */}
          {me?.role === "admin" && <Link className="btn btn--ghost btn--sm" to="/mi-tienda/pruebas">Tienda de pruebas</Link>}
          {cfg && <a className="btn btn--ghost btn--sm" href={publicUrl} target="_blank" rel="noopener"><Icon d={I.store} />Ver sitio</a>}
          {cfg && <button className={`btn btn--sm ${cfg.published ? "btn--soft" : "btn--crimson"}`} onClick={() => saveCfg({ published: !cfg.published })}>{cfg.published ? "Despublicar" : "Publicar tienda"}</button>}
        </div>
      </div>

      {cfg && (
        <div className="grid-2">
          <Card title="Configura el sitio a tu gusto">
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="grid-2">
                <Field label="Nombre de la tienda"><input className="input" defaultValue={cfg.name} onBlur={(e) => saveCfg({ name: e.target.value })} /></Field>
                <Field label="Frase"><input className="input" defaultValue={cfg.tagline} onBlur={(e) => saveCfg({ tagline: e.target.value })} /></Field>
                <Field label="Color principal"><div style={{ display: "flex", gap: 8 }}><input type="color" value={cfg.primary} onChange={(e) => saveCfg({ primary: e.target.value })} style={{ width: 44, height: 38, border: 0, background: "none" }} /><input className="input input--mono" style={{ textAlign: "left" }} defaultValue={cfg.primary} onBlur={(e) => saveCfg({ primary: e.target.value })} /></div></Field>
                <Field label="Color secundario"><div style={{ display: "flex", gap: 8 }}><input type="color" value={cfg.secondary} onChange={(e) => saveCfg({ secondary: e.target.value })} style={{ width: 44, height: 38, border: 0, background: "none" }} /><input className="input input--mono" style={{ textAlign: "left" }} defaultValue={cfg.secondary} onBlur={(e) => saveCfg({ secondary: e.target.value })} /></div></Field>
                <Field label="Tipo de tienda" hint="Catálogo muestra productos; Tienda agrega carrito y checkout."><select className="select" value={cfg.kind} onChange={(e) => saveCfg({ kind: e.target.value })}><option value="catalogo">Catálogo</option><option value="tienda">Tienda (carrito)</option></select></Field>
                <Field label="Tipografía"><select className="select" value={cfg.font} onChange={(e) => saveCfg({ font: e.target.value })}>{["Arial"].map((f) => <option key={f}>{f}</option>)}</select></Field>
                <Field label="Logo"><ImageField value={cfg.logo_url} label="Logo" onChange={(url) => saveCfg({ logo_url: url })} /></Field>
                <Field label="WhatsApp"><input className="input" defaultValue={cfg.whatsapp} onBlur={(e) => saveCfg({ whatsapp: e.target.value })} /></Field>
                <Field label="Moneda de la tienda" hint="Precios exhibidos y pedidos. Lo que esté en otra moneda se convierte con el tipo de cambio del día."><select className="select" value={cfg.currency || "CRC"} onChange={(e) => saveCfg({ currency: e.target.value })}><option value="CRC">Colones (₡)</option><option value="USD">Dólares ($)</option></select></Field>
              </div>
              <Field label="Dominio" hint={`Gratis: ${location.host}${publicUrl} · propio: apuntar un CNAME al portal`}><input className="input" defaultValue={cfg.domain || ""} placeholder="tienda.crimsoncr.com" onBlur={(e) => saveCfg({ domain: e.target.value })} /></Field>
            </div>
          </Card>
          <ShippingRates rates={cfg.shipping_rates} onSave={(r) => saveCfg({ shipping_rates: r })} />
        </div>
      )}

      {cfg && <BusinessCard value={cfg.business || {}} onSave={(b) => saveCfg({ business: b })} />}

      {cfg && cfg.kind === "tienda" && <CheckoutSettings rates={cfg.shipping_rates} onRates={(r) => saveCfg({ shipping_rates: r })} />}
      {cfg && <StoreShowcaseAdmin />}

      <Card title="Páginas y bloques" flush extra={<button className="btn btn--crimson btn--sm" onClick={() => setNewPage({ slug: "", title: "" })}><Icon d={I.plus} />Nueva página</button>}>
        {pagesSt === null ? <Loading /> : pages.length === 0 ? <Empty hint="Creá la página de inicio y armala con bloques." /> : (
          <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", minHeight: 420 }}>
            <div style={{ borderRight: "1px solid var(--hair)", padding: 10, display: "flex", flexDirection: "column", gap: 4 }}>
              {pages.map((p) => (
                <button key={p.id} className={`nav__link ${sel?.id === p.id ? "is-active" : ""}`} style={{ color: "var(--text)", gridTemplateColumns: "1fr auto", background: sel?.id === p.id ? "var(--crimson-soft)" : undefined, boxShadow: sel?.id === p.id ? "inset 2px 0 0 var(--crimson)" : undefined, border: 0, cursor: "pointer", textAlign: "left" }} onClick={() => setSel(p)}>
                  <span><b style={{ display: "block", fontSize: 13 }}>{p.title}</b><span className="meta" style={{ textTransform: "none" }}>/{p.slug}</span></span>
                  {!p.published && <span className="badge badge--muted">borrador</span>}
                </button>
              ))}
            </div>
            {sel && (
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <input className="input" style={{ maxWidth: 220 }} value={sel.title} onChange={(e) => setSel({ ...sel, title: e.target.value })} />
                  <input className="input input--mono" style={{ maxWidth: 160, textAlign: "left" }} value={sel.slug} onChange={(e) => setSel({ ...sel, slug: e.target.value })} />
                  <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={sel.published} onChange={(e) => setSel({ ...sel, published: e.target.checked })} />Publicada</label>
                  <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={sel.in_nav} onChange={(e) => setSel({ ...sel, in_nav: e.target.checked })} />En el menú</label>
                  <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
                    <button className="btn btn--soft btn--sm" onClick={() => setAdd(true)}><Icon d={I.plus} />Bloque</button>
                    <button className="btn btn--crimson btn--sm" onClick={() => savePage(sel)}><Icon d={I.check} />Guardar</button>
                    <button className="btn btn--danger btn--sm" onClick={() => delPage(sel)}>Eliminar</button>
                  </div>
                </div>

                {sel.blocks.length === 0 ? <Empty title="Página vacía" hint="Agregá el primer bloque." /> : sel.blocks.map((b, i) => (
                  <div key={i} className="card" style={{ overflow: "hidden" }}>
                    <div className="card__head" style={{ padding: "8px 12px" }}>
                      <span className="meta">{LABELS[b.type] || b.type}</span>
                      <div style={{ display: "flex", gap: 4 }}>
                        <button className="x" onClick={() => move(i, -1)} title="Subir">↑</button>
                        <button className="x" onClick={() => move(i, 1)} title="Bajar">↓</button>
                        <button className="x" onClick={() => setBlocks([...sel.blocks.slice(0, i + 1), JSON.parse(JSON.stringify(b)), ...sel.blocks.slice(i + 1)])} title="Duplicar"><Icon d={I.copy} size={14} /></button>
                        <button className="x" onClick={() => setBlocks(sel.blocks.filter((_, k) => k !== i))} title="Eliminar"><Icon d={I.x} size={14} /></button>
                      </div>
                    </div>
                    <div className="card__body" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                      <BlockEditor block={b} onChange={(nb) => setBlocks(sel.blocks.map((x, k) => (k === i ? nb : x)))} />
                      <div style={{ border: "1px solid var(--hair)", borderRadius: 10, overflow: "hidden", background: "var(--surface-2)" }}>
                        <div className="meta" style={{ padding: "6px 10px", borderBottom: "1px solid var(--hair)" }}>Vista previa</div>
                        <div style={{ transform: "scale(.72)", transformOrigin: "top left", width: "139%", pointerEvents: "none" }}>
                          <Blocks blocks={[b]} primary={cfg?.primary || "#e2233a"} products={[]} />
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>

      {add && sel && (
        <Modal title="Agregar bloque" onClose={() => setAdd(false)}>
          <div className="actions-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {Object.keys(NEW_BLOCK).map((k) => (
              <button key={k} className="action" style={{ cursor: "pointer", textAlign: "left" }} onClick={() => { setBlocks([...sel.blocks, JSON.parse(JSON.stringify(NEW_BLOCK[k]))]); setAdd(false); }}>
                <i><Icon d={I.plus} /></i><div><b>{LABELS[k]}</b><span>{k}</span></div>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {newPage && (
        <Modal title="Nueva página" onClose={() => setNewPage(null)} foot={<><button className="btn btn--ghost" onClick={() => setNewPage(null)}>Cancelar</button><button className="btn btn--crimson" onClick={createPage}>Crear</button></>}>
          <Field label="Título"><input className="input" value={newPage.title} onChange={(e) => setNewPage({ ...newPage, title: e.target.value, slug: newPage.slug || e.target.value.toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") })} /></Field>
          <Field label="Dirección (slug)" hint="Solo minúsculas, números y guiones."><input className="input input--mono" style={{ textAlign: "left" }} value={newPage.slug} onChange={(e) => setNewPage({ ...newPage, slug: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}

function BlockEditor({ block, onChange }: { block: Block; onChange: (b: Block) => void }) {
  const set = (k: string, v: unknown) => onChange({ ...block, [k]: v });
  const str = (k: string) => (block[k] as string) ?? "";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {"title" in block && <Field label="Título"><input className="input" value={str("title")} onChange={(e) => set("title", e.target.value)} /></Field>}
      {"text" in block && <Field label="Texto"><textarea className="textarea" value={str("text")} onChange={(e) => set("text", e.target.value)} /></Field>}
      {"image" in block && <Field label="Imagen"><ImageField value={str("image") || null} onChange={(url) => set("image", url || "")} /></Field>}
      {"label" in block && <Field label="Texto del botón"><input className="input" value={str("label")} onChange={(e) => set("label", e.target.value)} /></Field>}
      {"limit" in block && <Field label="Cantidad de productos"><input className="input input--mono" type="number" value={Number(block.limit)} onChange={(e) => set("limit", Number(e.target.value))} /></Field>}
      {"cta" in block && (
        <div className="grid-2">
          <Field label="Botón"><input className="input" value={(block.cta as { label?: string })?.label || ""} onChange={(e) => set("cta", { ...(block.cta as object), label: e.target.value })} /></Field>
          <Field label="Destino"><input className="input" value={(block.cta as { to?: string })?.to || ""} onChange={(e) => set("cta", { ...(block.cta as object), to: e.target.value })} /></Field>
        </div>
      )}
      {"cols" in block && (block.cols as { title: string; text: string }[]).map((c, i) => (
        <div key={i} className="grid-2">
          <Field label={`Columna ${i + 1}`}><input className="input" value={c.title} onChange={(e) => set("cols", (block.cols as { title: string; text: string }[]).map((x, k) => (k === i ? { ...x, title: e.target.value } : x)))} /></Field>
          <Field label="Texto"><input className="input" value={c.text} onChange={(e) => set("cols", (block.cols as { title: string; text: string }[]).map((x, k) => (k === i ? { ...x, text: e.target.value } : x)))} /></Field>
        </div>
      ))}
      {"images" in block && <Field label="Imágenes"><GalleryField value={(block.images as string[]).map((url) => ({ url }))} onChange={(v) => set("images", v.map((x) => x.url))} /></Field>}
      {"reverse" in block && <label style={{ fontSize: 13, display: "flex", gap: 8 }}><input type="checkbox" checked={!!block.reverse} onChange={(e) => set("reverse", e.target.checked)} />Imagen a la derecha</label>}
    </div>
  );
}

const toText = (r: Rate): RateText => ({ name: r.name, amount: formatCRCInput(r.amount), per_kg: formatCRCInput(r.per_kg ?? 0), overhead_pct: String(r.overhead_pct ?? 0), active: r.active });

/** Tarifas de envio (O1-11a). «Agregar» abre una fila en borrador que no toca la tienda hasta «Guardar tarifa»;
    los montos se leen en formato de Costa Rica (parseCRC) y una tarifa activa de ₡0 que no es de retiro se confirma. */
function ShippingRates({ rates, onSave }: { rates: Rate[]; onSave: (r: Rate[]) => Promise<boolean> }) {
  const [draft, setDraft] = useState<RateText | null>(null);
  const [draftErr, setDraftErr] = useState<Partial<Record<keyof RateText, string>>>({});
  const saveDraft = async () => {
    if (!draft) return;
    const c = checkRate(draft, rates);
    if (!c.ok) { setDraftErr(c.errors); return; }
    if (freeEverywhere(c.rate) && !confirm(FREE_CONFIRM)) return;
    if (await onSave([...rates, c.rate])) { setDraft(null); setDraftErr({}); }
  };
  return (
    <Card title="Tarifas de envío" extra={<button className="btn btn--ghost btn--sm" onClick={() => { setDraft({ name: "", amount: "", per_kg: "0", overhead_pct: "0", active: true }); setDraftErr({}); }} disabled={!!draft}><Icon d={I.plus} />Agregar</button>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(rates.length > 0 || draft) && <div className="meta ship-row ship-row--head"><span>Nombre</span><span>Base ₡</span><span>₡ por kg</span><span>Recargo %</span><span /><span /></div>}
        {rates.map((r) => (
          <RateRow key={r.id || r.name} rate={r} others={rates.filter((x) => x !== r)}
            onChange={(nr) => onSave(rates.map((x) => (x === r ? { ...r, ...nr } : x)))}
            onRemove={() => { if (confirm(`¿Quitar la tarifa «${r.name}»? Deja de aparecer en el checkout.`)) void onSave(rates.filter((x) => x !== r)); }} />
        ))}
        {draft && (
          <div style={{ display: "grid", gap: 8, padding: 10, border: "1px dashed var(--hair)", borderRadius: 10 }}>
            <RateFields value={draft} errors={draftErr} onChange={setDraft} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn btn--crimson btn--sm" onClick={saveDraft}><Icon d={I.check} />Guardar tarifa</button>
              <button className="btn btn--ghost btn--sm" onClick={() => { setDraft(null); setDraftErr({}); }}>Descartar</button>
            </div>
          </div>
        )}
        <p className="muted" style={{ fontSize: 12 }}>Tarifa fija, o por peso estilo Correos de Costa Rica: (base + ₡/kg × peso del pedido, mínimo 1 kg) + recargo. El peso sale de cada producto. Se factura como una línea con IVA 13 %, igual que en el pedido. Escriba los montos en colones, por ejemplo 4.700.</p>
      </div>
    </Card>
  );
}

function RateFields({ value: t, errors, onChange, onCommit, tail }: { value: RateText; errors: Partial<Record<keyof RateText, string>>; onChange: (t: RateText) => void; onCommit?: (t: RateText) => void; tail?: ReactNode }) {
  const field = (k: "name" | "amount" | "per_kg" | "overhead_pct", label: string, mono: boolean, title?: string) => (
    <input className={`input${mono ? " input--mono" : ""}`} aria-label={label} title={title} value={t[k]} inputMode={mono ? "decimal" : undefined}
      aria-invalid={errors[k] ? true : undefined} onChange={(e) => onChange({ ...t, [k]: e.target.value })} onBlur={() => onCommit?.(t)} />
  );
  const msgs = Object.values(errors).filter(Boolean);
  return (
    <>
      <div className="ship-row">
        {field("name", "Nombre de la tarifa", false)}
        {field("amount", "Base en colones", true)}
        {field("per_kg", "Colones por kilo", true, "Correos de CR: monto por kilo (mínimo 1 kg)")}
        {field("overhead_pct", "Recargo en porcentaje", true, "Recargo porcentual (manejo, seguro)")}
        <label style={{ fontSize: 12, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={t.active} onChange={(e) => { const n = { ...t, active: e.target.checked }; onChange(n); onCommit?.(n); }} />Activa</label>
        {tail ?? <span />}
      </div>
      {msgs.length > 0 && <p role="alert" style={{ margin: 0, fontSize: 12, color: "var(--crimson-dark, #b3172a)" }}>{msgs.join(" · ")}</p>}
    </>
  );
}

function RateRow({ rate, others, onChange, onRemove }: { rate: Rate; others: Rate[]; onChange: (r: Partial<Rate>) => Promise<boolean>; onRemove: () => void }) {
  const [t, setT] = useState<RateText>(() => toText(rate));
  const [errors, setErrors] = useState<Partial<Record<keyof RateText, string>>>({});
  useEffect(() => { setT(toText(rate)); setErrors({}); }, [rate]);
  const commit = (n: RateText) => {
    const c = checkRate(n, others);
    if (!c.ok) { setErrors(c.errors); return; }
    setErrors({});
    const same = c.rate.name === rate.name && c.rate.amount === rate.amount && c.rate.per_kg === (rate.per_kg ?? 0) && c.rate.overhead_pct === (rate.overhead_pct ?? 0) && c.rate.active === rate.active;
    if (same) return;
    if (freeEverywhere(c.rate) && !freeEverywhere(rate) && !confirm(FREE_CONFIRM)) { setT(toText(rate)); return; }
    void onChange(c.rate);
  };
  return (
    <div style={{ display: "grid", gap: 4 }}>
      <RateFields value={t} errors={errors} onChange={setT} onCommit={commit} tail={<button className="x" onClick={onRemove} aria-label={`Quitar la tarifa ${rate.name}`}><Icon d={I.x} size={14} /></button>} />
      {suspiciousAmount(rate) && <p className="muted" style={{ margin: 0, fontSize: 12, color: "var(--warn, #9a6200)" }}>Revise este monto: ₡{formatCRCInput(rate.amount)} es muy poco para un envío. Si quiso decir ₡{formatCRCInput(Math.round(rate.amount * 1000))}, corríjalo aquí.</p>}
    </div>
  );
}

const BIZ_FIELDS: [keyof Business, string, string][] = [
  ["legal_name", "Razón social", "Grupo Empresarial Ejemplo SRL"],
  ["legal_id", "Cédula jurídica", "3-102-000000"],
  ["phone", "Teléfono", "+506 0000-0000"],
  ["email", "Correo", "ventas@ejemplo.com"],
  ["hours", "Horario", "lunes a viernes de 9:00 a. m. a 6:00 p. m."],
  ["address", "Dirección (opcional)", "Provincia, cantón y señas"],
];
/* normaliza una razon social para compararla con el titular del SINPE (mayusculas, tildes, puntos de S.R.L.) */
const normName = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "");

/** Datos del comercio (O1-17): quien vende. Salen en el pie de la tienda, en el checkout y en los correos. */
function BusinessCard({ value, onSave }: { value: Business; onSave: (b: Business) => Promise<boolean> }) {
  const [holder, setHolder] = useState<string>("");
  useEffect(() => {
    api<{ methods?: { sinpe?: { holder?: string; active?: boolean } } }>("/store/checkout")
      .then((c) => setHolder(c.methods?.sinpe?.active ? (c.methods.sinpe.holder || "").trim() : ""))
      .catch(() => setHolder(""));
  }, []);
  const legal = (value.legal_name || "").trim();
  const mismatch = !!holder && !!legal && normName(holder) !== normName(legal);
  const preview = businessLines(value);
  return (
    <Card title="Datos del comercio">
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>Estos datos aparecen en el pie de la tienda, en el checkout y en los correos. Sus clientes los buscan antes de pagarle a un comercio que no conocen.</p>
        <div className="grid-2">
          {BIZ_FIELDS.map(([k, label, ph]) => (
            <Field key={k} label={label}>
              <input className="input" defaultValue={value[k] || ""} placeholder={ph} maxLength={k === "address" ? 200 : 120}
                onBlur={(e) => { const v = e.target.value.trim(); if (v !== (value[k] || "")) void onSave({ ...value, [k]: v }); }} />
            </Field>
          ))}
        </div>
        {mismatch && <p role="status" style={{ margin: 0, fontSize: 13, color: "var(--warn, #9a6200)", fontWeight: 600 }}>El titular del SINPE no coincide con la razón social; sus clientes lo van a notar. (Titular: {holder})</p>}
        <div className="meta" style={{ textTransform: "none" }}>Así se ve en el pie: {preview.length ? preview.join(" / ") : "nada todavía (los campos vacíos no se muestran)"}</div>
      </div>
    </Card>
  );
}
