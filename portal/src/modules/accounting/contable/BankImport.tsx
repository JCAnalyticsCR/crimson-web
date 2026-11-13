/* Importar estado de cuenta: elegir el archivo (CSV o .xlsx del BAC, BCR, BN...), revisar las columnas que se
   detectaron (se pueden cambiar), ver la vista previa y recien ahi guardar. Lo repetido y lo anterior a la fecha de
   corte se omite; lo seguro se concilia solo al guardar. */
import { useRef, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Field, Modal } from "../../../ui/components";
import { errMsg, num, type BookAccount, type Mapping, type Preview } from "./bankTypes";

const FIELDS: [keyof Mapping, string, string?][] = [
  ["date", "Fecha"],
  ["description", "Descripción / concepto"],
  ["reference", "Referencia / documento"],
  ["amount", "Monto con signo", "Una sola columna: + entra, − sale."],
  ["debit", "Débito (sale)"],
  ["credit", "Crédito (entra)"],
  ["balance", "Saldo", "Opcional: sirve para revisar que no falte nada."],
  ["type", "Tipo (D/C)", "Opcional: si el monto viene sin signo."],
];

export default function BankImport({ account, onClose, onDone }: { account: BookAccount; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [pv, setPv] = useState<Preview | null>(null);
  const [map, setMap] = useState<Mapping | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const cur = account.currency;

  const send = async <T,>(path: string, f: File, m: Mapping | null) => {
    const fd = new FormData();
    fd.append("file", f);
    if (m) fd.append("mapping", JSON.stringify(m));
    return api<T>(path, { method: "POST", body: fd });
  };
  const preview = async (f: File, m: Mapping | null) => {
    setBusy(true);
    try { const r = await send<Preview>(`/bank-books/accounts/${account.id}/statements/preview`, f, m); setPv(r); setMap(r.mapping); }
    catch (e) { setPv(null); toast(errMsg(e), "bad"); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!file || !map) return;
    setBusy(true);
    try {
      const r = await send<{ added: number; duplicates: number; before_cutoff: number; matched: number }>(`/bank-books/accounts/${account.id}/statements`, file, map);
      toast(`Guardados ${r.added} movimientos · ${r.matched} conciliados solos${r.duplicates ? ` · ${r.duplicates} repetidos omitidos` : ""}`);
      onDone();
    } catch (e) { toast(errMsg(e), "bad"); }
    finally { setBusy(false); }
  };
  const setCol = (k: keyof Mapping, v: string) => {
    if (!map) return;
    const next = { ...map, [k]: v === "" ? null : Number(v) } as Mapping;
    if (k === "amount" && v !== "") { next.debit = null; next.credit = null; }
    if ((k === "debit" || k === "credit") && v !== "") next.amount = null;
    setMap(next);
  };
  const dirty = !!pv && !!map && JSON.stringify(map) !== JSON.stringify(pv.mapping);
  const canSave = !!pv && !dirty && pv.errors.length === 0 && pv.new > 0 && !busy;

  return (
    <Modal wide title={`Importar estado de cuenta · ${account.name}`} onClose={onClose} foot={<>
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      {dirty && file && <button className="btn btn--soft" disabled={busy} onClick={() => preview(file, map)}>Actualizar vista previa</button>}
      <button className="btn btn--crimson" disabled={!canSave} onClick={save}>{busy ? <span className="spinner" /> : pv ? `Guardar ${pv.new} movimientos` : "Guardar"}</button>
    </>}>
      <input ref={input} type="file" accept=".csv,.txt,.xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); setPv(null); preview(f, null); } e.target.value = ""; }} />
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn btn--soft btn--sm" onClick={() => input.current?.click()} disabled={busy}>{file ? "Cambiar archivo" : "Elegir archivo"}</button>
        <span className="muted" style={{ fontSize: 13, minWidth: 0, overflowWrap: "anywhere" }}>{file ? file.name : "CSV o Excel (.xlsx), máximo 5 MB y 5000 movimientos. Si el banco da .xls, abrilo y guardalo como .xlsx."}</span>
        {busy && <span className="spinner" />}
      </div>

      {pv && map && <>
        <h4 className="h3" style={{ margin: "16px 0 6px" }}>Columnas</h4>
        <p className="muted" style={{ fontSize: 12, margin: "0 0 8px" }}>Revisá que cada dato salga de la columna correcta. Usá monto con signo <b>o</b> débito y crédito.</p>
        <div className="grid-2">
          <Field label="Fila de encabezados">
            <select className="select" value={map.header_row} onChange={(e) => setMap({ ...map, header_row: Number(e.target.value) })}>
              {pv.header_rows.map((r, i) => <option key={i} value={i}>Fila {i + 1}: {r.filter(Boolean).slice(0, 4).join(" · ").slice(0, 60) || "(vacía)"}</option>)}
            </select>
          </Field>
          <Field label="Formato de fecha">
            <select className="select" value={map.date_order} onChange={(e) => setMap({ ...map, date_order: e.target.value as Mapping["date_order"] })}>
              <option value="dmy">dd/mm/aaaa</option><option value="mdy">mm/dd/aaaa</option><option value="ymd">aaaa-mm-dd</option>
            </select>
          </Field>
          {FIELDS.map(([k, label, hint]) => (
            <Field key={k} label={label} hint={hint}>
              <select className="select" value={map[k] == null ? "" : String(map[k])} onChange={(e) => setCol(k, e.target.value)}>
                <option value="">—</option>
                {pv.columns.map((c) => <option key={c.index} value={c.index}>{c.header}{c.samples[0] ? ` (ej. ${c.samples[0]})` : ""}</option>)}
              </select>
            </Field>
          ))}
        </div>

        {dirty ? <p className="muted" style={{ fontSize: 13 }}>Cambiaste columnas: tocá «Actualizar vista previa» antes de guardar.</p> : <>
          <h4 className="h3" style={{ margin: "16px 0 6px" }}>Vista previa</h4>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 8, fontSize: 13 }}>
            <Kpi label="Nuevos" value={String(pv.new)} />
            <Kpi label="Repetidos (se omiten)" value={String(pv.duplicates)} />
            <Kpi label="Antes del corte (se omiten)" value={String(pv.before_cutoff)} />
            <Kpi label="Entra" value={fmtMoney(pv.credits, cur)} tone="ok" />
            <Kpi label="Sale" value={fmtMoney(pv.debits, cur)} tone="bad" />
            <Kpi label={`Saldo del banco${pv.date_to ? ` al ${fmtDate(pv.date_to)}` : ""}`} value={pv.closing_balance ? fmtMoney(pv.closing_balance, cur) : "—"} />
          </div>
          {pv.continuity && (
            <div className={`badge badge--${pv.continuity.ok ? "ok" : "warn"}`} style={{ marginTop: 10, whiteSpace: "normal", display: "block", padding: "8px 10px", lineHeight: 1.5 }}>
              {pv.continuity.ok
                ? `El saldo con que arranca el archivo (${fmtMoney(pv.continuity.file, cur)}) calza con lo que ya hay en Crimson.`
                : `El archivo arranca el ${fmtDate(pv.continuity.date)} con ${fmtMoney(pv.continuity.file, cur)} y en Crimson el saldo según el banco es ${fmtMoney(pv.continuity.crimson, cur)}: falta un estado de cuenta anterior o el saldo inicial no es el del banco.`}
            </div>
          )}
          {pv.errors.length > 0 && (
            <div className="badge badge--bad" style={{ marginTop: 10, whiteSpace: "normal", display: "block", padding: "8px 10px", lineHeight: 1.5 }}>
              {pv.errors.length} fila(s) con errores (no se guarda nada hasta corregirlas): {pv.errors.slice(0, 5).map((e) => `fila ${e.row}: ${e.error}`).join(" · ")}
            </div>
          )}
          <div style={{ overflowX: "auto", marginTop: 10, maxHeight: 360, overflowY: "auto" }}>
            <table className="table table--sm table--stack">
              <thead><tr><th>Fecha</th><th>Descripción</th><th>Referencia</th><th className="num">Monto</th><th className="num">Saldo</th><th /></tr></thead>
              <tbody>
                {pv.rows.slice(0, 200).map((r) => (
                  <tr key={r.row} style={{ opacity: r.duplicate || r.before_cutoff ? 0.5 : 1 }}>
                    <td data-label="Fecha">{fmtDate(r.date)}</td>
                    <td data-label="Descripción" className="stack-full">{r.description}</td>
                    <td data-label="Referencia" className="mono">{r.reference || "—"}</td>
                    <td data-label="Monto" className="num mono" style={{ color: num(r.amount) >= 0 ? "var(--ok)" : "var(--bad)" }}>{fmtMoney(r.amount, cur)}</td>
                    <td data-label="Saldo" className="num mono">{r.balance ? fmtMoney(r.balance, cur) : "—"}</td>
                    <td data-label="Nota">{r.duplicate ? <span className="badge badge--muted">Repetido</span> : r.before_cutoff ? <span className="badge badge--muted">Antes del corte</span> : <span className="badge badge--info">Nuevo</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(pv.rows.length > 200 || pv.rows_truncated) && <p className="muted" style={{ fontSize: 12 }}>Se muestran los primeros 200 de {pv.total}.</p>}
          </div>
        </>}
      </>}
    </Modal>
  );
}

export function Kpi({ label, value, tone, hint }: { label: string; value: string; tone?: "ok" | "bad" | "warn"; hint?: string }) {
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--hair-2)", borderRadius: 12, padding: "10px 14px", minWidth: 0 }}>
      <div className="meta">{label}</div>
      <div className="mono" style={{ fontWeight: 700, fontSize: 17, overflowWrap: "anywhere", color: tone ? `var(--${tone})` : undefined }}>{value}</div>
      {hint && <div className="muted" style={{ fontSize: 11 }}>{hint}</div>}
    </div>
  );
}
