/* Mi Tienda -> SEO (O4-01): la descripcion de la portada vive hoy en Portada; O4-08 trae titulo, descripcion y vista
   previa de Google. Sin medicion: nada de Google Analytics, Ads ni pixeles de terceros (decision del dueno). */
import { Link } from "react-router-dom";
import { Card } from "../../../ui/components";

export default function StoreSeo() {
  return (
    <Card title="SEO">
      <div className="sh-stack">
        <p className="sh-lead">La descripción para Google y WhatsApp de la portada se edita hoy en Portada.</p>
        <div><Link className="btn btn--ghost btn--sm" to="/mi-tienda/portada">Ir a Portada</Link></div>
      </div>
    </Card>
  );
}
