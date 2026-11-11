/* Mi Tienda -> Vitrina: lo que se ve en la portada de la tienda propia (titular, banners, destacados, kits,
   sellos), las paginas propias (envios, garantias, preguntas frecuentes, terminos y privacidad) y los datos de vitrina
   por producto (precio anterior, ficha PDF, especificaciones). Sin medicion: nada de Google ni de terceros (EX-NOGA). */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtMoney, type Product } from "../../lib/api";
import { parseMonto } from "../../lib/parseCRC";
import { useSession } from "../../app/session";
import { Card, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { ImageField } from "../../ui/MediaPicker";
import { AvailBadge } from "../../ui/Availability";

type Banner = { id?: string; image: string; title: string; text: string; link: string; cta: string; position: number; active: boolean };
/* photos: fotos fijas que Andres eligio para la tarjeta (vacio = los equipos que el kit arma hoy) */
type KitPhotoSel = { product_id: number; image: string };
/* pins: producto fijo por pieza ({nvr: 123}); sin la llave = Automatico. extras: lineas adicionales (p. ej. una UPS) */
type KitExtra = { product_id: number; quantity: number };
type Kit = { id: string; title: string; text: string; answers: { place?: string; indoor?: number; outdoor?: number; days?: number; remote?: boolean; quality?: string; night?: string; record?: string; distance?: string; audio?: boolean; smart?: boolean; growth?: boolean; ups?: boolean }; active: boolean; photos?: KitPhotoSel[]; pins?: Record<string, number>; extras?: KitExtra[] };
type KitPhotoPv = { product_id: number; image: string; label: string; name: string; images: string[] };
/* vista previa de la composicion: cada pieza con su producto de hoy (source auto | pin | missing) y los candidatos que calzan */
type KitRolePv = { key: string; label: string; product_id: number | null; name: string | null; source: string; options: { id: number; name: string; code: string; price: number | string }[] };
type KitPinPv = { key: string; label: string; product_id: number; name: string | null; ok: boolean; used: boolean; reason: string | null };
type KitExtraPv = { product_id: number; quantity: number; name: string | null; ok: boolean; reason: string | null };
type KitPv = {
  id: string; photos?: KitPhotoPv[]; photos_manual?: boolean; lines: { product_id: number; name: string; quantity: number; state: string | null; label: string | null; key?: string; source?: string }[]; missing: string[];
  total?: number | string; currency?: string; roles?: KitRolePv[]; pins?: KitPinPv[]; extras?: KitExtraPv[];
};
const ROLE_NAMES: Record<string, string> = { cam_out: "Cámaras exteriores", cam_in: "Cámaras interiores", nvr: "Grabador (NVR)", disk: "Disco duro", switch: "Switch PoE", rj45: "Conectores RJ45", cable: "Cable UTP", box: "Caja de paso exterior" };
type Trust = { icon: string; title: string; text: string };
type Faq = { q: string; a: string };
type Showcase = {
  headline: string; subline: string; seo_description: string; banners: Banner[]; featured: number[]; kits: Kit[]; trust: Trust[];
  /* O2-08 (aditivo: un API viejo no lo manda): producto de portada y hasta 5 de respaldo */
  hero?: number[]; hero_products?: { id: number; name: string; code: string; show_on_web: boolean; state?: string | null; label?: string | null }[];
  faq: Faq[]; pages: { envios: string; garantias: string }; legal: { terms?: string; privacy?: string };
  /* state/label: disponibilidad de la funcion unica (lo que ve el cliente) */
  featured_products: { id: number; name: string; code: string; show_on_web: boolean; state?: string | null; label?: string | null }[];
  kits_preview?: KitPv[];
  slug: string; domain: string | null;
  /* al guardar: lo que no se pudo fijar (de otra empresa o que nunca sirve para esa pieza) */
  warnings?: string[];
};
/* se puede comprar en linea ya (en bodega o bajo pedido con dato vigente) */
const vendible = (state?: string | null) => state === "en_bodega" || state === "bajo_pedido";
type PShow = { id: number; name: string; code: string; price: number | null; currency: string; compare_price: number | null; datasheet_url: string | null; specs: Record<string, string>; derived_specs: Record<string, string>; path: string };

const TABS = ["Portada", "Destacados y kits", "Páginas", "Por producto"] as const; // EX-NOGA: sin «Medición» (nada de Google)

export default function StoreShowcaseAdmin() {
  const { toast } = useSession();
  const [d, setD] = useState<Showcase | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Portada");
  const [avisos, setAvisos] = useState<string[]>([]);
  /* O1-11b: editores con cambios sin guardar; cambiar de pestana los descarta, asi que antes se avisa */
  const [sucios, setSucios] = useState<Record<string, boolean>>({});
  const onDirty = useCallback<OnDirty>((k, v) => setSucios((x) => (!!x[k] === v ? x : { ...x, [k]: v })), []);
  const [irA, setIrA] = useState<(typeof TABS)[number] | null>(null);
  const pendientes = Object.entries(sucios).filter(([, v]) => v).map(([k]) => SECCION[k] || k);
  const cambiarTab = (t: (typeof TABS)[number]) => { if (t === tab) return; if (pendientes.length) setIrA(t); else setTab(t); }; // lo que el ultimo guardado de kits no pudo fijar: queda a la vista hasta el siguiente
  useEffect(() => { api<Showcase>("/store/showcase").then(setD).catch((e) => toast(e.message, "bad")); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const save = (patch: Partial<Showcase>) => api<Showcase>("/store/showcase", { method: "PUT", json: patch }).then((r) => {
    setD(r);
    // los avisos son oraciones largas (una por producto que no se fijo): quedan escritos junto a «Guardar kits»; el
    // aviso flotante solo dice que los hay
    if ("kits" in patch) setAvisos(r.warnings || []);
    if (r.warnings?.length && "kits" in patch) toast(`Vitrina actualizada con ${r.warnings.length === 1 ? "1 aviso" : `${r.warnings.length} avisos`}: véalos sobre la lista de kits`, "bad");
    else if (r.warnings?.length) toast(`Vitrina actualizada. ${r.warnings.join(" ")}`, "bad"); // O1-04 d: banner de relleno desactivado
    else toast("Vitrina actualizada");
  }).catch((e) => toast(e.message, "bad"));
  const saveLegal = (legal: { terms?: string; privacy?: string }) => api("/store", { method: "PUT", json: { legal: { ...d?.legal, ...legal } } }).then(() => { setD((x) => (x ? { ...x, legal: { ...x.legal, ...legal } } : x)); toast("Texto legal guardado"); }).catch((e) => toast(e.message, "bad"));
  const base = d?.domain ? `https://${d.domain.replace(/^https?:\/\//, "")}` : d ? `/tienda/${d.slug}` : "";

  return (
    <Card title="Vitrina de la tienda" extra={d && <a className="btn btn--ghost btn--sm" href={d.domain ? base : `${base}`} target="_blank" rel="noopener"><Icon d={I.store} />Ver vitrina</a>}>
      {!d ? <Loading /> : (
        <div style={{ display: "grid", gap: 14 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }} role="tablist">
            {TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} className={`btn btn--sm ${tab === t ? "btn--crimson" : "btn--ghost"}`} onClick={() => cambiarTab(t)}>{t}</button>)}
          </div>

          {tab === "Portada" && (
            <div style={{ display: "grid", gap: 12 }}>
              <div className="grid-2">
                <Field label="Titular de la portada" hint="Vacío = “Protegé tu casa o negocio con equipo profesional.” (propuesta, a aprobar)"><input className="input" defaultValue={d.headline} maxLength={120} onBlur={(e) => e.target.value !== d.headline && save({ headline: e.target.value })} /></Field>
                <Field label="Bajada"><input className="input" defaultValue={d.subline} maxLength={300} onBlur={(e) => e.target.value !== d.subline && save({ subline: e.target.value })} /></Field>
              </div>
              <Field label="Descripción para Google y WhatsApp (portada)"><input className="input" defaultValue={d.seo_description} maxLength={300} onBlur={(e) => e.target.value !== d.seo_description && save({ seo_description: e.target.value })} /></Field>
              <HeroEditor ids={d.hero || []} items={d.hero_products || []} onSave={(hero) => save({ hero })} onDirty={onDirty} />
              <BannersEditor value={d.banners} onSave={(banners) => save({ banners })} onDirty={onDirty} />
              <TrustEditor value={d.trust} onSave={(trust) => save({ trust })} onDirty={onDirty} />
            </div>
          )}

          {tab === "Destacados y kits" && (
            <div style={{ display: "grid", gap: 16 }}>
              <FeaturedEditor ids={d.featured} items={d.featured_products} onSave={(featured) => save({ featured })} onDirty={onDirty} />
              <KitsEditor value={d.kits} preview={d.kits_preview || []} avisos={avisos} onSave={(kits) => save({ kits })} />
            </div>
          )}

          {tab === "Páginas" && (
            <div style={{ display: "grid", gap: 12 }}>
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Párrafos separados por una línea en blanco. Una primera línea corta (sin punto) se muestra como subtítulo.</p>
              <Field label="Envíos y devoluciones"><textarea className="textarea" rows={8} defaultValue={d.pages.envios} onBlur={(e) => e.target.value !== d.pages.envios && save({ pages: { ...d.pages, envios: e.target.value } })} /></Field>
              <Field label="Garantías"><textarea className="textarea" rows={8} defaultValue={d.pages.garantias} onBlur={(e) => e.target.value !== d.pages.garantias && save({ pages: { ...d.pages, garantias: e.target.value } })} /></Field>
              <Field label="Términos y condiciones" hint="El importador de Fygaro trae el texto que tenía la tienda."><textarea className="textarea" rows={8} defaultValue={d.legal.terms || ""} onBlur={(e) => e.target.value !== (d.legal.terms || "") && saveLegal({ terms: e.target.value })} /></Field>
              <Field label="Política de privacidad"><textarea className="textarea" rows={8} defaultValue={d.legal.privacy || ""} onBlur={(e) => e.target.value !== (d.legal.privacy || "") && saveLegal({ privacy: e.target.value })} /></Field>
              <FaqEditor value={d.faq} onSave={(faq) => save({ faq })} onDirty={onDirty} />
            </div>
          )}

          {tab === "Por producto" && <ProductShowcase />}
          {irA && (
            <Modal title="Hay cambios sin guardar" onClose={() => setIrA(null)} foot={<><button className="btn btn--ghost" onClick={() => setIrA(null)}>Seguir editando</button><button className="btn btn--danger" onClick={() => { setTab(irA); setIrA(null); }}>Cambiar sin guardar</button></>}>
              <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5 }}>Tiene cambios sin guardar en {pendientes.join(", ")}. Si cambia de pestaña se pierden: guárdelos primero con su botón «Guardar».</p>
            </Modal>
          )}


        </div>
      )}
    </Card>
  );
}

