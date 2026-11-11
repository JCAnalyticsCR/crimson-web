/* Portada de la tienda. O2-08: una promesa, un producto real y un solo llamado. Hero sobre noche liso con el titular,
   un boton rojo («Armá tu sistema»), «Ver catálogo» como enlace y datos reales de la configuracion; el producto de
   portada (elegido en el panel, con respaldos) es la imagen LCP. Ningun movimiento fuera del hero.
   O1-04: portada honesta. Nada de «Lo más pedido» sin pedidos: los destacados dicen si los eligio el equipo o la
   tienda (featured_source) y cada kit dice que es solo equipo y si su precio es de referencia.
   Cuando el snapshot está disponible, las secciones de categorías, marcas, destacados y novedades se muestran
   al instante (sin esperar /showcase). Los kits, el producto de portada y la info de páginas siguen viniendo de /showcase. */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AvailBadge, Grid, IC, Ico, KitPhotos, PriceTag, ProductCard, Skeleton, catIcon, money, useAvailability, useHead, usePath, useStore, waLink, quitarRetiro, sinRetiro, type KitPhoto, type SeoBlock, type VProd } from "./storeKit";
import { snapToVProd } from "./catalogFilter";
import { useSnapshot } from "./useSnapshot";
import { expireBootShowcase, takeBootShowcase } from "./boot";

type Cat = { id: number; name: string; path: string; count: number; image: string | null; children: { name: string; path: string; count: number }[] };
/* photos: fotos fijas de los equipos reales del kit (o las que Andres eligio en Vitrina) */
/* payable (O1-04 h): todas las piezas se pagan en linea; false = precio de referencia (falta en un API viejo) */
type Kit = { id: string; title: string; text: string; answers: Record<string, unknown>; items: number; total: number | string; image: string | null; photos?: KitPhoto[]; cameras: number; payable?: boolean };
type Banner = { id: string; image: string; title: string; text: string; link: string; cta: string };
type Showcase = {
  headline: string; subline: string; banners: Banner[]; trust: { icon: string; title: string; text: string }[];
  categories: Cat[]; brands: { name: string; count: number }[]; featured: VProd[]; fresh: VProd[]; kits: Kit[];
  /** O1-04 a: «manual» = los eligio el equipo; «auto» = la tienda (solo comprables con precio) */
  featured_source?: "manual" | "auto";
  /** O2-08 (aditivo): el producto de portada (el primero comprable de la lista del panel) o null */
  hero?: VProd | null;
  product_count: number; currency: string; info: { kind: string; title: string; path: string }[];
  seo?: SeoBlock | null;
};

const TRUST_IC: Record<string, string> = { shield: IC.shield, tool: IC.tool, truck: IC.truck, doc: IC.doc, chat: IC.chat, clock: IC.clock };
const isExternal = (u: string) => /^https?:\/\//.test(u);
/** familias (categoria de primer nivel) de seguridad, redes y respaldo: services/product_typing.FUNCTIONAL_FAMILY */
const FAMILIA = new Set(["seguridad", "videovigilancia", "vigilancia", "cctv", "camara", "camaras", "acceso", "alarma", "alarmas", "redes", "red", "conectividad", "networking", "cableado", "cable", "cables", "energia", "respaldo", "ups", "almacenamiento"]);
const deFamilia = (name: string) => (name.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase().match(/[a-z0-9]+/g) ?? []).some((w) => FAMILIA.has(w));
/** O1-04 d: un banner sin imagen o con titulo vacio o «Nuevo banner» no se pinta (el API ya los filtra; un snapshot viejo no) */
const bannerOk = (b: { image?: string; title?: string; active?: boolean }) => b.active !== false && !!(b.image || "").trim() && !["", "nuevo banner"].includes((b.title || "").trim().toLowerCase());


