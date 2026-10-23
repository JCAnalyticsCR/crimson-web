/* Flota: vehiculos con propietario, conductor habitual, estado y exclusion del analisis (con motivo). */
import { useState } from "react";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Loading, Modal } from "../../ui/components";
import { KINDS, type Vehicle, fmtNum } from "./types";

type Form = { id?: number; name: string; plate: string; kind: string; owner: "empresa" | "personal"; driver: string; active: boolean; excluded_from_analysis: boolean; exclusion_reason: string; notes: string };
export const blankVehicle = (): Form => ({ name: "", plate: "", kind: "", owner: "empresa", driver: "", active: true, excluded_from_analysis: false, exclusion_reason: "", notes: "" });

export function VehicleForm({ initial, onClose, onSaved }: { initial: Form; onClose: () => void; onSaved: () => void }) {
  const { toast } = useSession();
  const [f, setF] = useState(initial);
  const set = (p: Partial<Form>) => setF((x) => ({ ...x, ...p }));
  const save = async () => {
    if (!f.name.trim()) return toast("Ponga un nombre o alias", "bad");
    if (f.excluded_from_analysis && !f.exclusion_reason.trim()) return toast("Indique por qué se excluye", "bad");
    const { id, ...b } = f;
    try {
      await api(id ? `/vehicles/${id}` : "/vehicles", { method: id ? "PUT" : "POST", json: { ...b, plate: b.plate || null, kind: b.kind || null, driver: b.driver || null, notes: b.notes || null, exclusion_reason: b.excluded_from_analysis ? b.exclusion_reason : null } });
      toast("Vehículo guardado"); onSaved();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  return (
    <Modal title={f.id ? "Vehículo" : "Nuevo vehículo"} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
      <div className="grid-2">
        <Field label="Nombre o alias"><input className="input" value={f.name} placeholder="Pickup blanco" onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Placa"><input className="input input--mono" style={{ textAlign: "left", textTransform: "uppercase" }} value={f.plate} onChange={(e) => set({ plate: e.target.value.toUpperCase() })} /></Field>
        <Field label="Tipo"><select className="select" value={f.kind} onChange={(e) => set({ kind: e.target.value })}><option value="">—</option>{Object.entries(KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        <Field label="Propietario"><select className="select" value={f.owner} onChange={(e) => set({ owner: e.target.value as Form["owner"] })}><option value="empresa">Empresa</option><option value="personal">Personal</option></select></Field>
        <Field label="Conductor habitual"><input className="input" value={f.driver} onChange={(e) => set({ driver: e.target.value })} /></Field>
        <Field label="Estado"><select className="select" value={f.active ? "1" : "0"} onChange={(e) => set({ active: e.target.value === "1" })}><option value="1">Activo</option><option value="0">Inactivo</option></select></Field>
      </div>
      <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={f.excluded_from_analysis} onChange={(e) => set({ excluded_from_analysis: e.target.checked })} />Excluir este vehículo del análisis (sus cargas siguen en la lista)</label>
      {f.excluded_from_analysis && <Field label="Motivo" hint="Ej.: vehículo personal que cargaba con la tarjeta de la empresa"><input className="input" value={f.exclusion_reason} onChange={(e) => set({ exclusion_reason: e.target.value })} /></Field>}
      <Field label="Notas"><textarea className="textarea" rows={2} value={f.notes} onChange={(e) => set({ notes: e.target.value })} /></Field>
    </Modal>
  );
}

export default function FleetView({ vehicles, onEdit }: { vehicles: Vehicle[] | null; onEdit: (f: Form) => void }) {
  const { allows } = useSession();
  return (
    <Card flush>
      {vehicles === null ? <Loading /> : vehicles.length === 0 ? <Empty title="Sin vehículos" hint="Agregá los vehículos de la empresa (y los personales que cargan con la tarjeta, para poder excluirlos)." /> : (
        <div className="veh-scroll"><table className="table">
          <thead><tr><th>Vehículo</th><th>Tipo</th><th>Propietario</th><th>Conductor</th><th className="num">Cargas</th><th className="num">Último odómetro</th><th>Estado</th><th>Análisis</th><th /></tr></thead>
          <tbody>{vehicles.map((v) => (
            <tr key={v.id} className={v.excluded_from_analysis || !v.active ? "veh-row--off" : ""}>
              <td className="veh-nowrap"><b>{v.name}</b>{v.plate && <div className="meta">{v.plate}</div>}</td>
              <td>{v.kind ? KINDS[v.kind] || v.kind : "—"}</td>
              <td>{v.owner === "personal" ? "Personal" : "Empresa"}</td>
              <td>{v.driver || "—"}</td>
              <td className="num mono">{v.loads}{v.last_date && <div className="meta">{fmtDate(v.last_date)}</div>}</td>
              <td className="num mono">{v.last_odometer != null ? fmtNum(v.last_odometer) : "—"}</td>
              <td>{v.active ? <span className="veh-tag veh-tag--ok">activo</span> : <span className="veh-tag veh-tag--muted">inactivo</span>}</td>
              <td className="veh-why">{v.excluded_from_analysis ? <><span className="veh-tag veh-tag--off">Excluido</span><div>{v.exclusion_reason}</div></> : <span className="veh-tag veh-tag--ok">incluido</span>}</td>
              <td className="num">{allows("vehicles.editar") && <button className="btn btn--ghost btn--sm" onClick={() => onEdit({ id: v.id, name: v.name, plate: v.plate || "", kind: v.kind || "", owner: v.owner, driver: v.driver || "", active: v.active, excluded_from_analysis: v.excluded_from_analysis, exclusion_reason: v.exclusion_reason || "", notes: v.notes || "" })}>Editar</button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Card>
  );
}