const row: React.CSSProperties = { border: "1px solid var(--hair)", borderRadius: 10, padding: 10, display: "grid", gap: 8 };

type OnDirty = (key: string, dirty: boolean) => void;
/** Borrador de un editor de la vitrina (O1-11b). Antes cada editor se reiniciaba con lo del servidor en CUALQUIER
    guardado (setD de otro editor): escribir un banner y guardar el titular borraba el banner. Ahora lo de afuera solo
    se adopta si el editor no tiene cambios pendientes o si el guardado es el suyo (guardando()). Avisa al padre si
    hay cambios sin guardar, para advertir antes de cambiar de pestaña. */
function useDraft<T>(value: T, key: string, onDirty?: OnDirty): [T, (v: T) => void, () => void] {
  const [draft, setDraft] = useState<T>(value);
  const base = useRef(value);
  const adopt = useRef(false);
  useEffect(() => {
    setDraft((cur) => (adopt.current || JSON.stringify(cur) === JSON.stringify(base.current) ? value : cur));
    adopt.current = false;
    base.current = value;
  }, [value]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(base.current);
  useEffect(() => { onDirty?.(key, dirty); }, [dirty, key, onDirty]);
  useEffect(() => () => onDirty?.(key, false), [key, onDirty]);
  return [draft, setDraft, () => { adopt.current = true; }];
}
const SECCION: Record<string, string> = { banners: "Banners", trust: "Sellos de confianza", featured: "Productos destacados", hero: "Producto de portada", faq: "Preguntas frecuentes" };

/** O1-04 d: sin imagen, o con el titulo vacio o «Nuevo banner»: la tienda no lo pinta (y el API lo guarda inactivo) */
const incompleto = (b: Banner) => !b.image.trim() || ["", "nuevo banner"].includes(b.title.trim().toLowerCase());

function BannersEditor({ value, onSave, onDirty }: { value: Banner[]; onSave: (v: Banner[]) => void; onDirty?: OnDirty }) {
  const [list, setList, guardando] = useDraft<Banner[]>(value, "banners", onDirty);
  const upd = (i: number, p: Partial<Banner>) => setList(list.map((b, k) => (k === i ? { ...b, ...p } : b)));
  return (
    <div style={{ display: "grid", gap: 8 }} data-editor="banners">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <b style={{ fontSize: 13 }}>Banners (hasta 3 visibles; el primero es el grande)</b>
        <div style={{ display: "flex", gap: 6 }}>
          {/* O1-04 d: nace inactivo y sin titulo (antes «Nuevo banner» activo salia publicado tal cual) */}
          <button className="btn btn--ghost btn--sm" onClick={() => setList([...list, { image: "", title: "", text: "", link: "/catalogo", cta: "Ver más", position: list.length + 1, active: false }])} disabled={list.length >= 12}><Icon d={I.plus} />Banner</button>
          <button className="btn btn--crimson btn--sm" onClick={() => { guardando(); onSave(list); }}><Icon d={I.check} />Guardar banners</button>
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
          {incompleto(b) && <p className="meta" role="note" style={{ margin: 0, color: "var(--warn)" }}>Este banner no se muestra hasta que tenga título e imagen.</p>}
        </div>
      ))}
    </div>
  );
}