/** Construye un Showcase parcial desde el snapshot (sin kits ni info de páginas legales). */
function showcaseFromSnap(snap: import("./catalogFilter").Snapshot): Showcase {
  const catById = new Map(snap.categories.map((c) => [c.id, c]));
  const childOf = new Map<number | null, typeof snap.categories>();
  for (const c of snap.categories) {
    const pid = c.parent_id != null && catById.has(c.parent_id) ? c.parent_id : null;
    if (!childOf.has(pid)) childOf.set(pid, []);
    childOf.get(pid)!.push(c);
  }

  // Conteo acumulado (con subcategorías)
  const directCount = new Map<number, number>();
  for (const p of snap.products) {
    if (p.category_id != null) directCount.set(p.category_id, (directCount.get(p.category_id) ?? 0) + 1);
  }
  const totMemo = new Map<number, number>();
  function totalCount(cid: number): number {
    if (totMemo.has(cid)) return totMemo.get(cid)!;
    let n = directCount.get(cid) ?? 0;
    for (const ch of childOf.get(cid) ?? []) n += totalCount(ch.id);
    totMemo.set(cid, n);
    return n;
  }

  const categories: Cat[] = (childOf.get(null) ?? [])
    .map((c) => ({
      id: c.id, name: c.name, path: `/c/${c.slug}`,
      count: totalCount(c.id), image: null,
      children: (childOf.get(c.id) ?? []).map((ch) => ({
        name: ch.name, path: `/c/${ch.slug}`, count: totalCount(ch.id),
      })),
    }))
    .filter((c) => c.count > 0)
    .sort((a, b) => b.count - a.count);

  // Marcas (O1-04 c): solo las que venden seguridad, redes o respaldo; Hikvision primero, luego por conteo
  const topOf = (cid: number | null): string | null => {
    let c = cid != null ? catById.get(cid) : undefined;
    for (let i = 0; c && c.parent_id != null && catById.has(c.parent_id) && i < 6; i++) c = catById.get(c.parent_id);
    return c ? c.name : null;
  };
  const famBrands = new Set(snap.products.filter((p) => p.brand && deFamilia(topOf(p.category_id) ?? "")).map((p) => p.brand));
  const brands = [...snap.brands].filter((b) => famBrands.has(b.name))
    .sort((a, b) => {
      const ah = a.name.toLowerCase() === "hikvision";
      const bh = b.name.toLowerCase() === "hikvision";
      if (ah !== bh) return ah ? -1 : 1;
      return b.count - a.count;
    })
    .slice(0, 14);

  const byId = new Map(snap.products.map((p) => [p.id, p]));

  // Destacados: los que eligio el equipo (manual); si no hay, la tienda elige solo entre lo comprable con precio
  // (auto, O1-04 e). /showcase trae la eleccion definitiva.
  const featuredIds = snap.showcase.featured.filter((id) => byId.has(id));
  let featuredProds = featuredIds.map((id) => byId.get(id)!).slice(0, 8);
  const featured_source = featuredProds.length ? "manual" as const : "auto" as const;
  if (!featuredProds.length) {
    featuredProds = [...snap.products]
      .filter((p) => p.image && p.display_price > 0 && p.availability?.disponible === true && p.availability.state !== "servicio")
      .sort((a, b) => {
        const ah = (a.brand ?? "").toLowerCase() === "hikvision";
        const bh = (b.brand ?? "").toLowerCase() === "hikvision";
        return ah !== bh ? (ah ? -1 : 1) : Number(b.web_featured) - Number(a.web_featured) || a.name.toLowerCase().localeCompare(b.name.toLowerCase());
      })
      .slice(0, 8);
  }
  const featured = featuredProds.map((p) => snapToVProd(p, snap.currency, snap.categories));
  const featuredSet = new Set(featuredProds.map((p) => p.id));

  // Novedades: más recientes con foto, sin solapamiento con destacados
  const fresh = [...snap.products]
    .filter((p) => p.image && !featuredSet.has(p.id))
    .sort((a, b) => b.created_ts - a.created_ts || b.id - a.id)
    .slice(0, 8)
    .map((p) => snapToVProd(p, snap.currency, snap.categories));

  return {
    headline: snap.showcase.headline ?? "",
    subline: snap.showcase.subline ?? "",
    banners: (snap.showcase.banners ?? []).filter(bannerOk) as Banner[],
    trust: snap.showcase.trust ?? [],
    categories,
    brands,
    featured,
    featured_source,
    fresh,
    kits: [], // los kits no están en el snapshot; /showcase los trae
    product_count: snap.n,
    currency: snap.currency,
    info: [], // /showcase trae los enlaces de páginas legales
  };
}

