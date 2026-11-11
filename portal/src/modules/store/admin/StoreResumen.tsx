/* Mi Tienda -> Resumen (O4-01): por ahora, el indice de las secciones. O4-03 le pone «Lista para vender». */
import { Link } from "react-router-dom";
import { useSession } from "../../../app/session";
import { sectionUrl, visibleSections } from "./sections";

export default function StoreResumen() {
  const { allows, me } = useSession();
  const secs = visibleSections(allows, me?.role === "admin").filter((s) => s.key !== "resumen");
  return (
    <div className="sh-index">
      {secs.map((s) => <Link key={s.key} to={sectionUrl(s)}><b>{s.label}</b><span>{s.hint}</span></Link>)}
    </div>
  );
}
