/* QuickBooks Online – pasos guiados:
   1) Importar plan de cuentas
   2) Mapear roles contables
   3) Revisar mes
   4) Exportar (descarga ZIP) con historial de exportaciones

   Permisos: accounting.ver para leer / accounting.exportar para exportar /
             admin para reabrir mes.
   Tablet 768 px: tablas con table--stack. */

import { useEffect, useRef, useState } from "react";
import { api } from "../../../lib/api";
import type { ToastAction } from "../../../app/session";
import { useSession } from "../../../app/session";
import { Empty, Field, Icon, I, Loading, Modal } from "../../../ui/components";

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

type QboAccount = { id: number; full_name: string; number: string | null; account_type: string | null; currency: string };
type RoleRow = { role_key: string; label: string; qbo_account_id: number | null; qbo_account_name: string | null };
type MappingData = { roles: RoleRow[]; unmapped: number };
type Period = { month: string; status: "abierto" | "exportado"; exported_at: string | null };
type Issue = { severity: "error" | "warning"; code: string; message: string };
type ReviewData = { month: string; status: string; entry_count: number; issues: Issue[]; can_export: boolean; generated: { created: number; skipped: number; errors: string[] } };
type Export = { id: number; month: string; entry_count: number; line_count: number; is_reexport: boolean; created_at: string };

const STEPS = [
  { key: "cuentas", label: "1. Plan de cuentas" },
  { key: "mapeo", label: "2. Mapeo de roles" },
  { key: "revision", label: "3. Revisar mes" },
  { key: "exportar", label: "4. Exportar" },
] as const;
type Step = (typeof STEPS)[number]["key"];

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

function fmt(d: string | null) {
  if (!d) return "—";
  try { return new Date(d).toLocaleDateString("es-CR", { dateStyle: "medium" }); } catch { return d; }
}