/** O2-08: categorias con menos productos que esto van en la linea «Más categorías» */
const MOSAICO_MIN = 10;
/** «{N} en bodega» solo con el dato real (la etiqueta del API: «En bodega · 9 disponibles» o «3 en bodega») */
const enBodega = (a?: { state?: string; label?: string; label_short?: string | null } | null): string | null => {
  if (!a || a.state !== "en_bodega") return null;
  const m = /(\d+)\s+(?:disponibles?|en bodega)/i.exec(`${a.label_short || ""} ${a.label || ""}`);
  return m ? `${m[1]} en bodega` : null;
};
/** sellos sujetos al candado legal (O1-00): sin el, nada de garantia ni devoluciones */
const sinCandado = (t: { title: string; text: string }) => !/garant|devoluc|reembols/i.test(`${t.title} ${t.text}`);

export default function StoreHome() {
  const ctx = useStore();
  const to = usePath();
  const { snap } = useSnapshot(ctx.slug);

  // Precarga desde el snapshot (instantáneo si el snapshot está en caché)
  const snapD = useMemo(() => snap ? showcaseFromSnap(snap) : null, [snap]);

  /* O2-01a: la portada que el servidor incrusto en el HTML (boot.ts): el titular, el producto de portada y los kits
     estan desde el primer pintado y no cambian de texto al llegar /showcase (que se pide igual, en segundo plano) */
  const [d, setD] = useState<Showcase | null>(() => (ctx.hostMode ? takeBootShowcase<Showcase>(ctx.slug, "/") : null));
  useEffect(() => expireBootShowcase, []);
  const [err, setErr] = useState(false);

  /* Datos del snapshot mientras /showcase carga (O1-04 j). Antes: «if (snapD && !d) setD(snapD)». Si /showcase
     respondia entre que React pintaba el snapshot y corria este efecto (que todavia veia d en null), la cola quedaba
     [showcase, snapshot] y ganaba el snapshot: portada sin kits. Ahora la actualizacion es funcional (nunca pisa lo
     que ya hay) y una referencia marca que /showcase llego. */
  const showcaseLlego = useRef(false);
  // O2-14: lo que solo trae /showcase (kits y producto de portada) guarda su lugar hasta que llega: nada salta
  const [llego, setLlego] = useState(() => d !== null); // con el arranque, lo de /showcase ya esta
  useEffect(() => { if (snapD && !showcaseLlego.current) setD((cur) => cur ?? snapD); }, [snapD]);

  // /showcase: siempre se pide para traer los kits, el producto de portada y los datos definitivos
  useEffect(() => { ctx.api<Showcase>("/showcase").then((r) => { showcaseLlego.current = true; setD(r); setLlego(true); }).catch(() => { setErr(true); setLlego(true); }); }, [ctx]);

  /* O2-08: «Listos para entrega», solo con existencia propia y solo si hay 4 o más (del snapshot: la misma verdad que
     el listado) */
  const listos = useMemo(() => {
    if (!snap) return [];
    const xs = snap.products.filter((p) => p.availability?.state === "en_bodega" && p.image && p.display_price > 0);
    return xs.length >= 4 ? xs.slice(0, 8).map((p) => snapToVProd(p, snap.currency, snap.categories)) : [];
  }, [snap]);

  const ids = useMemo(() => (d ? [...d.featured, ...d.fresh, ...listos, ...(d.hero ? [d.hero] : [])].map((p) => p.id) : []), [d, listos]);
  const avail = useAvailability(ids);
  const S = ctx.store;
  useHead({
    title: `${S.name} · Tienda de seguridad electrónica y redes en Costa Rica`,
    description: `Cámaras, grabadores, redes, control de acceso y UPS con precio e IVA incluido. Soporte técnico local, factura electrónica e instalación por ${S.name}.`,
    path: "/",
    image: d?.hero?.image || d?.featured[0]?.image,
    seo: d?.seo,
  });
  const ship = ctx.shipping;
  const datos = [ship?.free_from ? `Envío gratis desde ${money(ship.free_from, S.currency || "CRC")}` : "", ship?.invoice ? "Factura electrónica" : ""].filter(Boolean);
  const hero = d?.hero || null;
  const mosaico = (d?.categories || []).filter((c) => c.count >= MOSAICO_MIN).slice(0, 6);
  const otras = (d?.categories || []).filter((c) => !mosaico.includes(c));
  const sellos = (d?.trust || []).map((t) => (S.kind === "tienda" && sinRetiro(ctx.shipping) ? { ...t, title: quitarRetiro(t.title), text: quitarRetiro(t.text) } : t)).filter((t) => ctx.trustOk || sinCandado(t));
  const banners = (d?.banners || []).slice(0, 3);

  return (
    <>
      {/* ---------- hero: una promesa, un producto real y un solo llamado (O2-08). Fondo noche liso ---------- */}
      <section className="vt-hero" aria-labelledby="vt-title" data-producto={hero ? "si" : undefined}>
        <div className="sh-wrap vt-hero__grid">
          <div className="vt-hero__copy">
            <h1 id="vt-title">{d?.headline || "Protegé tu casa o negocio con equipo profesional."}</h1>
            <p>{d?.subline || "Cámaras, alarmas, redes y respaldo eléctrico con precio final (IVA incluido) y factura electrónica."}</p>
            <div className="vt-hero__cta">
              <Link className="sh-btn sh-btn--crimson" to={to("/arma-tu-sistema")}>Armá tu sistema</Link>
              <Link className="vt-hero__link" to={to("/catalogo")}>Ver catálogo</Link>
            </div>
            {datos.length > 0 && <p className="vt-hero__facts">{datos.join(" · ")}</p>}
          </div>
          {/* el producto de portada: lo elige el panel (con respaldos); es la imagen LCP */}
          {!llego && <span className="vt-hero__prod vt-hero__prod--skel" aria-hidden="true"><span className="vt-hero__img sh-skel" /><span className="vt-hero__ptxt"><span className="sh-skel" style={{ height: 14, width: "40%" }} /><span className="sh-skel" style={{ height: 40, width: "90%" }} /><span className="sh-skel" style={{ height: 28, width: "50%" }} /></span></span>}
          {hero && (
            <Link className="vt-hero__prod" to={to(hero.path)}>
              <span className="vt-hero__img">{hero.image && <img src={hero.image} alt="" fetchPriority="high" loading="eager" decoding="async" width={480} height={480} />}</span>
              <span className="vt-hero__ptxt">
                {hero.brand && <span className="vt-hero__brand">{hero.brand}</span>}
                <b>{hero.name}</b>
                <PriceTag p={hero} />
                <AvailBadge a={avail[hero.id] || hero.availability} compact />
              </span>
            </Link>
          )}
        </div>
      </section>

      {/* ---------- sellos: icono de linea en tinta y dato concreto; garantia y devoluciones solo con el candado ---------- */}
      {!d && (
        <section className="vt-trust" aria-hidden="true">
          <ul className="sh-wrap">{Array.from({ length: 4 }, (_, i) => <li key={i}><span className="vt-trust__ico sh-skel" /><div style={{ flex: 1 }}><b className="sh-skel" style={{ height: 14, width: "60%" }} /><span className="sh-skel" style={{ height: 30, marginTop: 6 }} /></div></li>)}</ul>
        </section>
      )}
      {sellos.length > 0 && (
        <section className="vt-trust" aria-label="Por qué comprar aquí">
          <ul className="sh-wrap">
            {sellos.map((t) => <li key={t.title}><span className="vt-trust__ico"><Ico d={TRUST_IC[t.icon] || IC.shield} size={20} /></span><div><b>{t.title}</b><span>{t.text}</span></div></li>)}
          </ul>
        </section>
      )}

      {err && <div className="sh-wrap vt-empty"><b>No pudimos cargar la portada.</b><Link className="sh-btn sh-btn--ghost" to={to("/catalogo")}>Ver el catálogo</Link></div>}

      {/* ---------- banners administrables: la rejilla se adapta a la cantidad; solo el primero carga de inmediato ---------- */}
      {banners.length > 0 && (
        <section className="sh-wrap vt-banners" aria-label="Destacados" data-n={banners.length}>
          {banners.map((b, i) => {
            const inner = (
              <>
                {b.image && <img src={b.image} alt="" loading={i === 0 ? "eager" : "lazy"} decoding="async" width={1200} height={600} />}
                <span className="vt-banner__txt"><b>{b.title}</b>{b.text && <span>{b.text}</span>}{(b.cta || b.link) && <em>{b.cta || "Ver más"}</em>}</span>
              </>
            );
            const cls = `vt-banner${b.image ? "" : " vt-banner--plain"}`;
            return !b.link ? <div key={b.id || i} className={cls}>{inner}</div>
              : isExternal(b.link) ? <a key={b.id || i} className={cls} href={b.link} target="_blank" rel="noopener noreferrer">{inner}</a>
                : <Link key={b.id || i} className={cls} to={to(b.link)}>{inner}</Link>;
          })}
        </section>
      )}

      {/* ---------- kits: los productos estrella. 1 a ancho completo, 2 en dos columnas, 3 o más en riel. Mientras
           carga, el espacio queda reservado: lo de abajo no salta. ---------- */}
      {!llego && !err && (
        <section className="vt-kits" aria-hidden="true">
          <div className="sh-wrap">
            <div className="vt-sec__head vt-sec__head--dark"><div><h2>Sistemas completos, armados por necesidad</h2></div></div>
            <ul className="vt-kits__list">{Array.from({ length: 3 }, (_, i) => <li key={i}><div className="vt-kit vt-kit--skel"><span className="vt-kit__img" /><span className="vt-kit__body" /></div></li>)}</ul>
          </div>
        </section>
      )}
      {llego && d && d.kits.length > 0 && (
        <section className="vt-kits" aria-labelledby="vt-kits">
          <div className="sh-wrap">
            <div className="vt-sec__head vt-sec__head--dark">
              <div><span className="sh-eyebrow">Kits listos para instalar</span><h2 id="vt-kits">Sistemas completos, armados por necesidad</h2></div>
              <Link to={to("/arma-tu-sistema")} className="vt-more">Armar el mío</Link>
            </div>
            <ul className="vt-kits__list" data-n={Math.min(d.kits.length, 3)}>
              {d.kits.map((k, i) => {
                const qs = new URLSearchParams(Object.entries({ ...k.answers, kit: k.id }).map(([a, b]) => [a, String(b)])).toString();
                return (
                  <li key={k.id}>
                    <Link to={to(`/arma-tu-sistema?${qs}`)} className="vt-kit">
                      <KitPhotos photos={k.photos || (k.image ? [{ image: k.image, label: "Equipo", name: k.title }] : [])} cameras={k.cameras} label={`KIT ${String(i + 1).padStart(2, "0")}`} />
                      <span className="vt-kit__body">
                        <b>{k.title}</b>
                        <span>{k.text}</span>
                        <span className="vt-kit__chips"><span>{k.cameras} cámaras</span><span>{k.items} artículos</span>{Number(k.answers.days) > 0 && <span>{Number(k.answers.days)} días de grabación</span>}</span>
                        <span className="vt-kit__foot">
                          <span className="vt-kit__price"><small>{k.payable === false ? "Precio de referencia" : "Total del equipo"}</small><strong>{money(k.total, d.currency)}</strong><small>IVA incluido</small></span>
                          <span className="vt-kit__cta">Ver el kit<Ico d={IC.next} size={16} /></span>
                        </span>
                        {/* O1-04 h: que incluye y si el precio se paga en linea o se confirma */}
                        <span className="vt-kit__terms">Solo equipo · la instalación se cotiza aparte{k.payable === false && <><br />Precio de referencia · se confirma por WhatsApp</>}</span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            <p className="vt-kits__note">Precios con IVA. Antes de instalar, un técnico revisa que el kit se ajuste a tu sitio.</p>
          </div>
        </section>
      )}

      {/* ---------- listos para entrega: solo existencia propia y solo con 4 o más ---------- */}
      {listos.length > 0 && (
        <section className="sh-wrap vt-sec" aria-labelledby="vt-ready">
          <div className="vt-sec__head"><div><h2 id="vt-ready">Listos para entrega</h2></div><Link to={to("/catalogo?disp=inmediata")} className="vt-more">Ver todo en bodega</Link></div>
          <ul className="vt-grid vt-grid--rail">
            {listos.map((p) => <li key={p.id}><ProductCard p={p} avail={avail[p.id]} badge={enBodega(avail[p.id] || p.availability)} /></li>)}
          </ul>
        </section>
      )}

      {/* ---------- categorias: las principales como mosaico con su foto; las chicas en una linea ---------- */}
      <section className="sh-wrap vt-sec" aria-labelledby="vt-cats">
        <div className="vt-sec__head"><div><h2 id="vt-cats">Comprá por categoría</h2></div><Link to={to("/catalogo")} className="vt-more">Ver todo</Link></div>
        <ul className="vt-cats" aria-busy={!d}>
          {!d ? Array.from({ length: 4 }, (_, i) => <li key={i}><div className="vt-cat vt-cat--skel"><span className="vt-cat__ico sh-skel" /><span className="vt-cat__txt"><span className="sh-skel" style={{ height: 16, width: "70%" }} /><span className="sh-skel" style={{ height: 10, width: "45%" }} /></span></div></li>) : (mosaico.length ? mosaico : (d.categories || []).slice(0, 4)).map((c) => (
            <li key={c.id}>
              <Link to={to(c.path)} className="vt-cat">
                <span className="vt-cat__ico" aria-hidden="true">{c.image ? <img src={c.image} alt="" loading="lazy" decoding="async" width={120} height={120} /> : <Ico d={catIcon(c.name)} size={26} />}</span>
                <span className="vt-cat__txt"><b>{c.name}</b><span className="sh-meta">{c.count} producto{c.count === 1 ? "" : "s"}</span></span>
              </Link>
            </li>
          ))}
        </ul>
        {d && mosaico.length > 0 && otras.length > 0 && (
          <p className="vt-morecats"><span>Más categorías:</span> {otras.map((c, i) => <span key={c.id}>{i > 0 && " · "}<Link to={to(c.path)}>{c.name}</Link></span>)}</p>
        )}
      </section>

      {/* ---------- destacados (O1-04 a): el titulo dice quien los eligio. Sin nada comprable, la seccion no sale ---------- */}
      {(!d || d.featured.length > 0) && (
        <section className="sh-wrap vt-sec" aria-labelledby="vt-feat">
          <div className="vt-sec__head">
            {!d ? <div><h2 id="vt-feat">Destacados</h2></div>
              : d.featured_source === "manual" ? <div><span className="sh-eyebrow">Elegidos por nuestro equipo</span><h2 id="vt-feat">Selección {S.name}</h2></div>
                : <div><h2 id="vt-feat">Del catálogo</h2></div>}
            <Link to={to("/catalogo")} className="vt-more">Ver todo</Link>
          </div>
          {d ? <Grid items={d.featured} avail={avail} rail /> : <Skeleton n={4} rail />}
        </section>
      )}

      {/* ---------- novedades ---------- */}
      {d && d.fresh.length > 0 && (
        <section className="sh-wrap vt-sec" aria-labelledby="vt-new">
          <div className="vt-sec__head"><div><h2 id="vt-new">Recién agregados al catálogo</h2></div><Link to={to("/catalogo?orden=recent")} className="vt-more">Ver más</Link></div>
          <Grid items={d.fresh} avail={avail} rail />
        </section>
      )}

      {/* ---------- marcas, como texto ---------- */}
      {d && d.brands.length > 0 && (
        <section className="sh-wrap vt-sec" aria-labelledby="vt-brands">
          <div className="vt-sec__head"><h2 id="vt-brands">Marcas</h2></div>
          <ul className="vt-brands">
            {d.brands.map((b) => <li key={b.name}><Link to={to(`/catalogo?marca=${encodeURIComponent(b.name)}`)}>{b.name}</Link></li>)}
          </ul>
        </section>
      )}

      {/* ---------- una sola banda de asesoria ---------- */}
      <section className="sh-wrap vt-sec">
        <div className="vt-advice">
          <div>
            <h2>¿Proyecto grande o a la medida?</h2>
            <p>Condominios, bodegas, oficinas, control de acceso o redes completas: un asesor de Crimson visita, mide y te entrega una propuesta sin costo.</p>
          </div>
          <div className="vt-advice__cta">
            <Link className="sh-btn" to={to("/asesoria")}>Solicitar asesoría</Link>
            {S.whatsapp && <a className="vt-textlink" href={waLink(S.whatsapp, `Hola ${S.name}, quiero asesoría para un proyecto.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} size={16} />WhatsApp<span className="sh-sr"> (se abre WhatsApp)</span></a>}
          </div>
        </div>
      </section>
    </>
  );
}
