/* Ficha de producto: /p/<nombre>-<id>. O2-07: en orden de decision. Arriba del boton solo lo que decide (galeria,
   marca, nombre, precio con IVA, disponibilidad, envio, cantidad y boton); lo demas va debajo, plegado y presente en la
   pagina (Lo esencial, descripcion, especificaciones, accesorios y relacionados). En celular la barra fija aparece solo
   cuando el boton principal sale de la pantalla. Visor a pantalla completa con zoom (todo dentro de la pagina).
   Si el snapshot está cargado, muestra nombre/foto/precio/disponibilidad al instante sin esperar /item. */
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AvailBadge, Crumbs, EmptyHelp, Grid, IC, Ico, PriceTag, ProductCard, assistAction, cannotBuy, money, normAvail, useAvailability, useHead, usePath, useStore, waLink, ShipInfo, type SeoBlock, type Variant, type VProd } from "./storeKit";
import { mensajePedido } from "./pedidoWa";
import { pidFromRef, snapToVProd } from "./catalogFilter";
import { useSnapshot } from "./useSnapshot";
import { conNombreVisible } from "./storeNames";

type Detail = VProd & {
  description: string | null; specs: Record<string, string>; datasheet_url: string | null; net_price: string | number; tax_amount: string | number;
  breadcrumbs: { name: string; path: string }[]; related: VProd[]; accessories: { title: string; reason: string; items: VProd[] }[]; whatsapp: string;
  seo?: SeoBlock | null;
  /** O3-01 (aditivo): meses de garantia del fabricante; solo se publica con el candado legal (O1-00) */
  warranty_months?: number | null;
};

