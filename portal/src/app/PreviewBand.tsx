/* Vista previa por rol: banda fija ARRIBA de todo, tambien de las ventanas. Reunion 13/10 [08:47]: Andres no vio
   "Costear" porque estaba en vista previa y la franja oscura quedaba tapada por la ventana del levantamiento.
   Solo cambia lo que se MUESTRA (menus y botones): la API sigue respondiendo con la cuenta real, asi que los datos
   (listas, montos, conteos) siguen siendo los del administrador. Por eso la banda lo dice. */
import { useEffect } from "react";
import { createPortal } from "react-dom";
import { useSession } from "./session";
import { roleMeta } from "./roles";
import { Icon } from "../ui/components";

const EYE = "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z|M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z";

export default function PreviewBand() {
  const { viewAs, setViewAs } = useSession();
  useEffect(() => {
    const el = document.documentElement;
    if (viewAs) el.setAttribute("data-preview", viewAs); else el.removeAttribute("data-preview");
    return () => el.removeAttribute("data-preview");
  }, [viewAs]);
  if (!viewAs) return null;
  const rm = roleMeta(viewAs);
  return createPortal(
    <div className="preview-band" role="status" aria-live="polite">
      <Icon d={EYE} size={18} />
      <span className="preview-band__role" style={{ ["--tone" as string]: rm.tone }} aria-hidden>{rm.glyph}</span>
      <p className="preview-band__txt" title={`Estás viendo como ${rm.label}: solo se ocultan menús y botones. Los datos siguen siendo los de tu cuenta.`}>
        <b>Estás viendo como {rm.label}</b>
        <span className="preview-band__long"> — algunos botones y secciones están ocultos. Los datos (listas, montos y conteos) siguen siendo los de tu cuenta.</span>
        {/* celular: segunda linea propia; lo de los datos va primero (si no cabe, se corta "botones ocultos") */}
        <span className="preview-band__short">Datos de tu cuenta · botones ocultos</span>
      </p>
      <button type="button" className="preview-band__exit" onClick={() => setViewAs(null)} aria-label="Salir de la vista previa">
        Salir<span className="preview-band__long"> de la vista previa</span>
      </button>
    </div>,
    document.body,
  );
}
