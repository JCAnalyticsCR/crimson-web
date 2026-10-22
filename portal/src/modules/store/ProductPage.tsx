/* Ficha de producto: /p/<nombre>-<id>. Galeria con lupa (escritorio) y visor a pantalla completa con zoom
   (todo dentro de la pagina), marca/modelo/SKU, precio con IVA y desglose, precio anterior, disponibilidad,
   cantidad limitada a lo disponible, carrito (del modulo de pedidos), instalacion, WhatsApp, especificaciones,
   ficha tecnica PDF, "¿Que necesito ademas?", relacionados y compartir. En movil la barra de compra queda fija.
   Si el snapshot está cargado, muestra nombre/foto/precio/disponibilidad al instante sin esperar /item. */
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AvailBadge, Crumbs, EmptyHelp, Grid, IC, Ico, PriceTag, ProductCard, Viewfinder, cannotBuy, money, normAvail, useAvailability, useHead, usePath, useStore, waLink, type Variant, type VProd } from "./storeKit";
import { pidFromRef, snapToVProd } from "./catalogFilter";
import { useSnapshot } from "./useSnapshot";

type Detail = VProd & {
  description: string | null; specs: Record<string, string>; datasheet_url: string | null; net_price: string | number; tax_amount: string | number;
  breadcrumbs: { name: string; path: string }[]; related: VProd[]; accessories: { title: string; reason: string; items: VProd[] }[]; whatsapp: string;
};

