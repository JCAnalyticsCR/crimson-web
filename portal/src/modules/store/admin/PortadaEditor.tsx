/* Mi Tienda -> Portada (O4-01): el editor de la vitrina, pestana «Portada» (titular, banners y sellos). */
import StoreShowcaseAdmin from "../StoreShowcaseAdmin";

export default function PortadaEditor() {
  return <StoreShowcaseAdmin only={["Portada"]} />;
}
