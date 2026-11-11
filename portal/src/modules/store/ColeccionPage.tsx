/* Coleccion por regla (O4-07), /coleccion/<slug>: los productos que HOY cumplen la regla que el administrador armo en
   Mi Tienda -> Categorias («Listo para entregar», «Cámaras para casa por menos de ₡60 000»...). Sale del snapshot (la
   misma verdad que el listado) via GET /public/store/{slug}/collections/{slug}, que ya trae las tarjetas (trozo
   perezoso que no depende del filtro ni del snapshot del navegador); una direccion que no existe es 404 real
   (nginx, /__exists). Un solo JSON-LD: el bloque seo del servidor (O1-06). Pantalla de decision: nada se anima. */
import { lazy, Suspense, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Crumbs, Grid, Skeleton, useAvailability, useHead, useStore, type SeoBlock, type VProd } from "./storeKit";

const NotFound = lazy(() => import("./StorePages").then((m) => ({ default: m.NotFound })));

type Coleccion = { slug: string; title: string; text: string; n: number; products: VProd[]; currency: string; seo?: SeoBlock | null };

export default function ColeccionPage() {
  const { slug: cslug = "" } = useParams();
  const ctx = useStore();
  const [c, setC] = useState<Coleccion | null>(null);
  const [falta, setFalta] = useState(false);
  useEffect(() => {
    setC(null);
    setFalta(false);
    ctx.api<Coleccion>(`/collections/${encodeURIComponent(cslug)}`).then(setC).catch(() => setFalta(true));
  }, [ctx, cslug]);
  const items = c?.products || [];
  const avail = useAvailability(items.map((p) => p.id));
  useHead(c ? { title: `${c.title} · ${ctx.store.name}`, description: c.text || undefined, path: `/coleccion/${c.slug}`, image: items[0]?.image, seo: c.seo } : null);
  if (falta) return <Suspense fallback={null}><NotFound /></Suspense>;
  return (
    <div className="sh-wrap vt-sec">
      <Crumbs items={[{ name: "Tienda", path: "/" }, { name: c?.title || "Colección" }]} />
      <div className="vt-sec__head"><div><h1 id="vt-col">{c?.title || "\u00a0"}</h1>{c?.text && <p>{c.text}</p>}</div></div>
      {!c ? <Skeleton n={8} /> : items.length ? <Grid items={items} avail={avail} /> : <p className="vt-empty"><b>Hoy no hay productos en esta colección.</b></p>}
    </div>
  );
}
