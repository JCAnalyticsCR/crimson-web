/* Mi Tienda -> Categorias (O4-01): lugar propio; O4-07 trae el editor (orden, ocultar, imagen y texto). */
import { Link } from "react-router-dom";
import { Card } from "../../../ui/components";

export default function CategoriasEditor() {
  return (
    <Card title="Categorías">
      <div className="mt-stack">
        <p className="mt-lead">Las categorías se crean hoy desde Productos y servicios.</p>
        <div><Link className="btn btn--ghost btn--sm" to="/productos">Ir a Productos y servicios</Link></div>
      </div>
    </Card>
  );
}
