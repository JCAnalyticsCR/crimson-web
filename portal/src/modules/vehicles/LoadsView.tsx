/* Cargas de combustible: lista con filtros (las excluidas se ven con su marca) y formulario de carga. */
import { useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Loading, Modal } from "../../ui/components";
import { Dropzone } from "../../ui/MediaPicker";
import { type Load, type Lookups, fmtNum, n, today } from "./types";

type Form = {
  id?: number; vehicle_id: string; date: string; driver: string; liters: string; amount: string; currency: string; odometer: string; full_tank: boolean;
  receipt_media_id: number | null; receipt_name: string | null; usage: string; project_id: string; excluded: boolean; exclusion_reason: string; notes: string;
};
const blank = (): Form => ({ vehicle_id: "", date: today(), driver: "", liters: "", amount: "", currency: "CRC", odometer: "", full_tank: true, receipt_media_id: null, receipt_name: null, usage: "", project_id: "", excluded: false, exclusion_reason: "", notes: "" });

export function LoadForm({ lookups, initial, onClose, onSaved }: { lookups: Lookups | null; initial: Form; onClose: () => void; onSaved: () => void }) {
  const { toast } = useSession();
  const [f, setF] = useState<Form>(initial);
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<Form>) => setF((x) => ({ ...x, ...p }));
  const save = async () => {
    if (!f.vehicle_id) return toast("Elegí el vehículo", "bad");
    if (!f.amount) return toast("Indicá el importe", "bad");
    if (f.excluded && !f.exclusion_reason.trim()) return toast("Indicá por qué se excluye", "bad");
    setBusy(true);
    const body = {
      vehicle_id: Number(f.vehicle_id), date: f.date, driver: f.driver || null, liters: f.liters ? Number(f.liters) : null, amount: Number(f.amount), currency: f.currency,
      odometer: f.odometer ? Number(f.odometer) : null, full_tank: f.full_tank, receipt_media_id: f.receipt_media_id, usage: f.usage || null,
      project_id: f.project_id ? Number(f.project_id) : null, excluded: f.excluded, exclusion_reason: f.excluded ? f.exclusion_reason : null, notes: f.notes || null,
    };
    try { await api(f.id ? `/vehicles/fuel/${f.id}` : "/vehicles/fuel", { method: f.id ? "PUT" : "POST", json: body }); toast(f.id ? "Carga actualizada" : "Carga registrada"); onSaved(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const ppl = f.liters && f.amount ? Number(f.amount) / Number(f.liters) : null;
  return (
    <Modal wide title={f.id ? "Carga de combustible" : "Registrar carga"} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={busy} onClick={save}>{busy ? "Guardando…" : "Guardar"}</button></>}>
      <div className="grid-3">
        <Field label="Vehículo">
          <select className="select" value={f.vehicle_id} onChange={(e) => { const v = lookups?.vehicles.find((x) => String(x.id) === e.target.value); set({ vehicle_id: e.target.value, driver: f.driver || v?.driver || "" }); }}>
            <option value="">Elegir…</option>
            {lookups?.vehicles.filter((v) => v.active || String(v.id) === f.vehicle_id).map((v) => <option key={v.id} value={v.id}>{v.name}{v.plate ? ` · ${v.plate}` : ""}</option>)}
          </select>
        </Field>
        <Field label="Fecha"><input className="input" type="date" value={f.date} max={today()} onChange={(e) => set({ date: e.target.value })} /></Field>
        <Field label="Conductor"><input className="input" value={f.driver} onChange={(e) => set({ driver: e.target.value })} /></Field>
        <Field label="Litros" hint={ppl ? `${fmtMoney(ppl, f.currency)} por litro` : undefined}><input className="input input--mono" inputMode="decimal" value={f.liters} onChange={(e) => set({ liters: e.target.value.replace(",", ".") })} /></Field>
        <Field label="Importe"><input className="input input--mono" inputMode="decimal" value={f.amount} onChange={(e) => set({ amount: e.target.value.replace(",", ".") })} /></Field>
        <Field label="Moneda"><select className="select" value={f.currency} onChange={(e) => set({ currency: e.target.value })}><option value="CRC">Colones</option><option value="USD">Dólares</option></select></Field>
        <Field label="Odómetro (km)"><input className="input input--mono" inputMode="numeric" value={f.odometer} onChange={(e) => set({ odometer: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field label="Tanque" hint="km/L solo se mide entre dos tanques llenos">
          <select className="select" value={f.full_tank ? "1" : "0"} onChange={(e) => set({ full_tank: e.target.value === "1" })}><option value="1">Lleno</option><option value="0">Parcial</option></select>
        </Field>
        <Field label="Proyecto (opcional)">
          <select className="select" value={f.project_id} onChange={(e) => set({ project_id: e.target.value })}><option value="">Sin proyecto</option>{lookups?.projects.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
        </Field>
      </div>
      <Field label="Uso"><input className="input" placeholder="Visita a cliente, entrega de materiales…" value={f.usage} onChange={(e) => set({ usage: e.target.value })} /></Field>
      <Field label="Comprobante">
        {f.receipt_media_id ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}><span className="veh-tag veh-tag--ok">adjunto</span><span className="muted" style={{ fontSize: 13 }}>{f.receipt_name || "comprobante"}</span><button className="btn btn--ghost btn--sm" onClick={() => set({ receipt_media_id: null, receipt_name: null })}>Quitar</button></div>
        ) : <Dropzone compact accept="image/*,application/pdf" label="Foto o PDF del comprobante" onDone={(m) => set({ receipt_media_id: m.id, receipt_name: m.filename })} />}
      </Field>
      <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={f.excluded} onChange={(e) => set({ excluded: e.target.checked })} />Excluir esta carga del análisis (sigue en la lista)</label>
      {f.excluded && <Field label="Motivo de la exclusión"><input className="input" value={f.exclusion_reason} placeholder="Carga de un vehículo personal, error de digitación…" onChange={(e) => set({ exclusion_reason: e.target.value })} /></Field>}
      <Field label="Notas"><textarea className="textarea" rows={2} value={f.notes} onChange={(e) => set({ notes: e.target.value })} /></Field>
    </Modal>
  );
}

export const formFrom = (x: Load): Form => ({
  id: x.id, vehicle_id: String(x.vehicle_id), date: x.date, driver: x.driver || "", liters: x.liters != null ? String(n(x.liters)) : "", amount: String(n(x.amount) ?? ""), currency: x.currency,
  odometer: x.odometer != null ? String(x.odometer) : "", full_tank: x.full_tank, receipt_media_id: x.receipt_media_id, receipt_name: x.receipt_name, usage: x.usage || "",
  project_id: x.project_id ? String(x.project_id) : "", excluded: x.excluded, exclusion_reason: x.exclusion_reason || "", notes: x.notes || "",
});
export const blankLoad = blank;

export default function LoadsView({ lookups, refresh, onEdit }: { lookups: Lookups | null; refresh: number; onEdit: (f: Form) => void }) {
  const { allows } = useSession();
  const [flt, setFlt] = useState({ from: "", to: "", vehicle: "", driver: "", status: "todas" });
  const [rows, setRows] = useState<Load[] | null>(null);
  const qs = new URLSearchParams(Object.entries({ from: flt.from, to: flt.to, vehicle_id: flt.vehicle, driver: flt.driver, status: flt.status }).filter(([, v]) => v)).toString();
  useEffect(() => { setRows(null); const t = setTimeout(() => api<Load[]>(`/vehicles/fuel?${qs}`).then(setRows), 200); return () => clearTimeout(t); }, [qs, refresh]);
  const total = (rows || []).filter((x) => !x.excluded_effective && x.currency === "CRC").reduce((s, x) => s + (n(x.amount) || 0), 0);
  return (
    <div className="veh-stack">
      <div className="veh-filters">
        <div className="field"><label>Desde</label><input className="input" type="date" value={flt.from} onChange={(e) => setFlt({ ...flt, from: e.target.value })} /></div>
        <div className="field"><label>Hasta</label><input className="input" type="date" value={flt.to} onChange={(e) => setFlt({ ...flt, to: e.target.value })} /></div>
        <div className="field"><label>Vehículo</label><select className="select" value={flt.vehicle} onChange={(e) => setFlt({ ...flt, vehicle: e.target.value })}><option value="">Todos</option>{lookups?.vehicles.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></div>
        <div className="field"><label>Conductor</label><input className="input" value={flt.driver} onChange={(e) => setFlt({ ...flt, driver: e.target.value })} /></div>
        <div className="field"><label>Estado</label><select className="select" value={flt.status} onChange={(e) => setFlt({ ...flt, status: e.target.value })}><option value="todas">Todas</option><option value="incluidas">En análisis</option><option value="excluidas">Excluidas</option></select></div>
      </div>
      <Card flush title={rows ? `${rows.length} cargas` : "Cargas"} extra={rows && rows.length > 0 ? <span className="meta">en análisis (colones): <b className="money">{fmtMoney(total)}</b></span> : undefined}>
        {rows === null ? <Loading /> : rows.length === 0 ? <Empty hint="Registrá una carga o importá el historial desde Excel o CSV." /> : (
          <div className="veh-scroll"><table className="table">
            <thead><tr><th>Fecha</th><th>Vehículo · conductor</th><th className="num">Litros</th><th className="num">Importe</th><th className="num">₡ / L</th><th className="num">Odómetro · tanque</th><th>Uso</th><th>Análisis</th><th /></tr></thead>
            <tbody>{rows.map((x) => (
              <tr key={x.id} className={x.excluded_effective ? "veh-row--off" : ""}>
                <td className="veh-nowrap">{fmtDate(x.date)}</td>
                <td className="veh-nowrap"><b>{x.vehicle}</b>{x.plate && <span className="meta"> · {x.plate}</span>}<div className="muted" style={{ fontSize: 12 }}>{x.driver || "sin conductor"}</div></td>
                <td className="num mono">{x.liters != null ? fmtNum(x.liters, 2) : <span className="veh-tag veh-tag--warn">sin litros</span>}</td>
                <td className="num money">{fmtMoney(x.amount, x.currency)}</td>
                <td className="num money">{x.price_per_liter != null ? fmtMoney(x.price_per_liter, x.currency) : "—"}</td>
                <td className="num mono veh-nowrap">{x.odometer != null ? fmtNum(x.odometer) : "—"}<div className="meta">{x.full_tank ? "lleno" : "parcial"}</div></td>
                <td className="veh-why">{x.usage || "—"}{x.project && <div>{x.project}</div>}{x.receipt_url && <div><a href={x.receipt_url} target="_blank" rel="noreferrer">comprobante</a></div>}{x.source === "importacion" && <div className="meta">importada</div>}</td>
                <td className="veh-why">{x.excluded_effective ? <><span className="veh-tag veh-tag--off">Excluida</span><div>{x.exclusion_effective}</div></> : <span className="veh-tag veh-tag--ok">incluida</span>}</td>
                <td className="num">{allows("vehicles.editar") && <button className="btn btn--ghost btn--sm" onClick={() => onEdit(formFrom(x))}>Editar</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
