/* Mi Tienda -> Catalogo (O4-01): datos de vitrina por producto. O4-04 lo cambia por la cuadricula unificada. */
import { Link } from "react-router-dom";
import StoreShowcaseAdmin from "../StoreShowcaseAdmin";
import { Card } from "../../../ui/components";

export default function CatalogoWeb() {
  return (
    <>
      <Card title="Dónde se edita cada cosa">
        <div className="sh-row">
          <Link className="btn btn--ghost btn--sm" to="/inventario?tab=web">Inventario web</Link>
          <Link className="btn btn--ghost btn--sm" to="/productos">Productos y servicios</Link>
        </div>
      </Card>
      <StoreShowcaseAdmin only={["Por producto"]} />
    </>
  );
}
