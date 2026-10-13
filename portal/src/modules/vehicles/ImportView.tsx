/* Importacion de cargas (CSV / XLSX): vista previa con validaciones; nada se guarda sin confirmar. */
import { useRef, useState } from "react";
import { uploadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";

type Row = { row: number; action: "nuevo" | "omitir" | "error"; detail: string | null; warnings: string[]; vehicle: string | null };
type Res = { commit: boolean; rows: number; summary: { nuevo: number; omitir: number; error: number; avisos: number }; columns: Record<string, string | null>; result: Row[] };
const LABEL: Record<string, string> = { nuevo: "Se importa", omitir: "Se omite", error: "Error" };
const TAG: Record<string, string> = { nuevo: "veh-tag--ok", omitir: "veh-tag--muted", error: "veh-tag--off" };
const FIELDS: Record<string, string> = { date: "Fecha", vehicle: "Vehículo", driver: "Conductor", liters: "Litros", amount: "Importe", currency: "Moneda", odometer: "Odómetro", full_tank: "Tanque lleno", usage: "Uso", project: "Proyecto", notes: "Notas" };

export default function ImportView({ onDone }: { onDone: () => void }) {
  const { toast } = useSession();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [create, setCreate] = useState(false);
  const [prev, setPrev] = useState<Res | null>(null);
  const [only, setOnly] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (f: File, commit: boolean) => {
    setBusy(true);
    try {
      const r = await uploadFile<Res>(`/vehicles/fuel/import?commit=${commit}&create_vehicles=${create}`, f);
      if (commit) { toast(`${r.summary.nuevo} cargas importadas`); setPrev(null); setFile(null); onDone(); }
      else setPrev(r);
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  const pick = (f?: File | null) => { if (!f) return; setFile(f); setOnly(""); run(f, false); };
  const rows = (prev?.result || []).filter((r) => !only || (only === "avisos" ? r.warnings.length > 0 : r.action === only));
  return (
    <div className="veh-stack">
      <Card title="Importar historial de cargas">
        <div className="veh-stack" style={{ gap: 12 }}>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Columnas reconocidas: fecha, vehículo (placa o nombre), conductor, litros, importe, moneda, odómetro, tanque lleno (sí/no), uso, proyecto y notas. Primero ves la vista previa: <b>nada se guarda hasta confirmar</b>. Se omiten duplicados (misma fecha, vehículo e importe) y se avisan odómetros que retroceden y datos faltantes.</p>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <AuthLink path="/vehicles/fuel/import/template" download="plantilla-cargas-combustible.xlsx"><Icon d={I.reports} size={14} />Descargar plantilla</AuthLink>
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={create} onChange={(e) => { setCreate(e.target.checked); if (file) setPrev(null); }} />Crear los vehículos que no existan</label>
          </div>
          <div role="button" tabIndex={0} onClick={() => input.current?.click()} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files?.[0]); }}
            style={{ border: "1.5px dashed var(--hair-2)", borderRadius: 12, padding: "26px 16px", textAlign: "center", cursor: "pointer", color: "var(--text-3)", fontSize: 14 }}>
            <input ref={input} type="file" hidden accept=".csv,.xlsx,.xlsm,text/csv" onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
            <Icon d={I.upload} size={22} /><div style={{ marginTop: 6 }}>{busy ? "Procesando…" : file ? `${file.name} · tocá para cambiar` : "Arrastrá el archivo CSV o Excel, o hacé clic"}</div>
          </div>
          {file && !prev && !busy && <button className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => run(file, false)}>Ver vista previa otra vez</button>}
        </div>
      </Card>
      {prev && (
        <Card flush title={`Vista previa · ${prev.rows} filas`} extra={
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn--ghost btn--sm" onClick={() => { setPrev(null); setFile(null); }}>Cancelar</button>
            <button className="btn btn--crimson btn--sm" disabled={busy || prev.summary.nuevo === 0} onClick={() => file && run(file, true)}>Importar {prev.summary.nuevo} cargas</button>
          </div>
        }>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "12px 18px", borderBottom: "1px solid var(--hair)" }}>
            {([["", `Todas (${prev.result.length})`], ["nuevo", `Se importan (${prev.summary.nuevo})`], ["omitir", `Omitidas (${prev.summary.omitir})`], ["error", `Errores (${prev.summary.error})`], ["avisos", `Con avisos (${prev.summary.avisos})`]] as const).map(([k, l]) => (
              <button key={k} className={`btn btn--sm ${only === k ? "btn--crimson" : "btn--ghost"}`} onClick={() => setOnly(k)}>{l}</button>
            ))}
          </div>
          <div style={{ padding: "8px 18px", fontSize: 12, color: "var(--text-3)" }}>
            Columnas: {Object.entries(prev.columns).map(([k, v]) => <span key={k} style={{ marginRight: 10 }}>{FIELDS[k] || k} {v ? "✓" : "—"}</span>)}
          </div>
          {rows.length === 0 ? <Empty hint="No hay filas con ese filtro." /> : (
            <div className="veh-scroll"><table className="table">
              <thead><tr><th className="num">Fila</th><th>Resultado</th><th>Vehículo</th><th>Detalle</th><th>Avisos</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.row}>
                  <td className="num mono">{r.row}</td>
                  <td><span className={`veh-tag ${TAG[r.action]}`}>{LABEL[r.action]}</span></td>
                  <td>{r.vehicle || "—"}</td>
                  <td className="veh-why">{r.detail}</td>
                  <td className="veh-why">{r.warnings.length ? r.warnings.map((w) => <div key={w}><span className="veh-tag veh-tag--warn">aviso</span> {w}</div>) : "—"}</td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </Card>
      )}
    </div>
  );
}
