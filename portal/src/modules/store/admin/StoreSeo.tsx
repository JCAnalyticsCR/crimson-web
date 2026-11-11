/* Mi Tienda -> SEO: la descripcion de la portada para Google y WhatsApp, con el guardado unico (O4-02). O4-08 suma
   el titulo, la imagen para compartir y la vista previa. Sin medicion: nada de Google Analytics, Ads ni pixeles de
   terceros (decision del dueno). */
import { Card, Field, Loading } from "../../../ui/components";
import { SaveBar } from "./SaveBar";
import { Counter } from "./fields";
import { useDraft } from "./useDraft";

type Seo = { seo_description: string };

export default function StoreSeo() {
  const d = useDraft<Seo>({ key: "seo:showcase", url: "/store/showcase", read: (r) => ({ seo_description: String(r.seo_description ?? "") }) });
  if (d.error) return <Card title="SEO"><p className="mt-lead">{d.error}</p></Card>;
  if (!d.draft) return <Loading />;
  return (
    <>
      <Card title="Portada en Google y al compartir">
        <Field label="Descripción de la portada"><textarea className="textarea" rows={3} maxLength={300} value={d.draft.seo_description} onChange={(e) => d.set({ seo_description: e.target.value })} /></Field>
        <Counter value={d.draft.seo_description} max={155} />
      </Card>
      <SaveBar drafts={[d]} labels={{ seo_description: "Descripción para Google" }} />
    </>
  );
}