function TrustEditor({ value, onSave, onDirty }: { value: Trust[]; onSave: (v: Trust[]) => void; onDirty?: OnDirty }) {
  const [list, setList, guardando] = useDraft<Trust[]>(value, "trust", onDirty);
  const upd = (i: number, p: Partial<Trust>) => setList(list.map((b, k) => (k === i ? { ...b, ...p } : b)));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}><b style={{ fontSize: 13 }}>Sellos de confianza</b><button className="btn btn--crimson btn--sm" onClick={() => { guardando(); onSave(list); }}><Icon d={I.check} />Guardar sellos</button></div>
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

/* accept: lo que este buscador deja elegir (p. ej. las fotos de un kit: solo publicados con foto); rejectNote explica
   por que no aparece lo demas */
function ProductPicker({ onPick, placeholder = "Buscar producto por nombre o código", accept, rejectNote }: { onPick: (p: Product) => void; placeholder?: string; accept?: (p: Product) => boolean; rejectNote?: string }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Product[]>([]);
  const [todos, setTodos] = useState(false); // por defecto solo propone lo que se puede comprar
  const ok = accept ? res.filter(accept) : res;
  const shown = todos ? ok : ok.filter((p) => p.item_type !== "producto" || vendible(p.availability?.state));
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
      {q.trim().length >= 2 && res.length > 0 && ok.length < res.length && rejectNote && <p className="meta" style={{ margin: "6px 0 0" }}>{rejectNote}</p>}
      {q.trim().length >= 2 && ok.length > 0 && shown.length === 0 && <p className="meta" style={{ margin: "6px 0 0" }}>Nada vendible con esa búsqueda (sin existencia ni proveedor vigente). Marcá «Incluir lo que no se puede comprar» para verlo igual.</p>}
      {shown.length > 0 && (
        <div style={{ position: "absolute", zIndex: 5, left: 0, right: 0, top: 40, background: "var(--surface)", border: "1px solid var(--hair-2)", borderRadius: 10, boxShadow: "0 12px 30px -16px rgba(0,0,0,.4)" }}>
          {shown.map((p) => <button key={p.id} className="nav__link" style={{ width: "100%", textAlign: "left", border: 0, background: "none", cursor: "pointer", color: "var(--text)", display: "block", padding: "8px 12px" }} onClick={() => { onPick(p); setQ(""); setRes([]); }}><b style={{ fontSize: 13 }}>{p.name}</b> <span className="meta">{p.code}{!p.show_on_web ? " · no publicado" : ""}</span> {p.item_type === "producto" && <AvailBadge a={p.availability} />}</button>)}
        </div>
      )}
    </div>
  );
}

