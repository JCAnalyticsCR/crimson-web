/* Otros costos del vehiculo (opcional): mantenimiento, llantas, seguro, depreciacion. Registro simple por fecha. */
import { useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";
import { COST_KINDS, type Cost, type Lookups, n, today } from "./types";

type Form = { id?: number; vehicle_id: string; date: string; kind: string; amount: string; currency: string; description: string };

export default function CostsView({ lookups }: { lookups: Lookups | null }) {
  const { toast, allows } = useSession();
  const [rows, setRows] = useState<Cost[] | null>(null);
  const [edit, setEdit] = useState<Form | null>(null);
  const load = () => api<Cost[]>("/vehicles/costs").then(setRows);
  useEffect(() => { load(); }, []);
  const save = async () => {
    if (!edit) return;
    if (!edit.vehicle_id || !edit.amount) return toast("Vehículo e importe son obligatorios", "bad");
    const { id, ...b } = edit;
    try {
      await api(id ? `/vehicles/costs/${id}` : "/vehicles/costs", { method: id ? "PUT" : "POST", json: { ...b, vehicle_id: Number(b.vehicle_id), amount: Number(b.amount), description: b.description || null } });
      toast("Costo guardado"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <Card flush title="Otros costos del vehículo" extra={allows("vehicles.crear") ? <button className="btn btn--crimson btn--sm" onClick={() => setEdit({ vehicle_id: "", date: today(), kind: "mantenimiento", amount: "", currency: "CRC", description: "" })}><Icon d={I.plus} size={14} />Agregar</button> : undefined}>
      {rows === null ? <Loading /> : rows.length === 0 ? <Empty hint="Opcional: mantenimiento, llantas, seguro y depreciación. Se muestran aparte del combustible en el reporte." /> : (
        <div className="veh-scroll"><table className="table">
          <thead><tr><th>Fecha</th><th>Vehículo</th><th>Tipo</th><th>Detalle</th><th className="num">Monto</th><th /></tr></thead>
          <tbody>{rows.map((c) => (
            <tr key={c.id}>
              <td>{fmtDate(c.date)}</td><td>{c.vehicle}</td><td>{COST_KINDS[c.kind] || c.kind}</td><td className="veh-why">{c.description || "—"}</td>
              <td className="num money">{fmtMoney(c.amount, c.currency)}</td>
              <td className="num">{allows("vehicles.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ id: c.id, vehicle_id: String(c.vehicle_id), date: c.date, kind: c.kind, amount: String(n(c.amount) ?? ""), currency: c.currency, description: c.description || "" })}>Editar</button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {edit && (
        <Modal title={edit.id ? "Costo del vehículo" : "Nuevo costo"} onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Vehículo"><select className="select" value={edit.vehicle_id} onChange={(e) => setEdit({ ...edit, vehicle_id: e.target.value })}><option value="">Elegir…</option>{lookups?.vehicles.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>
            <Field label="Fecha"><input className="input" type="date" value={edit.date} onChange={(e) => setEdit({ ...edit, date: e.target.value })} /></Field>
            <Field label="Tipo"><select className="select" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>{Object.entries(COST_KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Monto"><input className="input input--mono" inputMode="decimal" value={edit.amount} onChange={(e) => setEdit({ ...edit, amount: e.target.value.replace(",", ".") })} /></Field>
            <Field label="Moneda"><select className="select" value={edit.currency} onChange={(e) => setEdit({ ...edit, currency: e.target.value })}><option value="CRC">Colones</option><option value="USD">Dólares</option></select></Field>
          </div>
          <Field label="Detalle"><input className="input" value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
        </Modal>
      )}
    </Card>
  );
}
