/* Mi Tienda -> SEO (O4-08): solo lo propio del sitio. Titulo y descripcion de la portada (antes fijos en codigo),
   imagen para compartir, vista previa del resultado en Google, el mapa del sitio con su conteo y los titulos de
   producto que Google corta, con enlace al filtro «Nombre largo». Sin medicion: el dueno no usa Google Analytics, Ads
   ni pixeles de terceros, y aqui no hay donde ponerlos. Guardado unico (O4-02). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../../lib/api";
import { Card, Field, Loading } from "../../../ui/components";
import { ImageField } from "../../../ui/MediaPicker";
import { SaveBar } from "./SaveBar";
import { SerpPreview } from "./SerpPreview";
import { useHub } from "./StoreHub";
import type { Health } from "./StoreResumen";
import type { Listing } from "./CatalogoWeb";
import { useDraft } from "./useDraft";

type Seo = { seo_title: string; seo_description: string; share_image: string };
const LABELS: Record<string, string> = { seo_title: "Título para Google", seo_description: "Descripción para Google", share_image: "Imagen para compartir" };

export default function StoreSeo() {
  const { cfg } = useHub();
  const d = useDraft<Seo>({ key: "seo:showcase", url: "/store/showcase", read: (r) => ({ seo_title: String(r.seo_title ?? ""), seo_description: String(r.seo_description ?? ""), share_image: String(r.share_image ?? "") }) });
  const [salud, setSalud] = useState<Health | null>(null);
  const [largos, setLargos] = useState<Listing | null>(null);
  useEffect(() => {
    api<Health>("/store/health").then(setSalud).catch(() => setSalud(null));
    api<Listing>("/store/catalog?falta=nombre_largo").then(setLargos).catch(() => setLargos(null));
  }, []);
  if (d.error) return <Card title="SEO"><p className="mt-lead">{d.error}</p></Card>;
  if (!d.draft) return <Loading />;
  const x = d.draft;
  const nombre = cfg?.name || "Tienda";
  const titulo = x.seo_title.trim() || `${nombre} · Tienda de seguridad electrónica y redes en Costa Rica`;
  const base = cfg?.domain ? `https://${cfg.domain.replace(/^https?:\/\//, "")}` : `${location.origin}/tienda/${cfg?.slug || ""}`;
  return (
    <>
      <Card title="Portada en Google y al compartir">
        <div className="mt-stack">
          <Field label="Título de la portada" hint={`Vacío = «${nombre} · Tienda de seguridad electrónica y redes en Costa Rica».`}><input className="input" value={x.seo_title} maxLength={90} onChange={(e) => d.set({ seo_title: e.target.value })} /></Field>
          <Field label="Descripción de la portada"><textarea className="textarea" rows={3} maxLength={300} value={x.seo_description} onChange={(e) => d.set({ seo_description: e.target.value })} /></Field>
          <Field label="Imagen para compartir" hint="La que sale en WhatsApp y redes al compartir la tienda. Vacía = el banner principal o el logo."><ImageField value={x.share_image || null} label="Imagen para compartir" onChange={(url) => d.set({ share_image: url || "" })} /></Field>
          <SerpPreview title={titulo} description={x.seo_description} url={`${base}/`} />
        </div>
      </Card>
      <Card title="Mapa del sitio">
        <p className="mt-lead">Google lee el mapa del sitio para encontrar las páginas: <a href={`${base}/sitemap.xml`} target="_blank" rel="noopener">{base}/sitemap.xml</a>{salud ? ` · ${salud.indicadores.visibles.toLocaleString("es-CR")} productos visibles` : ""}.</p>
      </Card>
      <Card title="Títulos que se cortan en Google" extra={largos && largos.total > 0 ? <Link className="btn btn--ghost btn--sm" to="/mi-tienda/catalogo?falta=nombre_largo">Ver los {largos.total}</Link> : undefined}>
        {!largos ? <Loading /> : largos.total === 0 ? <p className="mt-lead">Ningún producto visible tiene un nombre de más de 60 caracteres.</p> : (
          <ul className="mt-stack" style={{ margin: 0, paddingLeft: 18 }}>
            {largos.items.slice(0, 8).map((p) => <li key={p.id}><Link to={`/mi-tienda/catalogo/${p.id}?falta=nombre_largo`}>{p.name}</Link> <span className="meta" style={{ textTransform: "none" }}>{p.name.length} caracteres</span></li>)}
          </ul>
        )}
      </Card>
      <SaveBar drafts={[d]} labels={LABELS} />
    </>
  );
}