function FeaturedEditor({ ids, items, onSave, onDirty }: { ids: number[]; items: Showcase["featured_products"]; onSave: (v: number[]) => void; onDirty?: OnDirty }) {
  const [list, setList, guardando] = useDraft(items, "featured", onDirty);
  const move = (i: number, dir: -1 | 1) => { const b = [...list]; const j = i + dir; if (j < 0 || j >= b.length) return; [b[i], b[j]] = [b[j], b[i]]; setList(b); };
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Productos destacados de la portada</b>
        <button className="btn btn--crimson btn--sm" onClick={() => { guardando(); onSave(list.map((x) => x.id)); }} disabled={JSON.stringify(list.map((x) => x.id)) === JSON.stringify(ids)}><Icon d={I.check} />Guardar destacados</button>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Con productos elegidos aquí, la portada los titula «Selección» y «Elegidos por nuestro equipo». Vacío = la tienda elige sola uno por tipo de equipo, solo entre lo que se puede comprar en línea y con precio, y los titula «Del catálogo». Se muestran hasta 8.</p>
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

/** O2-08: el producto grande de la portada. Se guarda el elegido y hasta 5 de respaldo: la tienda muestra el primero
    que se pueda comprar ya, con precio y foto; si ninguno, la portada va sin producto ni precio. */
function HeroEditor({ ids, items, onSave, onDirty }: { ids: number[]; items: NonNullable<Showcase["hero_products"]>; onSave: (v: number[]) => void; onDirty?: OnDirty }) {
  const [list, setList, guardando] = useDraft(items, "hero", onDirty);
  const move = (i: number, dir: -1 | 1) => { const b = [...list]; const j = i + dir; if (j < 0 || j >= b.length) return; [b[i], b[j]] = [b[j], b[i]]; setList(b); };
  return (
    <div style={{ display: "grid", gap: 8 }} data-editor="hero">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Producto de portada</b>
        <button className="btn btn--crimson btn--sm" onClick={() => { guardando(); onSave(list.map((x) => x.id)); }} disabled={JSON.stringify(list.map((x) => x.id)) === JSON.stringify(ids)}><Icon d={I.check} />Guardar producto de portada</button>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Elija el producto que va grande en la portada y, si quiere, hasta 5 de respaldo en orden. La tienda muestra el primero que se pueda comprar en línea, con precio y foto; si ninguno se puede comprar, la portada va sin producto.</p>
      <ProductPicker onPick={(p) => !list.some((x) => x.id === p.id) && list.length < 6 && setList([...list, { id: p.id, name: p.name, code: p.code, show_on_web: p.show_on_web, state: p.availability?.state, label: p.availability?.label_publico || p.availability?.label }])} />
      {list.map((p, i) => (
        <div key={p.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <span className="meta" style={{ width: 70 }}>{i === 0 ? "Elegido" : `Respaldo ${i}`}</span>
          <span style={{ flex: 1, minWidth: 0 }}>{p.name} <span className="meta">{p.code}</span>{!p.show_on_web && <span className="badge badge--muted" style={{ marginLeft: 6 }}>no publicado</span>}{p.state && !vendible(p.state) && <span className="meta" style={{ marginLeft: 6, color: "var(--warn)" }}>no se puede comprar en línea</span>}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => move(i, -1)} aria-label="Subir">↑</button>
          <button className="btn btn--ghost btn--sm" onClick={() => move(i, 1)} aria-label="Bajar">↓</button>
          <button className="x" onClick={() => setList(list.filter((x) => x.id !== p.id))} aria-label="Quitar"><Icon d={I.x} size={14} /></button>
        </div>
      ))}
    </div>
  );
}

function KitsEditor({ value, preview, avisos, onSave }: { value: Kit[]; preview: NonNullable<Showcase["kits_preview"]>; avisos: string[]; onSave: (v: Kit[]) => void }) {
  const [list, setList] = useState<Kit[]>(value);
  useEffect(() => setList(value), [value]);
  const upd = (i: number, p: Partial<Kit>) => setList(list.map((k, j) => (j === i ? { ...k, ...p } : k)));
  const ans = (i: number, p: Kit["answers"]) => upd(i, { answers: { ...list[i].answers, ...p } });
  // id propio para cada kit nuevo: con `kit-${largo + 1}` quitar uno y agregar otro repetia el id
  const newId = () => { let n = list.length + 1; while (list.some((k) => k.id === `kit-${n}`)) n++; return `kit-${n}`; };
  // De que kit guardado viene cada fila (null = nuevo, sin guardar). El API devuelve la vista previa en el orden de los
  // kits guardados: se empareja por esa posicion y no por id, asi un kit nunca muestra los candidatos ni el estado de otro.
  const [origin, setOrigin] = useState<(number | null)[]>(() => value.map((_, j) => j));
  useEffect(() => setOrigin(value.map((_, j) => j)), [value]);
  const pvOf = (i: number) => (origin[i] == null ? undefined : preview[origin[i] as number]);
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b style={{ fontSize: 13 }}>Kits por necesidad</b>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => { setList([...list, { id: newId(), title: "Nuevo kit", text: "", answers: { place: "casa", indoor: 2, outdoor: 2, days: 15, remote: true }, active: true }]); setOrigin([...origin, null]); }} disabled={list.length >= 8}><Icon d={I.plus} />Kit</button>
          <button className="btn btn--crimson btn--sm" onClick={() => onSave(list)}><Icon d={I.check} />Guardar kits</button>
        </div>
      </div>
      {avisos.length > 0 && (
        <div role="status" style={{ border: "1px solid var(--warn)", borderRadius: 10, padding: "8px 12px", display: "grid", gap: 4, minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "2px 8px", flexWrap: "wrap" }}>
            <b style={{ fontSize: 12.5, color: "var(--warn)" }}>Se guardó, con {avisos.length === 1 ? "1 aviso" : `${avisos.length} avisos`}</b>
            <span className="meta">Se quitan al volver a guardar los kits.</span>
          </div>
          <ul className="meta" style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 3 }}>
            {avisos.map((w, j) => <li key={j} style={{ overflowWrap: "anywhere" }}>{w}</li>)}
          </ul>
        </div>
      )}
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Cada kit se arma con el configurador “Armá tu sistema”: los productos y el precio salen del catálogo vigente, nunca quedan desactualizados. Primero propone lo que se puede comprar (en bodega o bajo pedido). En cada pieza puede dejar “Automático” (entre equivalentes, el artículo varía de un día a otro) o fijar un producto, y sumar adicionales como una UPS. El cliente igual puede cambiar cada pieza al abrir el kit.</p>
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
            <Field label="Calidad"><select className="select" value={k.answers.quality || "4mp"} onChange={(e) => ans(i, { quality: e.target.value })}><option value="2mp">2 MP</option><option value="4mp">4 MP</option><option value="8mp">8 MP (4K)</option></select></Field>
            <Field label="De noche"><select className="select" value={k.answers.night || "ir"} onChange={(e) => ans(i, { night: e.target.value })}><option value="ir">Infrarrojo</option><option value="color">A color</option></select></Field>
            <Field label="Grabación"><select className="select" value={k.answers.record || "continuo"} onChange={(e) => ans(i, { record: e.target.value })}><option value="continuo">Continua</option><option value="movimiento">Con movimiento</option></select></Field>
            <Field label="Distancia"><select className="select" value={k.answers.distance || "media"} onChange={(e) => ans(i, { distance: e.target.value })}><option value="corta">Corta (15 m)</option><option value="media">Típica del lugar</option><option value="larga">Larga (70 m)</option></select></Field>
            {([["audio", "Audio"], ["smart", "Detecta personas"], ["growth", "Espacio para crecer"], ["ups", "UPS"]] as const).map(([f, l]) => (
              <label key={f} style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={!!k.answers[f]} onChange={(e) => ans(i, { [f]: e.target.checked })} />{l}</label>
            ))}
            <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={k.active} onChange={(e) => upd(i, { active: e.target.checked })} />Activo</label>
            <button className="btn btn--danger btn--sm" onClick={() => { setList(list.filter((_, j) => j !== i)); setOrigin(origin.filter((_, j) => j !== i)); }}>Quitar</button>
          </div>
          <KitComposition kit={k} pv={pvOf(i)} onChange={(p) => upd(i, p)} />
          <KitPhotosEditor value={k.photos || []} pv={pvOf(i)} onChange={(photos) => upd(i, { photos })} />
          {(() => {
            const pv = pvOf(i);
            if (!pv) return null;
            const malos = pv.lines.filter((ln) => !vendible(ln.state));
            return (
              <div className="meta" style={{ display: "grid", gap: 3 }}>
                <span>Hoy arma: {pv.lines.length ? pv.lines.map((ln) => `${ln.quantity} × ${ln.name}`).join(" · ") : "nada (sin productos que calcen)"}{pv.missing.length ? ` · para cotizar: ${pv.missing.join(", ")}` : ""}</span>
                {pv.total != null && Number(pv.total) > 0 && <span>Precio de la tarjeta hoy: <b>{fmtMoney(pv.total, pv.currency || "CRC")}</b> con IVA (cambia solo con el catálogo; los cambios sin guardar no cuentan)</span>}
                {malos.length > 0 && <span style={{ color: "var(--warn)" }}>Sin existencia ni proveedor vigente: {malos.map((ln) => ln.name).join(", ")} (el cliente lo consulta por WhatsApp)</span>}
              </div>
            );
          })()}
        </div>
      ))}
    </div>
  );
}

