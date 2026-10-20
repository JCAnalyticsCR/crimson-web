/* Mi Tienda -> Vitrina: lo que se ve en la portada de la tienda propia (titular, banners, destacados, kits,
   sellos), las paginas propias (envios, garantias, preguntas frecuentes, terminos y privacidad), la medicion
   (GA4, solo con consentimiento) y los datos de vitrina por producto (precio anterior, ficha PDF, especificaciones). */
import { useEffect, useState } from "react";
import { api, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field, I, Icon, Loading } from "../../ui/components";
import { ImageField } from "../../ui/MediaPicker";
import { AvailBadge } from "../../ui/Availability";

type Banner = { id?: string; image: string; title: string; text: string; link: string; cta: string; position: number; active: boolean };
type Kit = { id: string; title: string; text: string; answers: { place?: string; indoor?: number; outdoor?: number; days?: number; remote?: boolean }; active: boolean };
type Trust = { icon: string; title: string; text: string };
type Faq = { q: string; a: string };
type Showcase = {
  headline: string; subline: string; seo_description: string; ga4_id: string; banners: Banner[]; featured: number[]; kits: Kit[]; trust: Trust[];
  faq: Faq[]; pages: { envios: string; garantias: string }; legal: { terms?: string; privacy?: string };
  /* state/label: disponibilidad de la funcion unica (lo que ve el cliente) */
  featured_products: { id: number; name: string; code: string; show_on_web: boolean; state?: string | null; label?: string | null }[];
  kits_preview?: { id: string; lines: { product_id: number; name: string; quantity: number; state: string | null; label: string | null }[]; missing: string[] }[];
  slug: string; domain: string | null;
};
/* se puede comprar en linea ya (en bodega o bajo pedido con dato vigente) */
const vendible = (state?: string | null) => state === "en_bodega" || state === "bajo_pedido";
type PShow = { id: number; name: string; code: string; price: number | null; currency: string; compare_price: number | null; datasheet_url: string | null; specs: Record<string, string>; derived_specs: Record<string, string>; path: string };

const TABS = ["Portada", "Destacados y kits", "Páginas", "Por producto", "Medición"] as const;

