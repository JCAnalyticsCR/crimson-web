/* Piezas chicas de _comun que viven tambien FUERA de las maquetas (bienvenida, ficha de etiqueta de la capa):
   el sello de los tres estados y «Me interesa». Van aparte de componentes.tsx para que la entrada de la demo no
   cargue el marco de maquetas ni su CSS: esas piezas llegan con cada pantalla lazy. */
import { useInteres } from "./hooks";
import type { PropuestaId } from "../registro";
import "./sello.css";

export const Visto = () => <svg className="pp-visto" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7" /></svg>;

// ---------------------------------------------------------------- sello (tres estados que nunca se confunden)
export function SelloPropuesta({ estado = "propuesta", id, tamano = "normal", tono = "claro", className }: {
  estado?: "propuesta" | "existente" | "ejemplo"; id?: string; tamano?: "normal" | "chico"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const chico = tamano === "chico";
  return (
    <span className={`pp-sello${className ? ` ${className}` : ""}`} data-estado={estado} data-tamano={tamano} data-tono={tono}>
      {id && <b className="pp-sello__id">{id}</b>}
      <span className="pp-sello__t">
        {estado === "propuesta" ? (chico ? <>Propuesta<span className="pp-sr"> · no incluida aún</span></> : "Propuesta · no incluida aún")
          : estado === "existente" ? <><Visto />{chico ? <>Ya está<span className="pp-sr"> en la plataforma</span></> : "Ya está en la plataforma"}</>
            : "Datos de ejemplo"}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------- «Me interesa»
export function BotonMeInteresa({ id, variante = "boton", tono = "claro", className }: {
  id: PropuestaId; variante?: "boton" | "chip"; tono?: "claro" | "oscuro"; className?: string;
}) {
  const { tiene, alternar } = useInteres();
  const on = tiene(id);
  return (
    <button type="button" className={`pp-interes${className ? ` ${className}` : ""}`} data-variante={variante} data-tono={tono} aria-pressed={on} onClick={() => alternar(id)}>
      {on ? <>Te interesa <span aria-hidden="true">✓</span><span className="pp-sr"> (marcada)</span></> : <>Me interesa<span className="pp-sr">: {id}</span></>}
    </button>
  );
}