/** «¿Lo instalamos nosotros?» solo en seguridad y redes */
const INSTALABLE = /segur|c[aá]mara|vigil|cctv|grabador|acceso|alarma|cerradura|videoportero|red(es)?\b|wifi|switch|router|cable|conectiv/i;
/** datos que ya estan arriba (o no dicen nada) y no van en «Lo esencial» */
const SIN_ESENCIAL = new Set(["Tipo", "Marca", "Modelo", "Impuesto"]);
/** descripcion que no aporta: repite el nombre o es la linea del proveedor en mayusculas */
function descUtil(desc: string | null, name: string): boolean {
  const t = (desc || "").trim();
  if (t.length < 24) return false;
  const norm = (x: string) => x.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  if (norm(t) === norm(name) || norm(name).includes(norm(t))) return false;
  const letras = t.replace(/[^A-Za-zÁÉÍÓÚÑáéíóúñ]/g, "");
  const mayus = t.replace(/[^A-ZÁÉÍÓÚÑ]/g, "").length;
  return !(letras.length > 0 && mayus / letras.length > 0.7);
}

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
  // ya lo agrego: la linea «Agregado. En tu carrito: n · Ver carrito» queda debajo del boton hasta salir de la ficha
  const [inCart, setInCart] = useState(false);
  const [shared, setShared] = useState(false);
  // O1-03: la ficha completa (/item) ya llego. Antes de eso solo hay la tarjeta del snapshot, sin el desglose de IVA:
  // no se pinta «Precio sin IVA ₡X + IVA 13 % ₡0» con datos a medias, solo el precio final y «IVA incluido».
  const [full, setFull] = useState(false);
  const [zoom, setZoom] = useState(false);
  // barra fija (celular): solo cuando el boton principal no esta a la vista
  const [mainEl, mainBtn] = useState<HTMLDivElement | null>(null); // ref de funcion: sigue al boton aunque cambie de rama
  const [mainVisible, setMainVisible] = useState(true);

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
    setMissing(false); setPhoto(0); setQty(1); setFull(false);
    // Mostrar datos del snapshot inmediatamente si están en caché (cero latencia)
    if (snapCard) {
      setD({
        ...conNombreVisible(snapCard),
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
      .then((x) => { if (!alive) return; setD(conNombreVisible(x)); setVariant(x.variants?.[0]); setFull(true); })
      .catch(() => { if (alive) { setD(null); setMissing(true); } });
    return () => { alive = false; };
  // snapCard se lee sincrónicamente en el efecto; no se incluye en deps para no re-ejecutar cuando el snap llega tarde
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, ctx]);
  useEffect(() => setInCart(false), [ref]);
  // el nombre cambio -> direccion canonica sin recargar
  useEffect(() => { if (d && `/p/${ref}` !== d.path) nav(to(d.path), { replace: true }); }, [d]); // eslint-disable-line react-hooks/exhaustive-deps
  // O2-07: la barra fija aparece solo cuando el boton principal sale de pantalla
  useEffect(() => {
    if (!mainEl || typeof IntersectionObserver !== "function") { setMainVisible(true); return; }
    // la raiz se estira hacia abajo: «visible» = el boton no quedo arriba de la pantalla. Asi un salto directo de abajo
    // hacia arriba (ancla, Fin, scrollIntoView) tambien cambia el estado
    const io = new IntersectionObserver(([e]) => setMainVisible(e.isIntersecting), { rootMargin: "0px 0px 100000px 0px" });
    io.observe(mainEl);
    return () => io.disconnect();
  }, [mainEl]);

  const ids = useMemo(() => (d ? [d.id, ...d.related.map((r) => r.id), ...d.accessories.flatMap((g) => g.items.map((i) => i.id))] : []), [d]);
  const avail = useAvailability(ids);
  const a = normAvail((d && avail[d.id]) || d?.availability);
  // tope en linea (max_qty del API: lo propio libre + lo que el proveedor confirma); sin dato, 999 y el checkout valida
  const maxQty = a?.max_qty && a.max_qty > 0 ? Math.min(999, a.max_qty) : 999;
  useEffect(() => { if (qty > maxQty) setQty(maxQty); }, [maxQty, qty]);

  const S = ctx.store;
  const url = typeof location !== "undefined" ? location.href.split("?")[0] : "";
  // O1-06: la descripcion, Open Graph y el JSON-LD de la ficha son los del API (bloque seo, el mismo del SSI); la ficha
  // ya no arma los suyos
  useHead(d ? { title: `${d.name} · ${S.name}`, path: d.path, image: d.image, type: "product", seo: d.seo }
    : missing ? { title: `Producto no disponible · ${S.name}`, noindex: true } : null);

  if (missing) return <div className="sh-wrap"><EmptyHelp title="Este producto ya no está disponible." text="Puede que lo hayamos reemplazado por un modelo nuevo. Te ayudamos a encontrar el equivalente."><Link className="sh-btn" to={to("/catalogo")}>Ver catálogo</Link></EmptyHelp></div>;
  if (!d) return <div className="sh-wrap"><div className="vt-pdp" style={{ paddingTop: 24 }}><div className="sh-skel" style={{ aspectRatio: "1" }} /><div style={{ display: "grid", gap: 12, alignContent: "start" }}><div className="sh-skel" style={{ height: 22, width: "40%" }} /><div className="sh-skel" style={{ height: 70 }} /><div className="sh-skel" style={{ height: 160 }} /></div></div></div>;

  const imgs = d.images?.length ? d.images : d.image ? [d.image] : [];
  const price = variant?.display_price ?? d.display_price;
  const shown = { ...d, display_price: price };
  const ask = d.price_on_request || !Number(price);
  const soldOut = cannotBuy(a);
  // O1-02, venta asistida: lo que no se vende en linea y no esta agotado («por confirmar») se pide por WhatsApp con un
  // boton activo y el precio de referencia; nunca un boton apagado. accion = ninguna: solo la nota y el enlace.
  const agotado = a?.state === "agotado";
  const assist = soldOut && !agotado;
  const act = assistAction(a, S.whatsapp);
  const pedidoHref = act === "whatsapp" ? waLink(S.whatsapp, mensajePedido({ id: d.id, nombre: d.name, codigo: d.code, cantidad: qty, precio: price, moneda: d.display_currency, url })) : "";
  const agotadoHref = S.whatsapp ? waLink(S.whatsapp, `Hola, ¿cuándo vuelven a tener ${d.name}${d.code ? ` (código ${d.code})` : ""}?
${url}`) : "";
  const contactHref = to(`/asesoria?producto=${d.id}&cantidad=${qty}`);
  const net = variant ? Number(price) / (1 + d.tax_rate / 100) : Number(d.net_price);
  const iva = Number(price) - net;
  const save = d.compare_display_price && Number(d.compare_display_price) > Number(price) ? Number(d.compare_display_price) - Number(price) : 0;
  const waText = `Hola ${S.name}, me interesa: ${d.name}${d.code ? ` (${d.code})` : ""}${qty > 1 ? ` · ${qty} unidades` : ""}\n${url}`;
  const installHref = to(`/asesoria?producto=${d.id}&cantidad=${qty}&instalar=1`);
  const inCompare = ctx.compare.includes(d.id);
  const buyable = !ask && !soldOut;
  const enCarrito = ctx.cartQty?.(d.id) ?? 0;
  const instalable = INSTALABLE.test([d.category, d.type, ...d.breadcrumbs.map((b) => b.name)].filter(Boolean).join(" "));
  // fila de confianza: garantia y devoluciones solo con el candado legal (O1-00) y con el dato; sin candado, lo demas
  const factura = ctx.shipping?.invoice ?? true;
  const garantia = ctx.trustOk && d.warranty_months ? `Garantía: ${d.warranty_months} meses del fabricante` : "";
  const confianza = [garantia, factura ? "Factura electrónica" : "", ctx.payText || ""].filter(Boolean);
  const esencial = Object.entries(d.specs).filter(([k, v]) => !SIN_ESENCIAL.has(k) && v && v.length < 60).slice(0, 5);
  const verDesc = descUtil(d.description, d.name);
  const reporte = S.whatsapp ? waLink(S.whatsapp, `Hola ${S.name}, creo que un dato de esta ficha está mal: ${d.name}${d.code ? ` (código ${d.code})` : ""}\n${url}`)
    : S.business?.email ? `mailto:${S.business.email}?subject=${encodeURIComponent(`Dato incorrecto: ${d.name}`)}&body=${encodeURIComponent(url)}` : "";

  const add = () => {
    ctx.addToCart(d, qty, variant);
    setInCart(true); // el foco no se mueve: el aviso de 5 s y la linea de abajo lo anuncian
  };
  const share = async () => {
    const data = { title: d.name, text: `${d.name} · ${S.name}`, url };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(url);
      setShared(true); window.setTimeout(() => setShared(false), 2000);
    } catch { /* cancelado por la persona */ }
  };
  const marcaCat = [d.brand, d.category].filter(Boolean).join(" · ");
  const modeloCodigo = [d.model && d.model !== d.code && `Modelo ${d.model}`, d.code && `Código ${d.code}`].filter(Boolean).join(" · ");

  return (
    <div className="sh-wrap vt-product" data-sticky={!mainVisible || undefined}>
      <Crumbs items={[...d.breadcrumbs, { name: d.name }]} />
      <div className="vt-pdp">
        <Gallery imgs={imgs} name={d.name} photo={photo} setPhoto={setPhoto} onZoom={() => setZoom(true)} />
        <div className="vt-info">
          {marcaCat && <p className="vt-info__brand">{marcaCat}</p>}
          <h1>{d.name}</h1>
          {modeloCodigo && <p className="vt-info__code">{modeloCodigo}</p>}
          <div className="vt-info__price">
            <PriceTag p={shown} big />
            <AvailBadge a={a} />
          </div>
          {!ask && full && (
            <p className="vt-breakdown">
              Precio sin IVA {money(net, d.display_currency)} + IVA {d.tax_rate} % {money(iva, d.display_currency)}
              {save > 0 && <b> · Ahorrás {money(save, d.display_currency)}</b>}
            </p>
          )}
          {buyable && ctx.isShop && <ShipInfo price={Number(price)} cur={d.display_currency} />}
          {(d.variants?.length ?? 0) > 0 && (
            <fieldset className="vt-variants"><legend className="sh-meta">Opciones</legend>
              {d.variants.map((v) => <button key={v.id} type="button" className="sh-chip" aria-pressed={variant?.id === v.id} onClick={() => setVariant(v)}>{v.name}</button>)}
            </fieldset>
          )}

          <div className="vt-buybox" id="comprar">
            {ask ? (
              <>
                <p className="vt-buybox__note">Este equipo se cotiza según el proyecto. Escribinos y te pasamos el precio.</p>
                <div ref={mainBtn} className="vt-buybox__main">
                  {S.whatsapp ? <a className="sh-btn sh-btn--wa sh-btn--block" href={waLink(S.whatsapp, `Hola ${S.name}, quiero precio de: ${d.name} (${d.code})\n${url}`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Pedir precio por WhatsApp</a>
                    : <Link className="sh-btn sh-btn--block" to={contactHref}>Solicitar cotización</Link>}
                </div>
                {S.whatsapp && <Link className="vt-textlink" to={contactHref}>Solicitar cotización</Link>}
              </>
            ) : assist ? (
              <>
                {act === "whatsapp" && (
                  <div className="vt-buy" ref={mainBtn}>
                    <Qty value={qty} max={maxQty} onChange={setQty} />
                    <a className="sh-btn sh-btn--wa vt-buy__add vt-buy__wa" href={pedidoHref} target="_blank" rel="noopener noreferrer" aria-describedby="vt-wa-hint"><Ico d={IC.wa} />Pedir por WhatsApp</a>
                  </div>
                )}
                {act === "whatsapp" && <p className="vt-buybox__hint" id="vt-wa-hint">Se abre WhatsApp</p>}
                <p className="vt-buybox__note">Lo traemos del distribuidor. Precio de referencia: te confirmamos precio final, existencia y fecha de entrega antes de que pagués.</p>
                <Link className="vt-textlink" to={contactHref}>Prefiero que me contacten</Link>
              </>
            ) : agotado ? (
              <>
                {S.whatsapp ? <a className="vt-textlink" href={agotadoHref} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} size={16} />Consultar por WhatsApp</a> : <Link className="vt-textlink" to={contactHref}>Prefiero que me contacten</Link>}
              </>
            ) : (
              <>
                <div className="vt-buy" ref={mainBtn}>
                  <Qty value={qty} max={maxQty} onChange={setQty} />
                  <button type="button" className="sh-btn sh-btn--crimson vt-buy__add" onClick={add}>
                    <Ico d={IC.bag} />{ctx.isShop ? "Agregar al carrito" : "Agregar a la cotización"}
                  </button>
                </div>
                {/* pedido 21/10: el aviso de 5 s con «Ir al carrito» sigue; esta linea queda fija y el foco no se mueve */}
                <p className="vt-added" role="status">
                  {inCart && enCarrito > 0 ? <>Agregado. En tu {ctx.isShop ? "carrito" : "cotización"}: {enCarrito} · <button type="button" className="vt-added__go" onClick={ctx.openCart}>Ver {ctx.isShop ? "carrito" : "cotización"}</button></> : null}
                </p>
                {a && (a.state === "disponible" || a.state === "ultimas") && a.cantidad != null && a.cantidad > 0 && a.cantidad < 999 && a.max_qty && a.max_qty > a.cantidad ? <p className="vt-buybox__note">{a.max_qty - a.cantidad} más bajo pedido.</p> : null}
                {confianza.length > 0 && (
                  <p className="vt-trustrow">
                    <Ico d={IC.doc} size={16} /><span>{confianza.join(" · ")}{ctx.trustOk && <> · <Link to={to("/info/envios")}>Cambios y devoluciones</Link></>}</span>
                  </p>
                )}
                {instalable && (
                  <Link className="vt-install" to={installHref}>
                    <Ico d={IC.tool} /><span><b>¿Lo instalamos nosotros?</b><small>Te cotizamos la instalación sin compromiso</small></span><Ico d={IC.next} />
                  </Link>
                )}
                {S.whatsapp && <a className="vt-textlink" href={waLink(S.whatsapp, waText)} target="_blank" rel="noopener noreferrer" aria-describedby="vt-wa-hint2"><Ico d={IC.wa} size={16} />Consultar por WhatsApp<span className="sh-sr" id="vt-wa-hint2"> (se abre WhatsApp)</span></a>}
              </>
            )}
            <div className="vt-buybox__tools">
              <button type="button" className="vt-tool" aria-pressed={inCompare} onClick={() => ctx.toggleCompare(d.id)} disabled={!inCompare && ctx.compare.length >= 3}><Ico d={IC.compare} />{inCompare ? "En el comparador" : "Comparar"}</button>
              <button type="button" className="vt-tool" onClick={share}><Ico d={shared ? IC.check : IC.share} />{shared ? "Enlace copiado" : "Compartir"}</button>
              {d.datasheet_url && <a className="vt-tool" href={d.datasheet_url} target="_blank" rel="noopener noreferrer"><Ico d={IC.pdf} />Ficha técnica (PDF)</a>}
            </div>
          </div>
        </div>
      </div>

      {/* debajo, plegado: el contenido esta en la pagina (Google y el buscador del navegador lo encuentran) */}
      <div className="vt-pdp__detail">
        {esencial.length >= 3 && (
          <Fold id="esencial" title="Lo esencial" open>
            <ul className="vt-essentials">{esencial.map(([k, v]) => <li key={k}><b>{k}:</b> {v}</li>)}</ul>
          </Fold>
        )}
        {verDesc && (
          <Fold id="desc" title="Descripción">
            <p className="sh-desc">{d.description}</p>
          </Fold>
        )}
        {Object.keys(d.specs).length > 0 && (
          <Fold id="specs" title="Especificaciones">
            <div className="vt-specs">
              <p className="vt-specs__src">Datos según el distribuidor{reporte && <> · <a href={reporte} target={reporte.startsWith("http") ? "_blank" : undefined} rel="noopener noreferrer">Reportar un dato incorrecto</a></>}</p>
              <dl>{Object.entries(d.specs).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}<div><dt>Impuesto</dt><dd>IVA {d.tax_rate} % incluido</dd></div></dl>
            </div>
          </Fold>
        )}
      </div>

      {d.accessories.length > 0 && (
        <section className="vt-sec" aria-labelledby="vt-acc">
          <div className="vt-sec__head"><div><span className="sh-eyebrow">Para que funcione completo</span><h2 id="vt-acc">¿Qué necesito además?</h2></div><Link className="vt-more" to={to("/arma-tu-sistema")}>Armar el sistema completo</Link></div>
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
          <div className="vt-sec__head"><h2 id="vt-rel">También te puede servir</h2>{d.category_path && <Link className="vt-more" to={to(d.category_path)}>Ver {d.category}</Link>}</div>
          <Grid items={d.related} avail={avail} rail />
        </section>
      )}

      {/* barra fija de compra en celular: solo cuando el boton principal ya no se ve (no tapa nada mientras esta a la vista) */}
      <div className="vt-stickybuy" data-show={!mainVisible}>
        <div><PriceTag p={shown} /></div>
        {ask ? <a className="sh-btn sh-btn--crimson" href="#comprar">Pedir precio</a>
          : assist ? (act === "whatsapp"
            ? <a className="sh-btn sh-btn--wa" href={pedidoHref} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Pedir por WhatsApp</a>
            : <Link className="vt-textlink" to={contactHref}>Prefiero que me contacten</Link>)
          : agotado ? (S.whatsapp
            ? <a className="sh-btn sh-btn--ghost" href={agotadoHref} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Consultar</a>
            : <Link className="vt-textlink" to={contactHref}>Prefiero que me contacten</Link>)
          : <button type="button" className="sh-btn sh-btn--crimson" onClick={add}><Ico d={IC.bag} />Agregar</button>}
      </div>

      {zoom && <Lightbox imgs={imgs} start={photo} name={d.name} onClose={(i) => { setPhoto(i); setZoom(false); }} />}
    </div>
  );
}

