/* Mi Tienda -> Paginas (O4-01): las paginas propias de la vitrina y, aparte, las paginas adicionales con bloques. */
import StoreShowcaseAdmin from "../StoreShowcaseAdmin";
import { PagesCard } from "../Store";
import { useHub } from "./StoreHub";

export default function StorePaginas() {
  const { cfg } = useHub();
  return (
    <>
      <StoreShowcaseAdmin only={["Páginas"]} />
      <PagesCard primary={cfg?.primary} title="Páginas adicionales" />
    </>
  );
}