export default function StoreShowcaseAdmin() {
  const { toast } = useSession();
  const [d, setD] = useState<Showcase | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Portada");
  useEffect(() => { api<Showcase>("/store/showcase").then(setD).catch((e) => toast(e.message, "bad")); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const save = (patch: Partial<Showcase>) => api<Showcase>("/store/showcase", { method: "PUT", json: patch }).then((r) => { setD(r); toast("Vitrina actualizada"); }).catch((e) => toast(e.message, "bad"));
  const saveLegal = (legal: { terms?: string; privacy?: string }) => api("/store", { method: "PUT", json: { legal: { ...d?.legal, ...legal } } }).then(() => { setD((x) => (x ? { ...x, legal: { ...x.legal, ...legal } } : x)); toast("Texto legal guardado"); }).catch((e) => toast(e.message, "bad"));
  const base = d?.domain ? `https://${d.domain.replace(/^https?:\/\//, "")}` : d ? `/tienda/${d.slug}` : "";

  return (
    <Card title="Vitrina de la tienda" extra={d && <a className="btn btn--ghost btn--sm" href={d.domain ? base : `${base}`} target="_blank" rel="noopener"><Icon d={I.store} />Ver vitrina</a>}>
      {!d ? <Loading /> : (
        <div style={{ display: "grid", gap: 14 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }} role="tablist">
            {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={`btn btn--sm ${tab === t ? "btn--crimson" : "btn--ghost"}`} onClick={() => setTab(t)}>{t}</button>)}
          </div>

          {tab === "Portada" && (
            <div style={{ display: "grid", gap: 12 }}>
              <div className="grid-2">
                <Field label="Titular de la portada" hint="Vacío = “Equipo profesional de seguridad, listo para instalar.”"><input className="input" defaultValue={d.headline} maxLength={120} onBlur={(e) => e.target.value !== d.headline && save({ headline: e.target.value })} /></Field>
                <Field label="Bajada"><input className="input" defaultValue={d.subline} maxLength={300} onBlur={(e) => e.target.value !== d.subline && save({ subline: e.target.value })} /></Field>
              </div>
              <Field label="Descripción para Google y WhatsApp (portada)"><input className="input" defaultValue={d.seo_description} maxLength={300} onBlur={(e) => e.target.value !== d.seo_description && save({ seo_description: e.target.value })} /></Field>
              <BannersEditor value={d.banners} onSave={(banners) => save({ banners })} />
              <TrustEditor value={d.trust} onSave={(trust) => save({ trust })} />
            </div>
          )}

          {tab === "Destacados y kits" && (
            <div style={{ display: "grid", gap: 16 }}>
              <FeaturedEditor ids={d.featured} items={d.featured_products} onSave={(featured) => save({ featured })} />
              <KitsEditor value={d.kits} preview={d.kits_preview || []} onSave={(kits) => save({ kits })} />
            </div>
          )}

          {tab === "Páginas" && (
            <div style={{ display: "grid", gap: 12 }}>
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Párrafos separados por una línea en blanco. Una primera línea corta (sin punto) se muestra como subtítulo.</p>
              <Field label="Envíos y devoluciones"><textarea className="textarea" rows={8} defaultValue={d.pages.envios} onBlur={(e) => e.target.value !== d.pages.envios && save({ pages: { ...d.pages, envios: e.target.value } })} /></Field>
              <Field label="Garantías"><textarea className="textarea" rows={8} defaultValue={d.pages.garantias} onBlur={(e) => e.target.value !== d.pages.garantias && save({ pages: { ...d.pages, garantias: e.target.value } })} /></Field>
              <Field label="Términos y condiciones" hint="El importador de Fygaro trae el texto que tenía la tienda."><textarea className="textarea" rows={8} defaultValue={d.legal.terms || ""} onBlur={(e) => e.target.value !== (d.legal.terms || "") && saveLegal({ terms: e.target.value })} /></Field>
              <Field label="Política de privacidad"><textarea className="textarea" rows={8} defaultValue={d.legal.privacy || ""} onBlur={(e) => e.target.value !== (d.legal.privacy || "") && saveLegal({ privacy: e.target.value })} /></Field>
              <FaqEditor value={d.faq} onSave={(faq) => save({ faq })} />
            </div>
          )}

          {tab === "Por producto" && <ProductShowcase />}

          {tab === "Medición" && (
            <div style={{ display: "grid", gap: 10, maxWidth: 560 }}>
              <Field label="Google Analytics 4 (id de medición)" hint="Fygaro usaba G-0PQ0N2M5TF. Vacío = sin medición. Con id, la tienda pregunta a cada visitante y solo carga Analytics si acepta."><input className="input input--mono" style={{ textAlign: "left" }} defaultValue={d.ga4_id} placeholder="G-XXXXXXXXXX" onBlur={(e) => e.target.value.trim().toUpperCase() !== d.ga4_id && save({ ga4_id: e.target.value.trim().toUpperCase() })} /></Field>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const row: React.CSSProperties = { border: "1px solid var(--hair)", borderRadius: 10, padding: 10, display: "grid", gap: 8 };

function BannersEditor({ value, onSave }: { value: Banner[]; onSave: (v: Banner[]) => void }) {
  const [list, setList] = useState<Banner[]>(value);
  useEffect(() => setList(value), [value]);
  const upd = (i: number, p: Partial<Banner>) => setList(list.map((b, k) => (k === i ? { ...b, ...p } : b)));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <b style={{ fontSize: 13 }}>Banners (hasta 3 visibles; el primero es el grande)</b>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setList([...list, { image: "", title: "Nuevo banner", text: "", link: "/catalogo", cta: "Ver más", position: list.length + 1, active: true }])} disabled={list.length >= 12}><Icon d={I.plus} />Banner</button>
          <button className="btn btn--crimson btn--sm" onClick={() => onSave(list)}><Icon d={I.check} />Guardar banners</button>
        </div>
      </div>
      {list.length === 0 && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Sin banners: la portada muestra el titular, las categorías y los destacados.</p>}
      {list.map((b, i) => (
        <div key={b.id || i} style={row}>
          <div className="grid-2">
            <Field label="Título"><input className="input" value={b.title} maxLength={120} onChange={(e) => upd(i, { title: e.target.value })} /></Field>
            <Field label="Texto"><input className="input" value={b.text} maxLength={300} onChange={(e) => upd(i, { text: e.target.value })} /></Field>
            <Field label="Enlace" hint="/catalogo, /c/videovigilancia, /p/… o https://…"><input className="input" value={b.link} onChange={(e) => upd(i, { link: e.target.value })} /></Field>
            <Field label="Texto del botón"><input className="input" value={b.cta} maxLength={40} onChange={(e) => upd(i, { cta: e.target.value })} /></Field>
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "end", flexWrap: "wrap" }}>
            <div style={{ minWidth: 220 }}><ImageField value={b.image || null} label="Imagen" onChange={(url) => upd(i, { image: url || "" })} /></div>
            <Field label="Orden"><input className="input input--mono" type="number" style={{ width: 80 }} value={b.position} onChange={(e) => upd(i, { position: Number(e.target.value) })} /></Field>
            <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={b.active} onChange={(e) => upd(i, { active: e.target.checked })} />Activo</label>
            <button className="btn btn--danger btn--sm" onClick={() => setList(list.filter((_, k) => k !== i))}>Quitar</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function TrustEditor({ value, onSave }: { value: Trust[]; onSave: (v: Trust[]) => void }) {
  const [list, setList] = useState<Trust[]>(value);
  useEffect(() => setList(value), [value]);
  const upd = (i: number, p: Partial<Trust>) => setList(list.map((b, k) => (k === i ? { ...b, ...p } : b)));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}><b style={{ fontSize: 13 }}>Sellos de confianza</b><button className="btn btn--crimson btn--sm" onClick={() => onSave(list)}><Icon d={I.check} />Guardar sellos</button></div>
      {list.map((t, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: "120px 1fr 2fr", gap: 8 }}>
          <select className="select" value={t.icon} onChange={(e) => upd(i, { icon: e.target.value })}>{["shield", "tool", "truck", "doc", "chat", "clock"].map((x) => <option key={x}>{x}</option>)}</select>
          <input className="input" value={t.title} maxLength={60} onChange={(e) => upd(i, { title: e.target.value })} />
          <input className="input" value={t.text} maxLength={200} onChange={(e) => upd(i, { text: e.target.value })} />
        </div>
      ))}
    </div>
  );
}

function ProductPicker({ onPick, placeholder = "Buscar producto por nombre o código" }: { onPick: (p: Product) => void; placeholder?: string }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Product[]>([]);
  const [todos, setTodos] = useState(false); // por defecto solo propone lo que se puede comprar
  const shown = todos ? res : res.filter((p) => p.item_type !== "producto" || vendible(p.availability?.state));
  useEffect(() => {
    if (q.trim().length < 2) { setRes([]); return; }
    const t = window.setTimeout(() => api<{ items: Product[] }>(`/products?q=${encodeURIComponent(q.trim())}&limit=8`).then((r) => setRes(r.items)).catch(() => setRes([])), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  return (
    <div style={{ position: "relative" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input className="input" style={{ flex: "1 1 220px" }} value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} />
        <label style={{ fontSize: 12.5, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={todos} onChange={(e) => setTodos(e.target.checked)} />Incluir lo que no se puede comprar</label>
      </div>
      {q.trim().length >= 2 && res.length > 0 && shown.length === 0 && <p className="meta" style={{ margin: "6px 0 0" }}>Nada vendible con esa búsqueda (sin existencia ni proveedor vigente). Marcá «Incluir lo que no se puede comprar» para verlo igual.</p>}
      {shown.length > 0 && (
        <div style={{ position: "absolute", zIndex: 5, left: 0, right: 0, top: 40, background: "var(--surface)", border: "1px solid var(--hair-2)", borderRadius: 10, boxShadow: "0 12px 30px -16px rgba(0,0,0,.4)" }}>
          {shown.map((p) => <button key={p.id} className="nav__link" style={{ width: "100%", textAlign: "left", border: 0, background: "none", cursor: "pointer", color: "var(--text)", display: "block", padding: "8px 12px" }} onClick={() => { onPick(p); setQ(""); setRes([]); }}><b style={{ fontSize: 13 }}>{p.name}</b> <span className="meta">{p.code}{!p.show_on_web ? " · no publicado" : ""}</span> {p.item_type === "producto" && <AvailBadge a={p.availability} />}</button>)}
        </div>
      )}
    </div>
  );
}

function FeaturedEditor({ ids, items, onSave }: { ids: number[]; items: Showcase["featured_products"]; onSave: (v: number[]) => void }) {
  const [list, setList] = useState(items);
  useEffect(() => setList(items), [items]);
  const move = (i: number, dir: -1 | 1) => { const b = [...list]; const j = i + dir; if (j < 0 || j >= b.length) return; [b[i], b[j]] = [b[j], b[i]]; setList(b); };
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Productos destacados (“Lo más pedido”)</b>
        <button className="btn btn--crimson btn--sm" onClick={() => onSave(list.map((x) => x.id))} disabled={JSON.stringify(list.map((x) => x.id)) === JSON.stringify(ids)}><Icon d={I.check} />Guardar destacados</button>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Vacío = la tienda elige sola uno o dos por categoría, con foto y precio. Se muestran hasta 8.</p>
      <ProductPicker onPick={(p) => !list.some((x) => x.id === p.id) && list.length < 24 && setList([...list, { id: p.id, name: p.name, code: p.code, show_on_web: p.show_on_web, state: p.availability?.state, label: p.availability?.label_publico || p.availability?.label }])} />
      {list.map((p, i) => (
        <div key={p.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <span className="meta" style={{ width: 22 }}>{i + 1}</span>
          <span style={{ flex: 1, minWidth: 0 }}>{p.name} <span className="meta">{p.code}</span>{!p.show_on_web && <span className="badge badge--muted" style={{ marginLeft: 6 }}>no publicado</span>}{p.state && <span style={{ marginLeft: 6 }} title={p.label || undefined}><AvailBadge a={{ state: p.state, label: p.label || "" }} /></span>}{p.state && !vendible(p.state) && <span className="meta" style={{ marginLeft: 6, color: "var(--warn)" }}>no se puede comprar en línea</span>}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => move(i, -1)} aria-label="Subir">↑</button>
          <button className="btn btn--ghost btn--sm" onClick={() => move(i, 1)} aria-label="Bajar">↓</button>
          <button className="x" onClick={() => setList(list.filter((x) => x.id !== p.id))} aria-label="Quitar"><Icon d={I.x} size={14} /></button>
        </div>
      ))}
    </div>
  );
}

function KitsEditor({ value, preview, onSave }: { value: Kit[]; preview: NonNullable<Showcase["kits_preview"]>; onSave: (v: Kit[]) => void }) {
  const [list, setList] = useState<Kit[]>(value);
  useEffect(() => setList(value), [value]);
  const upd = (i: number, p: Partial<Kit>) => setList(list.map((k, j) => (j === i ? { ...k, ...p } : k)));
  const ans = (i: number, p: Kit["answers"]) => upd(i, { answers: { ...list[i].answers, ...p } });
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Kits por necesidad</b>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setList([...list, { id: `kit-${list.length + 1}`, title: "Nuevo kit", text: "", answers: { place: "casa", indoor: 2, outdoor: 2, days: 15, remote: true }, active: true }])} disabled={list.length >= 8}><Icon d={I.plus} />Kit</button>
          <button className="btn btn--crimson btn--sm" onClick={() => onSave(list)}><Icon d={I.check} />Guardar kits</button>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Cada kit se arma con el configurador “Arma tu sistema”: los productos y el precio salen del catálogo vigente, nunca quedan desactualizados. Primero propone lo que se puede comprar (en bodega o bajo pedido).</p>
      {list.map((k, i) => (
        <div key={i} style={row}>
          <div className="grid-2">
            <Field label="Título"><input className="input" value={k.title} maxLength={80} onChange={(e) => upd(i, { title: e.target.value })} /></Field>
            <Field label="Texto"><input className="input" value={k.text} maxLength={200} onChange={(e) => upd(i, { text: e.target.value })} /></Field>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "end" }}>
            <Field label="Lugar"><select className="select" value={k.answers.place || "casa"} onChange={(e) => ans(i, { place: e.target.value })}>{["casa", "negocio", "condominio", "bodega"].map((x) => <option key={x}>{x}</option>)}</select></Field>
            <Field label="Adentro"><input className="input input--mono" type="number" min={0} max={32} style={{ width: 80 }} value={k.answers.indoor ?? 0} onChange={(e) => ans(i, { indoor: Number(e.target.value) })} /></Field>
            <Field label="Afuera"><input className="input input--mono" type="number" min={0} max={32} style={{ width: 80 }} value={k.answers.outdoor ?? 0} onChange={(e) => ans(i, { outdoor: Number(e.target.value) })} /></Field>
            <Field label="Días"><select className="select" value={k.answers.days ?? 15} onChange={(e) => ans(i, { days: Number(e.target.value) })}>{[7, 15, 30, 60].map((x) => <option key={x}>{x}</option>)}</select></Field>
            <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={k.active} onChange={(e) => upd(i, { active: e.target.checked })} />Activo</label>
            <button className="btn btn--danger btn--sm" onClick={() => setList(list.filter((_, j) => j !== i))}>Quitar</button>
          </div>
          {(() => {
            const pv = preview.find((x) => x.id === k.id);
            if (!pv) return null;
            const malos = pv.lines.filter((ln) => !vendible(ln.state));
            return (
              <div className="meta" style={{ display: "grid", gap: 3 }}>
                <span>Hoy arma: {pv.lines.length ? pv.lines.map((ln) => `${ln.quantity} × ${ln.name}`).join(" · ") : "nada (sin productos que calcen)"}{pv.missing.length ? ` · para cotizar: ${pv.missing.join(", ")}` : ""}</span>
                {malos.length > 0 && <span style={{ color: "var(--warn)" }}>Sin existencia ni proveedor vigente: {malos.map((ln) => ln.name).join(", ")} (el cliente lo consulta por WhatsApp)</span>}
              </div>
            );
          })()}
        </div>
      ))}
    </div>
  );
}

function FaqEditor({ value, onSave }: { value: Faq[]; onSave: (v: Faq[]) => void }) {
  const [list, setList] = useState<Faq[]>(value);
  useEffect(() => setList(value), [value]);
  const upd = (i: number, p: Partial<Faq>) => setList(list.map((f, j) => (j === i ? { ...f, ...p } : f)));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <b style={{ fontSize: 13 }}>Preguntas frecuentes</b>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setList([...list, { q: "", a: "" }])} disabled={list.length >= 40}><Icon d={I.plus} />Pregunta</button>
          <button className="btn btn--crimson btn--sm" onClick={() => onSave(list.filter((f) => f.q.trim() && f.a.trim()))}><Icon d={I.check} />Guardar preguntas</button>
        </div>
      </div>
      {list.map((f, i) => (
        <div key={i} style={row}>
          <input className="input" value={f.q} placeholder="Pregunta" maxLength={200} onChange={(e) => upd(i, { q: e.target.value })} />
          <textarea className="textarea" value={f.a} placeholder="Respuesta" maxLength={2000} onChange={(e) => upd(i, { a: e.target.value })} />
          <button className="btn btn--danger btn--sm" style={{ justifySelf: "start" }} onClick={() => setList(list.filter((_, j) => j !== i))}>Quitar</button>
        </div>
      ))}
    </div>
  );
}

function ProductShowcase() {
  const { toast } = useSession();
  const [p, setP] = useState<PShow | null>(null);
  const [specs, setSpecs] = useState<[string, string][]>([]);
  const open = (id: number) => api<PShow>(`/store/showcase/products/${id}`).then((r) => { setP(r); setSpecs(Object.entries(r.specs)); });
  const save = (patch: Partial<{ compare_price: number | null; datasheet_url: string; specs: Record<string, string> }>) => p && api<PShow>(`/store/showcase/products/${p.id}`, { method: "PUT", json: patch }).then((r) => { setP(r); setSpecs(Object.entries(r.specs)); toast("Producto actualizado"); }).catch((e) => toast(e.message, "bad"));
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <ProductPicker onPick={(x) => open(x.id)} placeholder="Elegí el producto a editar (nombre o código)" />
      {p && (
        <div style={{ ...row, gap: 12 }}>
          <div><b>{p.name}</b> <span className="meta">{p.code} · {p.path}</span></div>
          <div className="grid-2">
            {/* sin catalog.precios el API manda price y compare_price en null: no se muestra (ni se borra al salir del campo) */}
            {p.price !== null && <Field label={`Precio anterior SIN IVA (${p.currency})`} hint={`Precio actual sin IVA: ${p.price}. Si el anterior es mayor, la tienda lo muestra tachado.`}><input key={`c${p.id}`} className="input input--mono" defaultValue={p.compare_price ?? ""} onBlur={(e) => save({ compare_price: e.target.value.trim() ? Number(e.target.value) : null })} /></Field>}
            <Field label="Ficha técnica (PDF)" hint="Enlace https:// al PDF del fabricante."><input key={`d${p.id}`} className="input" defaultValue={p.datasheet_url || ""} onBlur={(e) => save({ datasheet_url: e.target.value.trim() })} /></Field>
          </div>
          <div style={{ display: "grid", gap: 6 }}>
            <b style={{ fontSize: 13 }}>Especificaciones</b>
            <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>La tienda deduce estas del nombre y la descripción: {Object.entries(p.derived_specs).map(([k, v]) => `${k}: ${v}`).join(" · ") || "ninguna"}. Lo que agregues aquí manda sobre lo deducido.</p>
            {specs.map(([k, v], i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto", gap: 6 }}>
                <input className="input" value={k} placeholder="Garantía" onChange={(e) => setSpecs(specs.map((s, j) => (j === i ? [e.target.value, s[1]] : s)))} />
                <input className="input" value={v} placeholder="2 años" onChange={(e) => setSpecs(specs.map((s, j) => (j === i ? [s[0], e.target.value] : s)))} />
                <button className="x" onClick={() => setSpecs(specs.filter((_, j) => j !== i))} aria-label="Quitar"><Icon d={I.x} size={14} /></button>
              </div>
            ))}
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn btn--ghost btn--sm" onClick={() => setSpecs([...specs, ["", ""]])}><Icon d={I.plus} />Especificación</button>
              <button className="btn btn--crimson btn--sm" onClick={() => save({ specs: Object.fromEntries(specs.filter(([k, v]) => k.trim() && v.trim())) })}><Icon d={I.check} />Guardar especificaciones</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