export default function ProductPage() {
  const ctx = useStore();
  const to = usePath();
  const nav = useNavigate();
  const { ref = "" } = useParams();
  const [d, setD] = useState<Detail | null>(null);
  const [missing, setMissing] = useState(false);
  const [photo, setPhoto] = useState(0);
  const [variant, setVariant] = useState<Variant | undefined>();
  const [qty, setQty] = useState(1);
  const [added, setAdded] = useState(false);
  // ya lo agrego: "Ir al carrito" queda a mano hasta salir de la ficha (pedido 21/10; antes el enlace se iba a los 2 s)
  const [inCart, setInCart] = useState(false);
  const [shared, setShared] = useState(false);
  const [zoom, setZoom] = useState(false);
  const addT = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(addT.current), []);

  // Primer pintado desde el snapshot (sin esperar /item)
  const { snap } = useSnapshot(ctx.slug);
  const snapCard = useMemo<VProd | null>(() => {
    if (!snap) return null;
    const pid = pidFromRef(ref);
    if (!pid) return null;
    const p = snap.products.find((x) => x.id === pid);
    return p ? snapToVProd(p, snap.currency, snap.categories) : null;
  }, [snap, ref]);

  useEffect(() => {
    let alive = true;
    setMissing(false); setPhoto(0); setQty(1);
    // Mostrar datos del snapshot inmediatamente si están en caché (cero latencia)
    if (snapCard) {
      setD({
        ...snapCard,
        description: null,
        specs: {},
        datasheet_url: null,
        net_price: snapCard.display_price,
        tax_amount: 0,
        breadcrumbs: [{ name: "Tienda", path: "/" }],
        related: [],
        accessories: [],
        whatsapp: ctx.store.whatsapp ?? "",
      } as Detail);
    } else {
      setD(null);
    }
    // Cargar la ficha completa (description, specs, relacionados, accesorios)
    ctx.api<Detail>(`/item/${encodeURIComponent(ref)}`)
      .then((x) => { if (!alive) return; setD(x); setVariant(x.variants?.[0]); })
      .catch(() => { if (alive) { setD(null); setMissing(true); } });
    return () => { alive = false; };
  // snapCard se lee sincrónicamente en el efecto; no se incluye en deps para no re-ejecutar cuando el snap llega tarde
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, ctx]);
  useEffect(() => setInCart(false), [ref]);
  // el nombre cambio -> direccion canonica sin recargar
  useEffect(() => { if (d && `/p/${ref}` !== d.path) nav(to(d.path), { replace: true }); }, [d]); // eslint-disable-line react-hooks/exhaustive-deps

  const ids = useMemo(() => (d ? [d.id, ...d.related.map((r) => r.id), ...d.accessories.flatMap((g) => g.items.map((i) => i.id))] : []), [d]);
  const avail = useAvailability(ids);
  const a = normAvail((d && avail[d.id]) || d?.availability);
  // tope en linea (max_qty del API: lo propio libre + lo que el proveedor confirma); sin dato, 999 y el checkout valida
  const maxQty = a?.max_qty && a.max_qty > 0 ? Math.min(999, a.max_qty) : 999;
  useEffect(() => { if (qty > maxQty) setQty(maxQty); }, [maxQty, qty]);

  const S = ctx.store;
  const url = typeof location !== "undefined" ? location.href.split("?")[0] : "";
  useHead(d ? {
    title: `${d.name} · ${S.name}`,
    description: `${(d.description || d.name).replace(/\s+/g, " ").slice(0, 150)} · Precio con IVA, envío a todo Costa Rica.`,
    path: d.path, image: d.image, type: "product",
    ld: { "@context": "https://schema.org", "@type": "Product", name: d.name, sku: d.code, image: d.images, brand: d.brand ? { "@type": "Brand", name: d.brand } : undefined, offers: Number(d.display_price) ? { "@type": "Offer", price: String(d.display_price), priceCurrency: d.display_currency, url } : undefined },
  } : missing ? { title: `Producto no disponible · ${S.name}`, noindex: true } : null);

  if (missing) return <div className="sh-wrap"><EmptyHelp title="Este producto ya no está disponible." text="Puede que lo hayamos reemplazado por un modelo nuevo. Te ayudamos a encontrar el equivalente."><Link className="sh-btn" to={to("/catalogo")}>Ver catálogo</Link></EmptyHelp></div>;
  if (!d) return <div className="sh-wrap"><div className="vt-pdp" style={{ paddingTop: 24 }}><div className="sh-skel" style={{ aspectRatio: "1" }} /><div style={{ display: "grid", gap: 12, alignContent: "start" }}><div className="sh-skel" style={{ height: 22, width: "40%" }} /><div className="sh-skel" style={{ height: 70 }} /><div className="sh-skel" style={{ height: 160 }} /></div></div></div>;

  const imgs = d.images?.length ? d.images : d.image ? [d.image] : [];
  const price = variant?.display_price ?? d.display_price;
  const shown = { ...d, display_price: price };
  const ask = d.price_on_request || !Number(price);
  const soldOut = cannotBuy(a);
  const soldLabel = a?.state === "agotado" ? "Agotado" : "Consultar disponibilidad";
  const net = variant ? Number(price) / (1 + d.tax_rate / 100) : Number(d.net_price);
  const iva = Number(price) - net;
  const save = d.compare_display_price && Number(d.compare_display_price) > Number(price) ? Number(d.compare_display_price) - Number(price) : 0;
  const waText = `Hola ${S.name}, me interesa: ${d.name}${d.code ? ` (${d.code})` : ""}${qty > 1 ? ` · ${qty} unidades` : ""}\n${url}`;
  const installHref = to(`/asesoria?producto=${d.id}&cantidad=${qty}&instalar=1`);
  const inCompare = ctx.compare.includes(d.id);

  const add = () => {
    ctx.addToCart(d, qty, variant);
    setAdded(true);
    setInCart(true);
    window.clearTimeout(addT.current);
    addT.current = window.setTimeout(() => setAdded(false), 1800);
  };
  const share = async () => {
    const data = { title: d.name, text: `${d.name} · ${S.name}`, url };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(url);
      setShared(true); window.setTimeout(() => setShared(false), 2000);
    } catch { /* cancelado por la persona */ }
  };
  const meta = [d.brand && `Marca ${d.brand}`, d.model && d.model !== d.code && `Modelo ${d.model}`, d.code && `SKU ${d.code}`].filter(Boolean) as string[];

  return (
    <div className="sh-wrap vt-product">
      <Crumbs items={[...d.breadcrumbs, { name: d.name }]} />
      <div className="vt-pdp">
        <Gallery imgs={imgs} name={d.name} code={d.code} photo={photo} setPhoto={setPhoto} onZoom={() => setZoom(true)} />
        <div className="vt-info">
          <div className="vt-info__meta sh-meta">{meta.map((m) => <span key={m}>{m}</span>)}</div>
          <h1>{d.name}</h1>
          <div className="vt-info__price">
            <PriceTag p={shown} big />
            <AvailBadge a={a} />
          </div>
          {!ask && (
            <p className="vt-breakdown">
              Precio sin IVA {money(net, d.display_currency)} + IVA {d.tax_rate} % {money(iva, d.display_currency)}
              {save > 0 && <b> · Ahorrás {money(save, d.display_currency)}</b>}
            </p>
          )}
          {(d.variants?.length ?? 0) > 0 && (
            <fieldset className="vt-variants"><legend className="sh-meta">Opciones</legend>
              {d.variants.map((v) => <button key={v.id} type="button" className="sh-chip" aria-pressed={variant?.id === v.id} onClick={() => setVariant(v)}>{v.name}</button>)}
            </fieldset>
          )}

          <div className="vt-buybox" id="comprar">
            {ask ? (
              <>
                <p className="vt-buybox__note">Este equipo se cotiza según el proyecto (precio de distribuidor variable). Te respondemos el mismo día hábil.</p>
                {S.whatsapp && <a className="sh-btn sh-btn--wa sh-btn--block" href={waLink(S.whatsapp, `Hola ${S.name}, quiero precio de: ${d.name} (${d.code})\n${url}`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Pedir precio por WhatsApp</a>}
                <Link className="sh-btn sh-btn--block" to={to(`/asesoria?producto=${d.id}&cantidad=${qty}`)}>Solicitar cotización</Link>
              </>
            ) : (
              <>
                <div className="vt-buy">
                  <Qty value={qty} max={maxQty} onChange={setQty} />
                  <button className="sh-btn sh-btn--crimson vt-buy__add" data-done={added} onClick={add} disabled={soldOut}>
                    <Ico d={added ? IC.check : IC.bag} />{soldOut ? soldLabel : added ? "Agregado" : ctx.isShop ? "Agregar al carrito" : "Agregar a la cotización"}
                  </button>
                </div>
                {a && (a.state === "disponible" || a.state === "ultimas") && a.cantidad != null && a.cantidad > 0 && a.cantidad < 999 ? <p className="vt-buybox__note">Quedan {a.cantidad} en bodega{a.max_qty && a.max_qty > a.cantidad ? `; ${a.max_qty - a.cantidad} más bajo pedido` : ""}.</p> : null}
                {soldOut && <p className="vt-buybox__note">No lo podemos vender en línea en este momento: escribinos y te confirmamos disponibilidad y plazo.</p>}
                {inCart && <button className="sh-btn sh-btn--block vt-buy__go" onClick={ctx.openCart}><Ico d={IC.bag} />Ir {ctx.isShop ? "al carrito" : "a la cotización"}</button>}
                <Link className="vt-install" to={installHref}>
                  <Ico d={IC.tool} /><span><b>¿Lo instalamos nosotros?</b><small>Técnicos de Crimson · te cotizamos la instalación sin compromiso</small></span><Ico d={IC.next} />
                </Link>
                {S.whatsapp && <a className={`sh-btn ${soldOut ? "sh-btn--wa" : "sh-btn--ghost"} sh-btn--block`} href={waLink(S.whatsapp, waText)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />{soldOut ? "Consultar disponibilidad por WhatsApp" : "Consultar por WhatsApp"}</a>}
              </>
            )}
            <div className="vt-buybox__tools">
              <button type="button" className="vt-tool" aria-pressed={inCompare} onClick={() => ctx.toggleCompare(d.id)} disabled={!inCompare && ctx.compare.length >= 3}><Ico d={IC.compare} />{inCompare ? "En el comparador" : "Comparar"}</button>
              <button type="button" className="vt-tool" onClick={share}><Ico d={shared ? IC.check : IC.share} />{shared ? "Enlace copiado" : "Compartir"}</button>
              {d.datasheet_url && <a className="vt-tool" href={d.datasheet_url} target="_blank" rel="noopener noreferrer"><Ico d={IC.pdf} />Ficha técnica (PDF)</a>}
            </div>
          </div>

          <ul className="vt-assure">
            <li><Ico d={IC.shield} /><span>Garantía de fábrica gestionada por Crimson</span></li>
            <li><Ico d={IC.tool} /><span>Soporte técnico local en Costa Rica</span></li>
            <li><Ico d={IC.doc} /><span>Factura electrónica en cada compra</span></li>
            <li><Ico d={IC.truck} /><span>Envío a todo el país o retiro coordinado</span></li>
          </ul>
        </div>
      </div>

      <div className="vt-pdp__detail">
        {Object.keys(d.specs).length > 0 && (
          <section aria-labelledby="vt-specs" className="vt-specs">
            <h2 id="vt-specs">Especificaciones</h2>
            <dl>{Object.entries(d.specs).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}<div><dt>Impuesto</dt><dd>IVA {d.tax_rate} % incluido</dd></div></dl>
          </section>
        )}
        {d.description && (
          <section aria-labelledby="vt-desc" className="vt-descr">
            <h2 id="vt-desc">Descripción</h2>
            <p className="sh-desc">{d.description}</p>
          </section>
        )}
      </div>

      {d.accessories.length > 0 && (
        <section className="vt-sec" aria-labelledby="vt-acc">
          <div className="vt-sec__head"><div><span className="sh-eyebrow">Para que funcione completo</span><h2 id="vt-acc">¿Qué necesito además?</h2></div><Link className="vt-more" to={to("/arma-tu-sistema")}>Armar el sistema completo →</Link></div>
          <div className="vt-acc">
            {d.accessories.map((g) => (
              <div key={g.title} className="vt-acc__group">
                <div className="vt-acc__head"><b>{g.title}</b><span>{g.reason}</span></div>
                <ul className="vt-acc__list">{g.items.map((p) => <li key={p.id}><ProductCard p={p} avail={avail[p.id]} /></li>)}</ul>
              </div>
            ))}
          </div>
        </section>
      )}

      {d.related.length > 0 && (
        <section className="vt-sec" aria-labelledby="vt-rel">
          <div className="vt-sec__head"><h2 id="vt-rel">También te puede servir</h2>{d.category_path && <Link className="vt-more" to={to(d.category_path)}>Ver {d.category} →</Link>}</div>
          <Grid items={d.related} avail={avail} rail />
        </section>
      )}

      {/* barra fija de compra en movil: precio y CTA siempre a mano */}
      <div className="vt-stickybuy" aria-hidden={false}>
        <div><PriceTag p={shown} /></div>
        {ask ? <a className="sh-btn sh-btn--crimson" href="#comprar">Pedir precio</a>
          : inCart && !added ? <button className="sh-btn" onClick={ctx.openCart}><Ico d={IC.bag} />Ir {ctx.isShop ? "al carrito" : "a la cotización"}</button>
            : <button className="sh-btn sh-btn--crimson" data-done={added} onClick={add} disabled={soldOut}><Ico d={added ? IC.check : IC.bag} />{soldOut ? (a?.state === "agotado" ? "Agotado" : "Sin existencias") : added ? "Agregado" : "Agregar"}</button>}
      </div>

      {zoom && <Lightbox imgs={imgs} start={photo} name={d.name} onClose={(i) => { setPhoto(i); setZoom(false); }} />}
    </div>
  );
}

function Qty({ value, onChange, max }: { value: number; onChange: (n: number) => void; max: number }) {
  const clamp = (n: number) => Math.max(1, Math.min(max, n || 1));
  return (
    <span className="sh-qty vt-qty">
      <button type="button" onClick={() => onChange(clamp(value - 1))} aria-label="Quitar uno" disabled={value <= 1}>−</button>
      <input inputMode="numeric" value={value} onChange={(e) => onChange(clamp(Number(e.target.value.replace(/\D/g, ""))))} aria-label="Cantidad" />
      <button type="button" onClick={() => onChange(clamp(value + 1))} aria-label="Agregar uno" disabled={value >= max}>+</button>
    </span>
  );
}

/** Foto principal con lupa: en escritorio con mouse sigue al puntero (gate de rAF, sin layout por evento);
    clic o toque abre el visor a pantalla completa. */
function Gallery({ imgs, name, code, photo, setPhoto, onZoom }: { imgs: string[]; name: string; code: string; photo: number; setPhoto: (n: number) => void; onZoom: () => void }) {
  const box = useRef<HTMLButtonElement>(null);
  const raf = useRef(0);
  const pt = useRef({ x: 0, y: 0 });
  const rect = useRef<DOMRect | null>(null);
  const [lens, setLens] = useState(false);
  const canLens = typeof matchMedia === "function" && matchMedia("(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)").matches;
  const move = (e: RPointerEvent) => {
    if (!canLens || e.pointerType !== "mouse") return;
    pt.current = { x: e.clientX, y: e.clientY };
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = 0;
      const r = rect.current;
      const img = box.current?.querySelector("img");
      if (!r || !img) return;
      img.style.transformOrigin = `${((pt.current.x - r.left) / r.width) * 100}% ${((pt.current.y - r.top) / r.height) * 100}%`;
    });
  };
  const enter = (e: RPointerEvent) => { if (!canLens || e.pointerType !== "mouse") return; rect.current = box.current?.getBoundingClientRect() || null; setLens(true); };
  const leave = () => { setLens(false); cancelAnimationFrame(raf.current); raf.current = 0; };
  useEffect(() => () => cancelAnimationFrame(raf.current), []);
  const cur = imgs[Math.min(photo, imgs.length - 1)];
  return (
    <div className="vt-gallery">
      <Viewfinder className="vt-gallery__main" label={`CAM 01 · ${code}`} right={imgs.length > 1 ? `${photo + 1}/${imgs.length}` : undefined}>
        {cur ? (
          <button ref={box} type="button" className="vt-gallery__zoom" data-lens={lens} onPointerEnter={enter} onPointerMove={move} onPointerLeave={leave} onClick={onZoom} aria-label={`Ampliar foto de ${name}`}>
            <img src={cur} alt={name} fetchPriority="high" decoding="async" width={800} height={800} />
            <span className="vt-gallery__hint"><Ico d={IC.zoom} size={16} />Ampliar</span>
          </button>
        ) : <span className="sh-noimg">Sin foto</span>}
      </Viewfinder>
      {imgs.length > 1 && (
        <ul className="vt-thumbs" aria-label="Fotos">
          {imgs.map((u, i) => <li key={u + i}><button type="button" className="vt-thumb" aria-current={i === photo} aria-label={`Foto ${i + 1} de ${imgs.length}`} onClick={() => setPhoto(i)}><img src={u} alt="" loading="lazy" decoding="async" width={72} height={72} /></button></li>)}
        </ul>
      )}
    </div>
  );
}

