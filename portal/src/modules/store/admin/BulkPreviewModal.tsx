/* Edicion en lote (O4-05): vista previa IDENTICA a lo que se aplica (el API la arma con la misma funcion), los que se
   saltan con su motivo, confirmacion explicita y, despues, «Deshacer» de toda la operacion. Los precios se escriben
   finales con IVA; la vista previa muestra el neto que se guarda. */
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Loading, Modal } from "../../../ui/components";

export type BulkReq = { accion: string; valor?: string | number | null; ids?: number[]; filtro?: Record<string, string> };
type Row = { id: number; code: string; name: string; antes: string; despues: string; nota?: string | null };
type Preview = { titulo: string; filas: Row[]; saltados: { id: number; name: string; motivo: string }[]; n: number; mostrando: number };
type Applied = { op: number; n: number; saltados: number; retenidos: number };

export function BulkPreviewModal({ req, onClose, onDone }: { req: BulkReq; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [pv, setPv] = useState<Preview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<Preview>("/store/catalog/bulk", { method: "POST", json: { ...req, confirm: false } }).then(setPv).catch((e) => setErr(e.message)); }, [req]);
  const aplicar = async () => {
    setBusy(true);
    try {
      const r = await api<Applied>("/store/catalog/bulk", { method: "POST", json: { ...req, confirm: true } });
      onDone();
      onClose();
      toast(`Listo: ${r.n} ${r.n === 1 ? "producto cambiado" : "productos cambiados"}${r.retenidos ? `; ${r.retenidos} quedan retenidos por margen` : ""}.`, "ok", {
        label: "Deshacer",
        run: () => { api<{ n: number; saltados: number }>(`/store/catalog/bulk/${r.op}/undo`, { method: "POST" }).then((u) => { onDone(); toast(`Deshecho: ${u.n} ${u.n === 1 ? "producto vuelve" : "productos vuelven"} a como estaban${u.saltados ? ` (${u.saltados} ya habían cambiado y no se tocaron)` : ""}.`); }).catch((e) => toast(e.message, "bad")); },
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={pv ? pv.titulo : "Vista previa"} onClose={onClose} foot={<>
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      <button className="btn btn--crimson" disabled={!pv || !pv.n || busy} onClick={aplicar}>{busy ? "Aplicando…" : pv ? `Aplicar a ${pv.n} ${pv.n === 1 ? "producto" : "productos"}` : "Aplicar"}</button>
    </>}>
      {err ? <p role="alert" className="mt-warn">{err}</p> : !pv ? <Loading /> : (
        <div className="mt-stack">
          <p className="mt-lead">{pv.n ? `Así quedan ${pv.n === 1 ? "el producto" : `los ${pv.n} productos`}. Nada cambia hasta que confirme; después puede deshacerlo.` : "Ningún producto cambia con esta operación."}{pv.mostrando < pv.n ? ` Se muestran los primeros ${pv.mostrando}.` : ""}</p>
          {pv.filas.length > 0 && (
            <table className="table table--stack" aria-label="Antes y después">
              <thead><tr><th>Producto</th><th>Antes</th><th>Después</th></tr></thead>
              <tbody>
                {pv.filas.map((f) => (
                  <tr key={f.id}>
                    <td data-label="Producto">{f.name}<div className="meta" style={{ textTransform: "none" }}>{f.code}</div></td>
                    <td data-label="Antes">{f.antes}</td>
                    <td data-label="Después">{f.despues}{f.nota && <div className="meta" style={{ textTransform: "none", color: "var(--warn)" }}>{f.nota}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {pv.saltados.length > 0 && (
            <div className="mt-box">
              <b style={{ fontSize: 13 }}>Se saltan {pv.saltados.length}</b>
              <ul className="meta" style={{ margin: 0, paddingLeft: 18, textTransform: "none", display: "grid", gap: 2 }}>
                {pv.saltados.slice(0, 50).map((s) => <li key={s.id}>{s.name}: {s.motivo}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