function thisMonth() {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Paso 1: Importar plan de cuentas
// ---------------------------------------------------------------------------

function StepCuentas({ toast }: { toast: (msg: string, tone?: "ok" | "bad", action?: ToastAction) => void }) {
  const [accounts, setAccounts] = useState<QboAccount[] | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api<QboAccount[]>("/qb/accounts").then(setAccounts).catch(() => setAccounts([]));
  useEffect(() => { load(); }, []); // eslint-disable-line

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setImporting(true);
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await fetch("/api/qb/accounts/import", {
        method: "POST",
        body: fd,
        headers: { Authorization: `Bearer ${(await import("../../../lib/api")).auth.token ?? ""}` },
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail ?? `Error ${r.status}`); }
      const res = await r.json();
      toast(`Importadas: ${res.inserted} nuevas, ${res.updated} actualizadas, ${res.deactivated} desactivadas`);
      load();
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "Error al importar", "bad");
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Exportá el reporte <b>Lista de cuentas</b> de QuickBooks Online (Informes → Lista de cuentas) en CSV o Excel y
        subilo aquí. El sistema importa la jerarquía completa <code>Padre:Hijo</code> tal como QBO la muestra.
        Puede reimportarlo las veces que necesités sin perder el mapeo.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 20 }}>
        <label className="btn btn--crimson btn--sm" style={{ cursor: "pointer" }}>
          {importing ? "Importando…" : <><Icon d={I.upload} /> Subir CSV / XLSX</>}
          <input ref={fileRef} type="file" accept=".csv,.xlsx" style={{ display: "none" }} onChange={onFile} disabled={importing} />
        </label>
        {accounts !== null && <span className="muted">{accounts.length} cuenta{accounts.length !== 1 ? "s" : ""} importada{accounts.length !== 1 ? "s" : ""}</span>}
      </div>
      {accounts === null ? <Loading /> : accounts.length === 0 ? (
        <Empty title="Sin plan de cuentas" hint="Importá el archivo de QBO para continuar con el mapeo." />
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="table table--stack">
            <thead><tr><th>Número</th><th>Cuenta</th><th>Tipo</th><th>Divisa</th></tr></thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td data-label="Número" className="mono">{a.number ?? "—"}</td>
                  <td data-label="Cuenta">{a.full_name}</td>
                  <td data-label="Tipo">{a.account_type ?? "—"}</td>
                  <td data-label="Divisa">{a.currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paso 2: Mapeo de roles
// ---------------------------------------------------------------------------

function StepMapeo({ toast, accounts }: { toast: (msg: string, tone?: "ok" | "bad", action?: ToastAction) => void; accounts: QboAccount[] }) {
  const { allows } = useSession();
  const [data, setData] = useState<MappingData | null>(null);
  const [changed, setChanged] = useState<Record<string, number | null>>({});
  const [saving, setSaving] = useState(false);
  const canEdit = allows("accounting.editar");

  const load = () => api<MappingData>("/qb/mapping").then(setData).catch(() => setData(null));
  useEffect(() => { load(); }, []); // eslint-disable-line

  const setRole = (role_key: string, qbo_account_id: number | null) => {
    setChanged((p) => ({ ...p, [role_key]: qbo_account_id }));
  };

  const save = async () => {
    const items = Object.entries(changed).map(([role_key, qbo_account_id]) => ({ role_key, qbo_account_id }));
    if (!items.length) return;
    setSaving(true);
    try {
      await api("/qb/mapping", { method: "PUT", json: items });
      toast("Mapeo guardado");
      setChanged({});
      load();
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "Error", "bad");
    } finally { setSaving(false); }
  };

  if (!data) return <Loading />;

  const effectiveId = (r: RoleRow) => changed[r.role_key] !== undefined ? changed[r.role_key] : r.qbo_account_id;
  const errors = data.roles.filter((r) => !effectiveId(r));

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
        <div>
          {errors.length > 0
            ? <span style={{ color: "var(--bad)" }}>{errors.length} rol{errors.length !== 1 ? "es" : ""} sin cuenta asignada</span>
            : <span style={{ color: "var(--ok)" }}>Todos los roles tienen cuenta asignada</span>}
        </div>
        {canEdit && Object.keys(changed).length > 0 && (
          <button className="btn btn--crimson btn--sm" onClick={save} disabled={saving}>
            {saving ? "Guardando…" : "Guardar mapeo"}
          </button>
        )}
      </div>
      {accounts.length === 0 && (
        <div className="notice notice--warn" style={{ marginBottom: 12 }}>
          Primero importá el plan de cuentas (Paso 1) para poder asignar cuentas.
        </div>
      )}
      <div style={{ overflowX: "auto" }}>
        <table className="table table--stack">
          <thead><tr><th>Rol contable</th><th>Cuenta QBO</th></tr></thead>
          <tbody>
            {data.roles.map((r) => {
              const selId = effectiveId(r);
              return (
                <tr key={r.role_key} style={{ background: !selId ? "var(--surface-warn, #fff8e1)" : undefined }}>
                  <td data-label="Rol"><div style={{ fontWeight: 600 }}>{r.label}</div><div className="mono muted" style={{ fontSize: 11 }}>{r.role_key}</div></td>
                  <td data-label="Cuenta QBO">
                    {canEdit ? (
                      <select
                        className="select"
                        style={{ minWidth: 200 }}
                        value={selId ?? ""}
                        onChange={(e) => setRole(r.role_key, e.target.value ? Number(e.target.value) : null)}
                      >
                        <option value="">— Sin asignar —</option>
                        {accounts.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.number ? `${a.number} – ` : ""}{a.full_name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span>{r.qbo_account_name ?? <span className="muted">Sin asignar</span>}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paso 3: Revisar mes
// ---------------------------------------------------------------------------

function StepRevision({ toast, month, setMonth }: { toast: (msg: string, tone?: "ok" | "bad", action?: ToastAction) => void; month: string; setMonth: (m: string) => void }) {
  const [data, setData] = useState<ReviewData | null>(null);
  const [loading, setLoading] = useState(false);

  const review = async (m: string) => {
    setLoading(true); setData(null);
    try { setData(await api<ReviewData>(`/qb/review/${m}`)); }
    catch (ex) { toast(ex instanceof Error ? ex.message : "Error al revisar", "bad"); }
    finally { setLoading(false); }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ margin: 0 }}>
          <Field label="Mes">
            <input className="input input--mono" type="month" value={month} onChange={(e) => setMonth(e.target.value)} style={{ width: 160 }} />
          </Field>
        </div>
        <button className="btn btn--crimson btn--sm" onClick={() => review(month)} disabled={loading}>
          {loading ? "Revisando…" : <><Icon d={I.refresh} /> Revisar</>}
        </button>
      </div>

      {loading && <Loading />}

      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <div className="card" style={{ flex: "1 1 140px" }}>
              <div className="card__body">
                <div className="meta">Asientos</div>
                <div style={{ fontSize: 24, fontWeight: 700 }}>{data.entry_count}</div>
              </div>
            </div>
            <div className="card" style={{ flex: "1 1 140px" }}>
              <div className="card__body">
                <div className="meta">Generados / omitidos</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{data.generated.created} / {data.generated.skipped}</div>
              </div>
            </div>
            <div className="card" style={{ flex: "1 1 140px" }}>
              <div className="card__body">
                <div className="meta">Estado del mes</div>
                <div style={{ fontWeight: 700 }}>{data.status === "exportado" ? "✓ Exportado" : "Abierto"}</div>
              </div>
            </div>
          </div>

          {data.issues.length === 0 ? (
            <div className="notice notice--ok">Sin problemas. Puede exportar este mes.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data.issues.map((issue, i) => (
                <div key={i} className={`notice notice--${issue.severity === "error" ? "bad" : "warn"}`}>
                  <b>{issue.severity === "error" ? "Error" : "Aviso"}:</b> {issue.message}
                </div>
              ))}
            </div>
          )}

          {data.generated.errors.length > 0 && (
            <details>
              <summary className="muted" style={{ cursor: "pointer" }}>Errores de generación ({data.generated.errors.length})</summary>
              <ul style={{ marginTop: 8 }}>{data.generated.errors.map((e, i) => <li key={i} className="muted" style={{ fontSize: 12 }}>{e}</li>)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paso 4: Exportar
// ---------------------------------------------------------------------------

type QbConfig = { date_format: string; date_format_options: string[] };

function StepExportar({ toast, month, setMonth }: { toast: (msg: string, tone?: "ok" | "bad", action?: ToastAction) => void; month: string; setMonth: (m: string) => void }) {
  const { allows } = useSession();
  const [periods, setPeriods] = useState<Period[] | null>(null);
  const [exports, setExports] = useState<Export[] | null>(null);
  const [exporting, setExporting] = useState(false);
  const [reopenModal, setReopenModal] = useState<string | null>(null);
  const [reopenReason, setReopenReason] = useState("");
  const [reexportReason, setReexportReason] = useState("");
  const [config, setConfig] = useState<QbConfig | null>(null);
  const [savingConfig, setSavingConfig] = useState(false);
  const canExport = allows("accounting.exportar");
  const canAdmin = allows("settings.configurar");  // solo admin
  const canEdit = allows("accounting.editar");

  const load = async () => {
    const [p, e, cfg] = await Promise.all([
      api<Period[]>("/qb/periods").catch(() => [] as Period[]),
      api<Export[]>("/qb/exports").catch(() => [] as Export[]),
      api<QbConfig>("/qb/config").catch(() => null),
    ]);
    setPeriods(p);
    setExports(e);
    if (cfg) setConfig(cfg);
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const saveDateFormat = async (fmt: string) => {
    if (!canEdit) return;
    setSavingConfig(true);
    try {
      await api("/qb/config", { method: "PUT", json: { date_format: fmt } });
      setConfig((c) => c ? { ...c, date_format: fmt } : c);
      toast("Formato de fecha guardado");
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "Error al guardar", "bad");
    } finally { setSavingConfig(false); }
  };

  const currentPeriod = periods?.find((p) => p.month === month);

  const doExport = async () => {
    if (!canExport) return;
    setExporting(true);
    try {
      const token = (await import("../../../lib/api")).auth.token ?? "";
      const isReexport = currentPeriod?.status === "exportado";
      const body: Record<string, unknown> = {};
      if (isReexport) body.reexport_reason = reexportReason || "Reexportación manual";
      const r = await fetch(`/api/qb/${month}/export`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail ?? `Error ${r.status}`); }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `qbo_export_${month}.zip`; a.click();
      URL.revokeObjectURL(url);
      toast(`Exportación de ${month} descargada`);
      setReexportReason("");
      load();
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "Error al exportar", "bad");
    } finally { setExporting(false); }
  };

  const doReopen = async () => {
    if (!reopenReason.trim()) return;
    try {
      await api(`/qb/${reopenModal}/reopen`, { method: "POST", json: { reason: reopenReason } });
      toast(`Mes ${reopenModal} reabierto`);
      setReopenModal(null);
      setReopenReason("");
      load();
    } catch (ex) { toast(ex instanceof Error ? ex.message : "Error", "bad"); }
  };

  const isClosed = currentPeriod?.status === "exportado";

  return (
    <div>
      {/* Configuración: formato de fecha para QBO */}
      {config && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card__body">
            <p style={{ fontWeight: 600, marginBottom: 8 }}>Configuración de exportación</p>
            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <Field label="Formato de fecha en QBO">
                <select
                  className="input"
                  value={config.date_format}
                  disabled={!canEdit || savingConfig}
                  onChange={(e) => saveDateFormat(e.target.value)}
                  style={{ width: 180 }}
                >
                  {config.date_format_options.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              </Field>
              <p className="meta" style={{ marginTop: 20 }}>
                Debe coincidir con el formato de fecha configurado en su empresa de QuickBooks Online.
              </p>
            </div>
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", marginBottom: 20, flexWrap: "wrap" }}>
        <div style={{ margin: 0 }}>
          <Field label="Mes a exportar">
            <input className="input input--mono" type="month" value={month} onChange={(e) => setMonth(e.target.value)} style={{ width: 160 }} />
          </Field>
        </div>
        {canExport && (
          <button className="btn btn--crimson btn--sm" onClick={doExport} disabled={exporting || (isClosed && !canAdmin)}>
            <Icon d={I.upload} />{exporting ? "Exportando…" : isClosed ? "Reexportar" : "Exportar mes"}
          </button>
        )}
        {isClosed && canAdmin && (
          <button className="btn btn--ghost btn--sm" onClick={() => { setReopenModal(month); setReopenReason(""); }}>
            Reabrir mes
          </button>
        )}
      </div>

      {isClosed && canAdmin && (
        <div style={{ marginBottom: 12 }}>
          <Field label="Motivo de reexportación">
            <input className="input" value={reexportReason} onChange={(e) => setReexportReason(e.target.value)} placeholder="Obligatorio para reexportar un mes cerrado" />
          </Field>
        </div>
      )}

      {currentPeriod && (
        <div className="notice notice--info" style={{ marginBottom: 16 }}>
          Mes {month}: <b>{isClosed ? "Cerrado (exportado)" : "Abierto"}</b>
          {currentPeriod.exported_at && <> · Exportado el {fmt(currentPeriod.exported_at)}</>}
        </div>
      )}

      {/* Ayuda de importación en QBO */}
      <details style={{ marginBottom: 20 }}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>¿Cómo importar en QuickBooks Online?</summary>
        <ol style={{ marginTop: 10, paddingLeft: 20, lineHeight: 2 }}>
          <li>En QBO: <b>Configuración ⚙ → Importar datos → Asientos de diario</b></li>
          <li>Subí el archivo <code>asientos_YYYY-MM.csv</code> del ZIP descargado.</li>
          <li>Si hay varios archivos numerados (<code>_1, _2…</code>), importalos en orden.</li>
          <li>Usá el archivo <code>contactos_YYYY-MM.csv</code> si hay clientes o proveedores nuevos
            (<b>Configuración ⚙ → Importar datos → Clientes</b> o <b>Proveedores</b>).</li>
        </ol>
      </details>

      <h3 className="h3" style={{ marginBottom: 12 }}>Historial de exportaciones</h3>
      {exports === null ? <Loading /> : exports.length === 0 ? (
        <Empty title="Sin exportaciones" hint="Aún no se ha exportado ningún mes." />
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="table table--stack">
            <thead><tr><th>Mes</th><th>Asientos</th><th>Líneas</th><th>Fecha</th><th>Tipo</th></tr></thead>
            <tbody>
              {exports.map((e) => (
                <tr key={e.id}>
                  <td data-label="Mes" className="mono">{e.month}</td>
                  <td data-label="Asientos" className="num">{e.entry_count}</td>
                  <td data-label="Líneas" className="num">{e.line_count}</td>
                  <td data-label="Fecha">{fmt(e.created_at)}</td>
                  <td data-label="Tipo">{e.is_reexport ? <span className="badge badge--warn">Reexportación</span> : <span className="badge badge--ok">Normal</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Estado de todos los meses */}
      {periods && periods.length > 0 && (
        <>
          <h3 className="h3" style={{ margin: "20px 0 12px" }}>Estado de los meses</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="table table--stack">
              <thead><tr><th>Mes</th><th>Estado</th><th>Exportado</th><th></th></tr></thead>
              <tbody>
                {periods.map((per) => (
                  <tr key={per.month}>
                    <td data-label="Mes" className="mono">{per.month}</td>
                    <td data-label="Estado">
                      {per.status === "exportado"
                        ? <span className="badge badge--ok">Cerrado</span>
                        : <span className="badge badge--muted">Abierto</span>}
                    </td>
                    <td data-label="Exportado">{fmt(per.exported_at)}</td>
                    <td className="stack-actions">
                      {per.status === "exportado" && canAdmin && (
                        <button className="btn btn--ghost btn--sm" onClick={() => { setReopenModal(per.month); setReopenReason(""); }}>
                          Reabrir
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {reopenModal && (
        <Modal
          title={`Reabrir mes ${reopenModal}`}
          onClose={() => setReopenModal(null)}
          foot={<>
            <button className="btn btn--ghost" onClick={() => setReopenModal(null)}>Cancelar</button>
            <button className="btn btn--crimson" onClick={doReopen} disabled={!reopenReason.trim()}>Reabrir</button>
          </>}
        >
          <p className="muted" style={{ marginBottom: 12 }}>
            Esta acción permite modificar asientos de un mes ya exportado a QuickBooks.
            Los asientos ya importados en QBO deberán ajustarse manualmente.
          </p>
          <Field label="Motivo de reapertura (obligatorio)">
            <input className="input" value={reopenReason} onChange={(e) => setReopenReason(e.target.value)} placeholder="Ej: corrección de factura N.° FEC-000123" autoFocus />
          </Field>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Componente principal
// ---------------------------------------------------------------------------

export default function QbSetup() {
  const { toast } = useSession();
  const [step, setStep] = useState<Step>("cuentas");
  const [accounts, setAccounts] = useState<QboAccount[]>([]);
  const [month, setMonth] = useState(thisMonth);

  useEffect(() => {
    api<QboAccount[]>("/qb/accounts").then(setAccounts).catch(() => {});
  }, []);

  return (
    <div style={{ paddingBottom: 40 }}>
      {/* Encabezado */}
      <div style={{ marginBottom: 20 }}>
        <div className="meta">QuickBooks Online</div>
        <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
          Genera los asientos de diario del mes y los exporta en el formato de importación de QBO.
          Los asientos no se registran en QuickBooks automáticamente: la contadora los importa manualmente.
        </div>
      </div>

      {/* Pasos */}
      <nav style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 20 }}>
        {STEPS.map((s) => (
          <button
            key={s.key}
            className={`btn btn--sm ${step === s.key ? "btn--crimson" : "btn--ghost"}`}
            onClick={() => setStep(s.key)}
          >
            {s.label}
          </button>
        ))}
      </nav>

      {/* Contenido del paso */}
      <div className="card"><div className="card__body" style={{ padding: "20px 24px" }}>
        {step === "cuentas" && <StepCuentas toast={toast} />}
        {step === "mapeo" && <StepMapeo toast={toast} accounts={accounts} />}
        {step === "revision" && <StepRevision toast={toast} month={month} setMonth={setMonth} />}
        {step === "exportar" && <StepExportar toast={toast} month={month} setMonth={setMonth} />}
      </div></div>
    </div>
  );
}
