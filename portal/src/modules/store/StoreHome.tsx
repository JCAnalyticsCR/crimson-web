/* Portada de la tienda: "centro de monitoreo" con mesura. Pantalla de decision: el titular y los CTA estan
   desde el primer segundo (movil incluido); nada se mueve solo salvo el punto REC (y no bajo reduced-motion).
   Todo en un solo viaje al API (/showcase); las imagenes bajo el pliegue son perezosas. */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Grid, IC, Ico, Skeleton, Viewfinder, money, useAvailability, useHead, usePath, useStore, waLink, type VProd } from "./storeKit";

type Cat = { id: number; name: string; path: string; count: number; image: string | null; children: { name: string; path: string; count: number }[] };
type Kit = { id: string; title: string; text: string; answers: Record<string, unknown>; items: number; total: number | string; image: string | null; cameras: number };
type Banner = { id: string; image: string; title: string; text: string; link: string; cta: string };
type Showcase = {
  headline: string; subline: string; banners: Banner[]; trust: { icon: string; title: string; text: string }[];
  categories: Cat[]; brands: { name: string; count: number }[]; featured: VProd[]; fresh: VProd[]; kits: Kit[];
  product_count: number; currency: string; info: { kind: string; title: string; path: string }[];
};

const TRUST_IC: Record<string, string> = { shield: IC.shield, tool: IC.tool, truck: IC.truck, doc: IC.doc, chat: IC.chat, clock: IC.clock };
const isExternal = (u: string) => /^https?:\/\//.test(u);

/** hora local tipo timecode del monitor (se fija al cargar: no es un reloj que repinte) */
const stamp = () => new Intl.DateTimeFormat("es-CR", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date());