/** Bloque plegable: boton con aria-expanded y el contenido siempre en la pagina (oculto con hidden="until-found" donde
    el navegador lo entiende, para que la busqueda de la pagina lo abra). */
function Fold({ id, title, open = false, children }: { id: string; title: string; open?: boolean; children: ReactNode }) {
  const [on, setOn] = useState(open);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const abrir = () => setOn(true);
    el.addEventListener("beforematch", abrir);
    return () => el.removeEventListener("beforematch", abrir);
  }, []);
  return (
    <section className="vt-fold" aria-labelledby={`vt-${id}-t`}>
      <h2 id={`vt-${id}-t`}><button type="button" aria-expanded={on} aria-controls={`vt-${id}`} onClick={() => setOn(!on)}>{title}<Ico d={on ? IC.minus : IC.plus} size={18} /></button></h2>
      <div id={`vt-${id}`} ref={body} className="vt-fold__body" hidden={on ? undefined : ("until-found" as unknown as boolean)}>{children}</div>
    </section>
  );
}

function Qty({ value, onChange, max }: { value: number; onChange: (n: number) => void; max: number }) {
  const clamp = (n: number) => Math.max(1, Math.min(max, n || 1));
  return (
    <span className="sh-qty vt-qty">
      {/* en el minimo se marca con aria-disabled y no con disabled: sigue en el orden del teclado y la zona de compra
          nunca muestra un boton apagado (O1-02); clamp ya impide bajar de 1 */}
      <button type="button" onClick={() => onChange(clamp(value - 1))} aria-label="Quitar uno" aria-disabled={value <= 1}>−</button>
      <input inputMode="numeric" value={value} onChange={(e) => onChange(clamp(Number(e.target.value.replace(/\D/g, ""))))} aria-label="Cantidad" />
      <button type="button" onClick={() => onChange(clamp(value + 1))} aria-label="Agregar uno" disabled={value >= max}>+</button>
    </span>
  );
}

