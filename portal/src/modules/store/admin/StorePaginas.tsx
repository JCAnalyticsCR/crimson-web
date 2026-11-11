/* Mi Tienda -> Paginas: informativas (envios, garantias, preguntas) y legales (terminos y privacidad) en un solo
   borrador con dos fuentes (vitrina y /store), guardadas juntas con «Guardar y publicar» (O4-02). Las paginas
   adicionales con bloques son registros aparte y se guardan cada una con su boton. */
import { Card, Field, I, Icon, Loading } from "../../../ui/components";
import { PagesCard } from "../Store";
import { SaveBar } from "./SaveBar";
import { useHub } from "./StoreHub";
import { useDraft } from "./useDraft";

type Faq = { q: string; a: string };
type Info = { pages: { envios: string; garantias: string }; faq: Faq[] };
type Legal = { legal: { terms: string; privacy: string } };
const LABELS: Record<string, string> = { pages: "Envíos y garantías", faq: "Preguntas frecuentes", legal: "Términos y privacidad" };

export default function StorePaginas() {
  const { cfg } = useHub();
  const info = useDraft<Info>({
    key: "paginas:showcase",
    url: "/store/showcase",
    read: (r) => {
      const pg = (r.pages as Record<string, string>) || {};
      return { pages: { envios: pg.envios || "", garantias: pg.garantias || "" }, faq: (r.faq as Faq[]) || [] };
    },
    body: (x, changed, version) => ({ ...Object.fromEntries(changed.map((k) => [k, k === "faq" ? x.faq.filter((f) => f.q.trim() && f.a.trim()) : x[k]])), version }),
  });
  const legal = useDraft<Legal>({
    key: "paginas:store",
    url: "/store",
    read: (r) => { const l = (r.legal as Record<string, string>) || {}; return { legal: { terms: l.terms || "", privacy: l.privacy || "" } }; },
  });
  if (info.error || legal.error) return <Card title="Páginas"><p className="mt-lead">{info.error || legal.error}</p></Card>;
  if (!info.draft || !legal.draft) return <Loading />;
  const p = info.draft;
  const l = legal.draft.legal;
  const setFaq = (i: number, patch: Partial<Faq>) => info.set({ faq: p.faq.map((f, k) => (k === i ? { ...f, ...patch } : f)) });
  return (
    <>
      <Card title="Páginas informativas">
        <div className="mt-stack">
          <p className="mt-lead">Párrafos separados por una línea en blanco. Una primera línea corta (sin punto) se muestra como subtítulo. Se enlazan en el pie de la tienda.</p>
          <Field label="Envíos y devoluciones" hint="Diga el plazo de reembolso en días (por ejemplo, «el reembolso se hace en 8 días hábiles»)."><textarea className="textarea" rows={8} value={p.pages.envios} onChange={(e) => info.set({ pages: { ...p.pages, envios: e.target.value } })} /></Field>
          <Field label="Garantías"><textarea className="textarea" rows={8} value={p.pages.garantias} onChange={(e) => info.set({ pages: { ...p.pages, garantias: e.target.value } })} /></Field>
        </div>
      </Card>
      <Card title="Páginas legales">
        <div className="mt-stack">
          <Field label="Términos y condiciones"><textarea className="textarea" rows={8} value={l.terms} onChange={(e) => legal.set({ legal: { ...l, terms: e.target.value } })} /></Field>
          <Field label="Política de privacidad"><textarea className="textarea" rows={8} value={l.privacy} onChange={(e) => legal.set({ legal: { ...l, privacy: e.target.value } })} /></Field>
        </div>
      </Card>
      <Card title="Preguntas frecuentes" extra={<button className="btn btn--ghost btn--sm" onClick={() => info.set({ faq: [...p.faq, { q: "", a: "" }] })} disabled={p.faq.length >= 40}><Icon d={I.plus} />Pregunta</button>}>
        <div className="mt-stack">
          <p className="mt-lead">«¿Cómo pago?» se contesta solo con las formas de pago activas. Una pregunta sin respuesta no se publica.</p>
          {p.faq.map((f, i) => (
            <div key={i} className="mt-box">
              <Field label={`Pregunta ${i + 1}`}><input className="input" value={f.q} maxLength={200} onChange={(e) => setFaq(i, { q: e.target.value })} /></Field>
              <Field label="Respuesta"><textarea className="textarea" value={f.a} maxLength={2000} onChange={(e) => setFaq(i, { a: e.target.value })} /></Field>
              <button className="btn btn--danger btn--sm" style={{ justifySelf: "start" }} onClick={() => info.set({ faq: p.faq.filter((_, k) => k !== i) })}>Quitar</button>
            </div>
          ))}
        </div>
      </Card>
      {/* O4-08: las paginas con bloques quedan como «Páginas adicionales»; se ven en la tienda si estan publicadas y en el menu */}
      <PagesCard primary={cfg?.primary} title="Páginas adicionales" />
      <SaveBar drafts={[info, legal]} labels={LABELS} />
    </>
  );
}