/** Productos del kit (pedido de Andres): por pieza, "Automático" (el configurador elige el que mejor calza hoy) o un
    producto fijo; y adicionales con su cantidad. El API valida al guardar (de otra empresa o de otra pieza se descarta
    con aviso) y al armar: un fijado que hoy no sirve (sin publicar, sin precio, pocos canales) vuelve a automático y
    aquí se dice por qué. Los candidatos de cada lista son los que el configurador acepta para las respuestas del kit. */
function KitComposition({ kit, pv, onChange }: { kit: Kit; pv?: KitPv; onChange: (p: Partial<Kit>) => void }) {
  const [names, setNames] = useState<Record<number, string>>({}); // elegidos en esta sesion (aun sin guardar)
  const [otherRole, setOtherRole] = useState("");
  const pins = kit.pins || {};
  const extras = kit.extras || [];
  const setPin = (key: string, id: number | null) => { const n = { ...pins }; if (id) n[key] = id; else delete n[key]; onChange({ pins: n }); };
  if (!pv?.roles) return <p className="meta" style={{ margin: 0 }}>Guarde el kit para elegir sus productos por pieza.</p>;
  const cur = pv.currency || "CRC";
  const keys = [...pv.roles.map((r) => r.key), ...Object.keys(pins).filter((k) => !pv.roles!.some((r) => r.key === k))];
  const nameOf = (id: number) => names[id] || pv.pins?.find((x) => x.product_id === id)?.name || pv.extras?.find((x) => x.product_id === id)?.name || pv.roles?.flatMap((r) => r.options).find((o) => o.id === id)?.name || `Producto ${id}`;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <b style={{ fontSize: 12.5 }}>Productos del kit</b>
      <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 260px), 1fr))" }}>
        {keys.map((key) => {
          const r = pv.roles!.find((x) => x.key === key);
          const st = pv.pins?.find((x) => x.key === key);
          const pinned = pins[key];
          const opts = r?.options || [];
          const auto = r?.source === "auto" && r.name ? ` · hoy: ${r.name}` : r?.source === "missing" ? " · no hay producto que calce (se cotiza)" : !r ? " · no va en este kit" : "";
          return (
            <label key={key} style={{ display: "grid", gap: 3, minWidth: 0, fontSize: 12.5 }}>
              <span style={{ fontWeight: 600 }}>{r?.label || ROLE_NAMES[key] || key}</span>
              <select className="select" style={{ minWidth: 0 }} value={pinned ? String(pinned) : ""} onChange={(e) => setPin(key, e.target.value ? Number(e.target.value) : null)}>
                <option value="">Automático{auto}</option>
                {!!pinned && !opts.some((o) => o.id === pinned) && <option value={String(pinned)}>{nameOf(pinned)} (fijado)</option>}
                {opts.map((o) => <option key={o.id} value={String(o.id)}>{o.name} · {fmtMoney(o.price, cur)}</option>)}
              </select>
              {st && pinned === st.product_id && !st.ok && <span className="meta" style={{ color: "var(--warn)" }}>No se usa hoy: {st.reason} Va el automático.</span>}
              {st && pinned === st.product_id && st.ok && !st.used && st.reason && <span className="meta">{st.reason}</span>}
              {!!pinned && (!st || st.product_id !== pinned) && <span className="meta">Sin guardar.</span>}
            </label>
          );
        })}
      </div>
      <details>
        <summary className="meta" style={{ cursor: "pointer" }}>Fijar un producto que no está en la lista</summary>
        <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
          <select className="select" value={otherRole} onChange={(e) => setOtherRole(e.target.value)} aria-label="Pieza">
            <option value="">Elija la pieza…</option>
            {Object.entries(ROLE_NAMES).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {otherRole && (
            <ProductPicker
              placeholder={`Producto para ${ROLE_NAMES[otherRole].toLowerCase()} (nombre o código)`}
              accept={(p) => p.show_on_web}
              rejectNote="Solo productos publicados en la web. Al guardar se revisa que sirvan para esa pieza."
              onPick={(p) => { setNames((n) => ({ ...n, [p.id]: p.name })); setPin(otherRole, p.id); setOtherRole(""); }}
            />
          )}
        </div>
      </details>
      <div style={{ display: "grid", gap: 6 }}>
        <span style={{ fontSize: 12.5 }}><b>Adicionales</b> <span className="meta">Se suman al kit con su cantidad (por ejemplo una UPS o un videoportero). Hasta 6.</span></span>
        {extras.map((e) => {
          const st = pv.extras?.find((x) => x.product_id === e.product_id);
          return (
            <div key={e.product_id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
              <span style={{ flex: "1 1 180px", minWidth: 0, overflowWrap: "anywhere" }}>
                {nameOf(e.product_id)}
                {st && !st.ok && <span className="meta" style={{ color: "var(--warn)" }}> · no se suma hoy: {st.reason}</span>}
                {!st && <span className="meta"> · sin guardar</span>}
              </span>
              <input className="input input--mono" type="number" min={1} max={99} style={{ width: 76 }} aria-label="Cantidad" value={e.quantity} onChange={(ev) => onChange({ extras: extras.map((x) => (x.product_id === e.product_id ? { ...x, quantity: Math.max(1, Math.min(99, Number(ev.target.value) || 1)) } : x)) })} />
              <button className="x" onClick={() => onChange({ extras: extras.filter((x) => x.product_id !== e.product_id) })} aria-label="Quitar adicional"><Icon d={I.x} size={14} /></button>
            </div>
          );
        })}
        {extras.length < 6 && (
          <ProductPicker
            placeholder="Agregar un adicional (nombre o código)"
            accept={(p) => p.show_on_web}
            rejectNote="Solo productos publicados en la web."
            onPick={(p) => { if (extras.some((x) => x.product_id === p.id)) return; setNames((n) => ({ ...n, [p.id]: p.name })); onChange({ extras: [...extras, { product_id: p.id, quantity: 1 }] }); }}
          />
        )}
      </div>
    </div>
  );
}