export default function StoreHome() {
  const ctx = useStore();
  const to = usePath();
  const [d, setD] = useState<Showcase | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { ctx.api<Showcase>("/showcase").then(setD).catch(() => setErr(true)); }, [ctx]);
  const ids = useMemo(() => (d ? [...d.featured, ...d.fresh].map((p) => p.id) : []), [d]);
  const avail = useAvailability(ids);
  const S = ctx.store;
  useHead({
    title: `${S.name} · Tienda de seguridad electrónica y redes en Costa Rica`,
    description: `Cámaras, grabadores, redes, control de acceso y UPS con precio e IVA incluido. Soporte técnico local, factura electrónica e instalación por ${S.name}.`,
    path: "/",
    image: d?.featured[0]?.image,
  });
  const time = useMemo(stamp, []);
  const wall = (d?.featured || []).filter((p) => p.image).slice(0, 4);

  return (
    <>
      {/* ---------- hero: titular + CTA primero; el muro de monitores es decoracion (solo escritorio) ---------- */}
      <section className="vt-hero" aria-labelledby="vt-title">
        <div className="sh-wrap vt-hero__grid">
          <div className="vt-hero__copy">
            <span className="sh-eyebrow">Tienda {S.name} · Costa Rica</span>
            <h1 id="vt-title">{d?.headline || <>Equipo profesional de seguridad, <em>listo para instalar</em>.</>}</h1>
            <p>{d?.subline || "Cámaras, grabadores, redes y control de acceso con precio final, factura electrónica y técnicos locales que responden."}</p>
            <div className="vt-hero__cta">
              <Link className="sh-btn sh-btn--crimson" to={to("/arma-tu-sistema")}><Ico d={IC.cam} />Arma tu sistema</Link>
              <Link className="sh-btn vt-btn--line" to={to("/catalogo")}>Ver catálogo</Link>
            </div>
            <div className="vt-hero__facts sh-meta">
              <span><i className="sh-live" />{d ? `${d.product_count} productos` : "Catálogo en línea"}</span>
              <span>Precios con IVA</span>
              <span>Factura electrónica</span>
            </div>
          </div>
          <div className="vt-wall" aria-hidden="true">
            {wall.length ? wall.map((p, i) => (
              <Viewfinder key={p.id} className="vt-wall__cell" label={`CAM 0${i + 1} · ${(p.type || p.category || "").toUpperCase().slice(0, 14)}`} right={i === 0 ? `${time} · REC` : undefined} live={i === 0}>
                <img src={p.image!} alt="" loading="lazy" decoding="async" width={300} height={300} />
              </Viewfinder>
            )) : Array.from({ length: 4 }, (_, i) => <div key={i} className="vt-wall__cell vt-wall__cell--empty" />)}
          </div>
        </div>
      </section>

      {/* ---------- sellos de confianza ---------- */}
      <section className="vt-trust" aria-label="Por qué comprar aquí">
        <ul className="sh-wrap">
          {(d?.trust || []).map((t) => (
            <li key={t.title}><Ico d={TRUST_IC[t.icon] || IC.shield} size={22} /><div><b>{t.title}</b><span>{t.text}</span></div></li>
          ))}
        </ul>
      </section>

      {err && <div className="sh-wrap vt-empty"><b>No pudimos cargar la portada.</b><Link className="sh-btn sh-btn--ghost" to={to("/catalogo")}>Ver el catálogo</Link></div>}

      {/* ---------- banners administrables (sin carrusel: se ven todos, nada rota solo) ---------- */}
      {d && d.banners.length > 0 && (
        <section className="sh-wrap vt-banners" aria-label="Destacados">
          {d.banners.slice(0, 3).map((b, i) => {
            const inner = (
              <>
                {b.image && <img src={b.image} alt="" loading={i === 0 ? "eager" : "lazy"} decoding="async" />}
                <span className="vt-banner__txt"><b>{b.title}</b>{b.text && <span>{b.text}</span>}{(b.cta || b.link) && <em>{b.cta || "Ver más"} →</em>}</span>
              </>
            );
            const cls = `vt-banner${i === 0 ? " vt-banner--main" : ""}${b.image ? "" : " vt-banner--plain"}`;
            return !b.link ? <div key={b.id || i} className={cls}>{inner}</div>
              : isExternal(b.link) ? <a key={b.id || i} className={cls} href={b.link} target="_blank" rel="noopener noreferrer">{inner}</a>
                : <Link key={b.id || i} className={cls} to={to(b.link)}>{inner}</Link>;
          })}
        </section>
      )}

      {/* ---------- categorias ---------- */}
      <section className="sh-wrap vt-sec" aria-labelledby="vt-cats">
        <div className="vt-sec__head"><h2 id="vt-cats">Comprá por categoría</h2><Link to={to("/catalogo")} className="vt-more">Todo el catálogo →</Link></div>
        {!d ? <div className="vt-cats">{Array.from({ length: 6 }, (_, i) => <div key={i} className="sh-skel" style={{ height: 120 }} />)}</div> : (
          <ul className="vt-cats">
            {d.categories.map((c, i) => (
              <li key={c.id}>
                <Link to={to(c.path)} className="vt-cat">
                  <Viewfinder className="vt-cat__img" label={`CAM ${String(i + 1).padStart(2, "0")}`}>{c.image ? <img src={c.image} alt="" loading="lazy" decoding="async" width={160} height={160} /> : <Ico d={IC.cam} size={30} />}</Viewfinder>
                  <span className="vt-cat__txt"><b>{c.name}</b><span className="sh-meta">{c.count} producto{c.count === 1 ? "" : "s"}</span></span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ---------- kits por necesidad ---------- */}
      {d && d.kits.length > 0 && (
        <section className="vt-kits" aria-labelledby="vt-kits">
          <div className="sh-wrap">
            <div className="vt-sec__head vt-sec__head--dark">
              <div><span className="sh-eyebrow">Kits listos</span><h2 id="vt-kits">Sistemas armados por necesidad</h2></div>
              <Link to={to("/arma-tu-sistema")} className="vt-more">Armar el mío →</Link>
            </div>
            <ul className="vt-kits__list">
              {d.kits.map((k, i) => {
                const qs = new URLSearchParams(Object.entries(k.answers).map(([a, b]) => [a, String(b)])).toString();
                return (
                  <li key={k.id}>
                    <Link to={to(`/arma-tu-sistema?${qs}`)} className="vt-kit">
                      <Viewfinder className="vt-kit__img" label={`KIT ${String(i + 1).padStart(2, "0")}`} right={`${k.cameras} CAM`}>
                        {k.image ? <img src={k.image} alt="" loading="lazy" decoding="async" width={240} height={240} /> : <Ico d={IC.cam} size={34} />}
                      </Viewfinder>
                      <span className="vt-kit__body">
                        <b>{k.title}</b>
                        <span>{k.text}</span>
                        <span className="vt-kit__foot"><span className="sh-meta">{k.items} artículos · desde</span><strong>{money(k.total, d.currency)}</strong></span>
                        <em>Ver qué incluye →</em>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            <p className="vt-kits__note">Precio de referencia del equipo con IVA. Un técnico valida el kit antes de cobrar; la instalación se cotiza aparte.</p>
          </div>
        </section>
      )}

      {/* ---------- destacados ---------- */}
      <section className="sh-wrap vt-sec" aria-labelledby="vt-feat">
        <div className="vt-sec__head"><div><span className="sh-eyebrow"><i className="vt-rec" />Cam · en vivo</span><h2 id="vt-feat">Lo más pedido</h2></div><Link to={to("/catalogo")} className="vt-more">Ver todo →</Link></div>
        {d ? <Grid items={d.featured} avail={avail} /> : <Skeleton n={4} />}
      </section>

      {/* ---------- configurador (banda) ---------- */}
      <section className="sh-wrap">
        <div className="vt-band">
          <div>
            <span className="sh-eyebrow">4 preguntas · 1 minuto</span>
            <h2>¿No sabés qué cámaras comprar?</h2>
            <p>Decinos qué querés proteger y te armamos el sistema con equipo real de la tienda: cámaras, grabador, disco y cableado. Un técnico lo revisa antes de cobrarte.</p>
          </div>
          <Link className="sh-btn sh-btn--crimson" to={to("/arma-tu-sistema")}><Ico d={IC.cam} />Arma tu sistema</Link>
        </div>
      </section>

      {/* ---------- novedades ---------- */}
      {d && d.fresh.length > 0 && (
        <section className="sh-wrap vt-sec" aria-labelledby="vt-new">
          <div className="vt-sec__head"><h2 id="vt-new">Novedades</h2><Link to={to("/catalogo?orden=recent")} className="vt-more">Ver más →</Link></div>
          <Grid items={d.fresh} avail={avail} />
        </section>
      )}

      {/* ---------- marcas ---------- */}
      {d && d.brands.length > 0 && (
        <section className="sh-wrap vt-sec" aria-labelledby="vt-brands">
          <div className="vt-sec__head"><h2 id="vt-brands">Marcas que instalamos</h2></div>
          <ul className="vt-brands">
            {d.brands.map((b) => <li key={b.name}><Link to={to(`/catalogo?marca=${encodeURIComponent(b.name)}`)}>{b.name}<i>{b.count}</i></Link></li>)}
          </ul>
        </section>
      )}

      {/* ---------- asesoria ---------- */}
      <section className="sh-wrap vt-sec">
        <div className="vt-advice">
          <div>
            <span className="sh-eyebrow">Asesoría sin costo</span>
            <h2>¿Proyecto grande o a la medida?</h2>
            <p>Condominios, bodegas, oficinas, control de acceso o redes completas: un asesor de Crimson visita, mide y te entrega una propuesta.</p>
          </div>
          <div className="vt-advice__cta">
            <Link className="sh-btn sh-btn--crimson" to={to("/asesoria")}>Solicitar asesoría</Link>
            {S.whatsapp && <a className="sh-btn sh-btn--wa" href={waLink(S.whatsapp, `Hola ${S.name}, quiero asesoría para un proyecto.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />WhatsApp</a>}
          </div>
        </div>
      </section>
    </>
  );
}