/** Visor a pantalla completa dentro de la pagina: flechas, Esc, toque/clic para acercar (2.5x) y arrastrar. */
function Lightbox({ imgs, start, name, onClose }: { imgs: string[]; start: number; name: string; onClose: (i: number) => void }) {
  const [i, setI] = useState(start);
  const [z, setZ] = useState<{ on: boolean; x: number; y: number }>({ on: false, x: 50, y: 50 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: boolean } | null>(null);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const go = (n: number) => { setI((n + imgs.length) % imgs.length); setZ({ on: false, x: 50, y: 50 }); };
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose(i);
      if (e.key === "ArrowRight") go(i + 1);
      if (e.key === "ArrowLeft") go(i - 1);
    };
    document.addEventListener("keydown", onKey);
    const ov = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ov; prev?.focus?.(); };
  }, [i]); // eslint-disable-line react-hooks/exhaustive-deps
  const down = (e: RPointerEvent<HTMLDivElement>) => { drag.current = { x: e.clientX, y: e.clientY, ox: z.x, oy: z.y, moved: false }; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); };
  const moveP = (e: RPointerEvent<HTMLDivElement>) => {
    const dr = drag.current;
    if (!dr || !z.on) return;
    const r = e.currentTarget.getBoundingClientRect();
    const dx = ((e.clientX - dr.x) / r.width) * 100, dy = ((e.clientY - dr.y) / r.height) * 100;
    if (Math.abs(dx) + Math.abs(dy) > 1) dr.moved = true;
    setZ({ on: true, x: Math.max(0, Math.min(100, dr.ox - dx)), y: Math.max(0, Math.min(100, dr.oy - dy)) });
  };
  const up = (e: RPointerEvent<HTMLDivElement>) => {
    const dr = drag.current;
    drag.current = null;
    if (dr?.moved) return;
    const r = e.currentTarget.getBoundingClientRect();
    setZ(z.on ? { on: false, x: 50, y: 50 } : { on: true, x: ((e.clientX - r.left) / r.width) * 100, y: ((e.clientY - r.top) / r.height) * 100 });
  };
  return (
    <div className="vt-lightbox" role="dialog" aria-modal="true" aria-label={`Fotos de ${name}`}>
      <div className="vt-lightbox__bar">
        <span className="sh-meta">{i + 1} / {imgs.length} · {z.on ? "Arrastrá para mover" : "Tocá para acercar"}</span>
        <button ref={closeBtn} className="sh-x vt-lightbox__x" onClick={() => onClose(i)} aria-label="Cerrar visor"><Ico d={IC.x} /></button>
      </div>
      <div className="vt-lightbox__stage" data-zoom={z.on} onPointerDown={down} onPointerMove={moveP} onPointerUp={up}>
        <img src={imgs[i]} alt={name} draggable={false} style={{ transformOrigin: `${z.x}% ${z.y}%` }} />
      </div>
      {imgs.length > 1 && (
        <>
          <button className="vt-lightbox__nav vt-lightbox__nav--prev" onClick={() => go(i - 1)} aria-label="Foto anterior"><Ico d={IC.back} size={22} /></button>
          <button className="vt-lightbox__nav vt-lightbox__nav--next" onClick={() => go(i + 1)} aria-label="Foto siguiente"><Ico d={IC.next} size={22} /></button>
        </>
      )}
    </div>
  );
}