/* las fotos que trae /products (el tipo Product del portal no las declara), la principal primero como en la tienda */
const photosOf = (p: Product): string[] => {
  const imgs = ((p as Product & { images?: { url?: string; main?: boolean }[] }).images || []).filter((i) => !!i?.url);
  return [...imgs.filter((i) => i.main), ...imgs.filter((i) => !i.main)].map((i) => i.url as string);
};

/** Fotos fijas de la tarjeta del kit (pedido 21/10): Andres elige que equipos se ven y, si el producto tiene varias
    fotos, cual. Sin elegir, la tarjeta muestra los equipos que el kit arma hoy (camaras, grabador, disco, switch). */
function KitPhotosEditor({ value, pv, onChange }: { value: KitPhotoSel[]; pv?: KitPv; onChange: (v: KitPhotoSel[]) => void }) {
  const [picked, setPicked] = useState<Record<number, { name: string; images: string[] }>>({}); // elegidos en esta sesion (aun sin guardar)
  const saved: Record<number, KitPhotoPv> = Object.fromEntries((pv?.photos_manual ? pv.photos || [] : []).map((x) => [x.product_id, x]));
  const thumb = (on = false): React.CSSProperties => ({ width: 46, height: 46, objectFit: "contain", background: "#f3f1ee", borderRadius: 6, border: `2px solid ${on ? "var(--crimson)" : "var(--hair)"}`, padding: 2 });
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <b style={{ fontSize: 12.5 }}>Fotos de la tarjeta</b>
        <span className="meta">{value.length ? `${value.length} de 4 elegidas a mano (solo cuentan productos publicados con foto)` : "Automáticas: los equipos que el kit arma hoy"}</span>
        {value.length > 0 && <button className="btn btn--ghost btn--sm" onClick={() => onChange([])}>Volver a automáticas</button>}
      </div>
      {value.length === 0 && !!pv?.photos?.length && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {pv.photos.map((ph) => <img key={ph.product_id} src={ph.image} alt={ph.name} title={`${ph.label} · ${ph.name}`} style={thumb()} />)}
        </div>
      )}
      {value.map((ph, j) => {
        const info = saved[ph.product_id];
        const imgs = info?.images || picked[ph.product_id]?.images || [];
        const cur = ph.image || info?.image || imgs[0] || "";
        return (
          <div key={ph.product_id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
            <span className="meta" style={{ width: 18 }}>{j + 1}</span>
            <span style={{ flex: "1 1 180px", minWidth: 0 }}>{info?.name || picked[ph.product_id]?.name || `Producto ${ph.product_id}`}{!info && !imgs.length && <span className="meta"> · sin foto publicada: no se mostrará</span>}</span>
            {imgs.length > 1 ? imgs.map((src) => (
              <button key={src} type="button" onClick={() => onChange(value.map((x) => (x.product_id === ph.product_id ? { ...x, image: src } : x)))} aria-label="Usar esta foto" aria-pressed={src === cur} style={{ border: 0, background: "none", padding: 0, cursor: "pointer" }}>
                <img src={src} alt="" style={thumb(src === cur)} />
              </button>
            )) : cur ? <img src={cur} alt="" style={thumb()} /> : null}
            <button className="x" onClick={() => onChange(value.filter((x) => x.product_id !== ph.product_id))} aria-label="Quitar foto"><Icon d={I.x} size={14} /></button>
          </div>
        );
      })}
      {value.length < 4 && (
        <ProductPicker
          placeholder="Agregar un equipo a la foto del kit (nombre o código)"
          accept={(p) => p.show_on_web && photosOf(p).length > 0}
          rejectNote="Solo se pueden elegir productos publicados en la web y con foto: los demás no aparecen aquí."
          onPick={(p) => { if (value.some((x) => x.product_id === p.id)) return; setPicked((n) => ({ ...n, [p.id]: { name: p.name, images: photosOf(p) } })); onChange([...value, { product_id: p.id, image: "" }]); }}
        />
      )}
    </div>
  );
}

