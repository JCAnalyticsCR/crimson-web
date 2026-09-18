/* Protege una ruta por permiso. Sin permiso muestra por que (en vez de una pantalla vacia o un error 403). */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useSession } from "./session";
import { roleMeta } from "./roles";
import { I, Icon } from "../ui/components";

export default function Guard({ need, children }: { need?: string; children: ReactNode }) {
  const { allows, role, viewAs } = useSession();
  if (allows(need)) return <>{children}</>;
  const rm = roleMeta(role);
  return (
    <div className="noaccess">
      <div className="noaccess__lock" style={{ ["--tone" as string]: rm.tone }}><Icon d="M6 11V8a6 6 0 0 1 12 0v3|M5 11h14v10H5z|M12 15v3" size={28} /></div>
      <div className="meta">Área restringida</div>
      <h1 className="h1" style={{ margin: "6px 0" }}>Esta sección no está en tu rol</h1>
      <p className="muted" style={{ maxWidth: "48ch", margin: "0 auto" }}>
        {viewAs ? `En la vista previa como ${rm.label} esta pantalla no aparece.` : `Tu rol es ${rm.label}. Si necesitás acceso, pedile a un administrador que lo ajuste en Ajustes → Usuarios.`}
      </p>
      <Link className="btn btn--crimson" to="/" style={{ marginTop: 18 }}><Icon d={I.home} size={15} />Volver al inicio</Link>
    </div>
  );
}
