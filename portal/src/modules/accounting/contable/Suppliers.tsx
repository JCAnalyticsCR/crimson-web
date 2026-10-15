/* Proveedores (lo que estaba en la vieja pantalla de Contabilidad), con cuantos comprobantes y gastos tiene cada uno. */
import { useEffect, useState } from "react";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../../ui/components";
import { TrashButton } from "../../../ui/TrashButton";
import type { Exp } from "./Expenses";
import type { Reception } from "../hacienda";

type Sup = { id: number; name: string; tax_id: string | null; email: string | null; phone: string | null };
const norm = (s: string | null | undefined) => (s || "").replace(/[^0-9A-Za-z]/g, "").toUpperCase();

export default function Suppliers() {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Sup[] | null>(null);
  const [exp, setExp] = useState<Exp[]>([]);
  const [docs, setDocs] = useState<Reception[]>([]);
  const [sup, setSup] = useState<(Omit<Sup, "id"> & { id?: number }) | null>(null);
  const load = () => {
    api<Sup[]>("/suppliers").then(setRows).catch(() => setRows([]));
    api<Exp[]>("/expenses?limit=500").then(setExp).catch(() => setExp([]));
    api<{ items: Reception[] }>("/reception/documents?limit=200").then((r) => setDocs(r.items)).catch(() => setDocs([]));
  };
  useEffect(load, []);
  const saveSup = async () => {
    if (!sup) return;
    const { id, ...b } = sup;
    try { await api(id ? `/suppliers/${id}` : "/suppliers", { method: id ? "PUT" : "POST", json: { ...b, tax_id: b.tax_id || null, email: b.email || null, phone: b.phone || null } }); toast("Proveedor guardado"); setSup(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <>
      <Card title="Proveedores" flush extra={allows("catalog.crear") && <button className="btn btn--ghost btn--sm" onClick={() => setSup({ name: "", tax_id: "", email: "", phone: "" })}><Icon d={I.plus} />Proveedor</button>}>
        {rows === null ? <Loading /> : rows.length === 0 ? <Empty title="Sin proveedores" hint="Se crean al aceptar un comprobante electrónico o aquí; sirven para el D-270 y el libro de compras." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table"><thead><tr><th>Nombre</th><th>Cédula</th><th>Correo</th><th>Teléfono</th><th className="num">Comprobantes</th><th className="num">Gastos</th><th /></tr></thead>
              <tbody>{rows.map((x) => <tr key={x.id}>
                <td style={{ fontWeight: 600 }}>{x.name}</td><td className="mono muted">{x.tax_id || "—"}</td><td className="muted">{x.email || "—"}</td><td className="muted">{x.phone || "—"}</td>
                <td className="num mono">{x.tax_id ? docs.filter((d) => norm(d.issuer_id) === norm(x.tax_id)).length : 0}</td>
                <td className="num mono">{exp.filter((e) => e.supplier_id === x.id).length}</td>
                <td className="num row-actions"><span className="row-actions__in">{allows("catalog.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setSup({ id: x.id, name: x.name, tax_id: x.tax_id || "", email: x.email || "", phone: x.phone || "" })}>Editar</button>}<TrashButton kind="supplier" id={x.id} number={x.name} onDone={load} /></span></td>
              </tr>)}</tbody></table>
          </div>
        )}
      </Card>
      {sup && (
        <Modal title={sup.id ? "Proveedor" : "Nuevo proveedor"} onClose={() => setSup(null)} foot={<><button className="btn btn--ghost" onClick={() => setSup(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveSup}>Guardar</button></>}>
          <Field label="Nombre o razón social"><input className="input" autoFocus value={sup.name} onChange={(e) => setSup({ ...sup, name: e.target.value })} /></Field>
          <div className="grid-3">
            <Field label="Cédula"><input className="input input--mono" style={{ textAlign: "left" }} value={sup.tax_id || ""} onChange={(e) => setSup({ ...sup, tax_id: e.target.value })} /></Field>
            <Field label="Correo"><input className="input" value={sup.email || ""} onChange={(e) => setSup({ ...sup, email: e.target.value })} /></Field>
            <Field label="Teléfono"><input className="input" value={sup.phone || ""} onChange={(e) => setSup({ ...sup, phone: e.target.value })} /></Field>
          </div>
        </Modal>
      )}
    </>
  );
}