function FaqEditor({ value, onSave, onDirty }: { value: Faq[]; onSave: (v: Faq[]) => void; onDirty?: OnDirty }) {
  const [list, setList, guardando] = useDraft<Faq[]>(value, "faq", onDirty);
  const upd = (i: number, p: Partial<Faq>) => setList(list.map((f, j) => (j === i ? { ...f, ...p } : f)));
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <b style={{ fontSize: 13 }}>Preguntas frecuentes</b>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setList([...list, { q: "", a: "" }])} disabled={list.length >= 40}><Icon d={I.plus} />Pregunta</button>
          <button className="btn btn--crimson btn--sm" onClick={() => { guardando(); onSave(list.filter((f) => f.q.trim() && f.a.trim())); }}><Icon d={I.check} />Guardar preguntas</button>
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
            {p.price !== null && <Field label={`Precio anterior SIN IVA (${p.currency})`} hint={`Precio actual sin IVA: ${p.price}. Si el anterior es mayor, la tienda lo muestra tachado.`}><input key={`c${p.id}`} className="input input--mono" inputMode="decimal" defaultValue={p.compare_price ?? ""} onBlur={(e) => {
              // O1-11b: «4.700» son cuatro mil setecientos (parseCRC); un monto mal escrito se rechaza con el motivo
              const raw = e.target.value.trim();
              if (!raw) { if (p.compare_price != null) save({ compare_price: null }); return; }
              const r = parseMonto(raw, p.currency);
              if (!r.ok) { toast(r.error, "bad"); return; }
              if (r.value !== Number(p.compare_price)) save({ compare_price: r.value });
            }} /></Field>}
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
