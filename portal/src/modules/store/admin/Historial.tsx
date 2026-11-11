/* Mi Tienda -> Historial (O4-02): los ultimos 20 guardados (quien, cuando, que) y «Restaurar» para lo que se puede
   volver atras. Nunca se restaura publicacion, dominio, tipo de tienda, moneda ni cuentas de pago; de los textos
   legales solo queda la huella (se sabe que cambiaron, no se guarda el texto). */
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, Loading, Modal } from "../../../ui/components";
import { useHub } from "./StoreHub";

type Row = { id: number; at: string; quien: string; seccion: string; seccion_label: string; campos: string[]; restaurables: string[]; restaurado_de: number | null };

const cuando = (s: string) => new Date(s).toLocaleString("es-CR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default function Historial() {
  const { toast } = useSession();
  const hub = useHub();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [ask, setAsk] = useState<Row | null>(null);
  const load = () => api<Row[]>("/store/history").then(setRows).catch((e) => { toast(e.message, "bad"); setRows([]); });
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const restore = (r: Row) => api<{ campos: string[] }>(`/store/history/${r.id}/restore`, { method: "POST" })
    .then((x) => { toast(`Restaurado: ${x.campos.join(", ")}. La tienda lo muestra en la siguiente visita.`); hub.reload(); void load(); })
    .catch((e) => toast(e.message, "bad"));
  if (!rows) return <Loading />;
  return (
    <Card title="Últimos cambios" flush>
      {rows.length === 0 ? <Empty title="Todavía no hay cambios" hint="Cada «Guardar y publicar» queda aquí." /> : (
        <table className="table table--stack">
          <thead><tr><th>Cuándo</th><th>Quién</th><th>Sección</th><th>Qué cambió</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td data-label="Cuándo">{cuando(r.at)}</td>
                <td data-label="Quién">{r.quien}</td>
                <td data-label="Sección">{r.seccion_label}{r.restaurado_de ? <span className="meta" style={{ textTransform: "none" }}> · restauración</span> : null}</td>
                <td data-label="Qué cambió" style={{ overflowWrap: "anywhere" }}>{r.campos.join(", ")}</td>
                <td>{r.restaurables.length > 0 ? <button className="btn btn--ghost btn--sm" onClick={() => setAsk(r)}>Restaurar</button> : <span className="meta" style={{ textTransform: "none" }}>No se restaura</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {ask && (
        <Modal title="¿Restaurar este cambio?" onClose={() => setAsk(null)} foot={<><button className="btn btn--ghost" onClick={() => setAsk(null)}>Cancelar</button><button className="btn btn--crimson" onClick={() => { const r = ask; setAsk(null); void restore(r); }}>Restaurar</button></>}>
          <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5 }}>Vuelve a poner cómo estaba antes del {cuando(ask.at)}: {ask.restaurables.join(", ")}. La publicación, el dominio, el tipo de tienda, la moneda y las cuentas de pago no se tocan.</p>
        </Modal>
      )}
    </Card>
  );
}
