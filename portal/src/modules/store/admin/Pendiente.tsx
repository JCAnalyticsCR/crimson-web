/* Lugar reservado en el menu de Mi Tienda para lo que trae otra ola. «Contenido por aprobar» (Ola 3, O3-11) reemplaza
   este elemento en la ruta /mi-tienda/contenido-por-aprobar de StoreHub.tsx. */
import { Link } from "react-router-dom";
import { Card } from "../../../ui/components";

const TEXTOS: Record<string, { title: string; text: string; link?: [string, string] }> = {
  contenido: {
    title: "Contenido por aprobar",
    text: "Aquí aparecerán los nombres comerciales, las fichas técnicas y las descripciones que el sistema proponga, para que usted los apruebe por lote antes de que se vean en la tienda. Todavía no hay nada por aprobar.",
    link: ["/mi-tienda/catalogo", "Ir al catálogo"],
  },
};

export default function Pendiente({ seccion }: { seccion: string }) {
  const t = TEXTOS[seccion] || { title: "Próximamente", text: "Esta sección todavía no está lista." };
  return (
    <Card title={t.title}>
      <div className="sh-stack">
        <p className="sh-lead">{t.text}</p>
        {t.link && <div><Link className="btn btn--ghost btn--sm" to={t.link[0]}>{t.link[1]}</Link></div>}
      </div>
    </Card>
  );
}
