/* Vista previa del resultado en Google (O4-04 y O4-08): titulo, direccion y descripcion como se ven en el buscador,
   con contadores de 60 y 155 caracteres. Solo muestra: no llama a Google ni a nadie. */
import { Counter } from "./fields";

export const TITLE_MAX = 60;
export const DESC_MAX = 155;
const corta = (s: string, max: number) => (s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s);

export function SerpPreview({ title, description, url }: { title: string; description?: string | null; url: string }) {
  const desc = (description || "").replace(/\s+/g, " ").trim();
  return (
    <div className="mt-stack">
      <div className="serp" aria-label="Vista previa en Google">
        <span className="serp__url">{url}</span>
        <span className="serp__title">{corta(title, TITLE_MAX)}</span>
        <span className="serp__desc">{desc ? corta(desc, DESC_MAX) : "Google elegirá un texto de la página: escriba una descripción."}</span>
      </div>
      <div className="mt-row" style={{ justifyContent: "space-between" }}>
        <span>Título: <Counter value={title} max={TITLE_MAX} /></span>
        <span>Descripción: <Counter value={desc} max={DESC_MAX} /></span>
      </div>
    </div>
  );
}
