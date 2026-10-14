/* En vista previa, una accion que tu cuenta SI tiene pero el rol previsualizado no: en vez de desaparecer sin
   explicacion ("antes me salia Costear y ahora no", 13/10 [08:47]) queda un hueco rotulado que saca de la vista previa.
   Nunca ejecuta la accion: solo sale de la vista previa. */
import { useSession } from "../app/session";
import { roleMeta } from "../app/roles";

export function PreviewHidden({ needs, label }: { needs: string[]; label: string }) {
  const { viewAs, allows, realAllows, setViewAs, toast } = useSession();
  if (!viewAs || needs.every((n) => allows(n)) || !needs.every((n) => realAllows(n))) return null;
  return (
    <button type="button" className="preview-hidden" title={`${roleMeta(viewAs).label} no ve «${label}». Tocá para salir de la vista previa.`}
      onClick={() => { setViewAs(null); toast("Saliste de la vista previa: ya ves todo con tu rol"); }}>
      {label} · oculto en vista previa
    </button>
  );
}