/** O2-07: galeria 1:1 que se desliza con el dedo (scroll nativo con snap), con «anterior» y «siguiente» y miniaturas
    que se alcanzan con el teclado. Sin rotulos de visor. Un toque o clic en la foto abre el visor a pantalla completa. */
function Gallery({ imgs, name, photo, setPhoto, onZoom }: { imgs: string[]; name: string; photo: number; setPhoto: (n: number) => void; onZoom: () => void }) {
  const track = useRef<HTMLDivElement>(null);
  const n = imgs.length;
  const ir = (i: number) => {
    const k = (i + n) % n;
    setPhoto(k);
    const el = track.current;
    if (el) el.scrollTo({ left: k * el.clientWidth, behavior: "auto" });
  };
  // al deslizar con el dedo, la foto visible manda (sin trabajo por cada evento: se lee al terminar)
  useEffect(() => {
    const el = track.current;
    if (!el || n < 2) return;
    let t = 0;
    const onScroll = () => { window.clearTimeout(t); t = window.setTimeout(() => setPhoto(Math.round(el.scrollLeft / Math.max(1, el.clientWidth))), 80); };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => { el.removeEventListener("scroll", onScroll); window.clearTimeout(t); };
  }, [n, setPhoto]);
  return (
    <div className="vt-gallery">
      <div className="vt-gallery__main">
        {n ? (
          <div className="vt-gallery__track" ref={track}>
            {imgs.map((u, i) => (
              <button key={u + i} type="button" className="vt-gallery__slide" onClick={onZoom} tabIndex={i === photo ? 0 : -1} aria-label={`Ampliar foto ${i + 1} de ${n}: ${name}`}>
                <img src={u} alt={n > 1 ? `${name}, foto ${i + 1} de ${n}` : name} fetchPriority={i === 0 ? "high" : undefined} loading={i === 0 ? "eager" : "lazy"} decoding="async" width={800} height={800} />
              </button>
            ))}
          </div>
        ) : <span className="sh-noimg">Sin foto</span>}
        {n > 1 && (
          <>
            <button type="button" className="vt-gallery__nav vt-gallery__nav--prev" onClick={() => ir(photo - 1)} aria-label="Foto anterior"><Ico d={IC.back} /></button>
            <button type="button" className="vt-gallery__nav vt-gallery__nav--next" onClick={() => ir(photo + 1)} aria-label="Foto siguiente"><Ico d={IC.next} /></button>
            <span className="vt-gallery__count" aria-hidden="true">{photo + 1} / {n}</span>
          </>
        )}
      </div>
      {n > 1 && (
        <ul className="vt-thumbs" aria-label="Fotos">
          {imgs.map((u, i) => <li key={u + i}><button type="button" className="vt-thumb" aria-current={i === photo} aria-label={`Foto ${i + 1} de ${n}`} onClick={() => ir(i)}><img src={u} alt="" loading="lazy" decoding="async" width={72} height={72} /></button></li>)}
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
