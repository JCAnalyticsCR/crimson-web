/* Paquete mensual para la contadora (QuickBooks Online por archivo).
   Crimson no escribe en QuickBooks: arma un ZIP que la contadora revisa e importa ella misma.

   Pasos 3 y 4 de QbSetup:
     3) Revisar mes: qué saldría, avisos, Excel de control en borrador y «Marcar como revisado».
     4) Generar paquete: descarga el ZIP (cierra el mes y queda registrado), historial y guía de importación.

   Permisos: accounting.ver lee y baja el borrador / accounting.editar marca revisado /
             accounting.exportar genera / admin (settings.configurar) vuelve a generar un mes cerrado.
   Tablet 768 px: tablas con table--stack. */

import { useEffect, useState } from "react";
import { api, auth, downloadFile } from "../../../lib/api";
import type { ToastAction } from "../../../app/session";
import { useSession } from "../../../app/session";
import { Empty, Field, Icon, I, Loading } from "../../../ui/components";

type Toast = (msg: string, tone?: "ok" | "bad", action?: ToastAction) => void;
type Issue = { severity: "error" | "warning"; code: string; message: string };
type Counts = {
  invoices: number; expenses: number; payments: number; supplier_payments: number; credit_notes: number;
  customers_new: number; customers_changed: number; suppliers_new: number; suppliers_changed: number; late: number; changes: number;
};
export type PackageReview = {
  month: string;
  status: "abierto" | "revisado" | "exportado";
  reviewed_at: string | null;
  reviewed_by: string | null;
  counts: Counts;
  issues: Issue[];
  can_mark_reviewed: boolean;
  can_generate: boolean;
  last_package: { created_at: string | null; by: string | null; documents: number; is_reexport: boolean } | null;
};
type Export = { id: number; month: string; kind: "asientos" | "paquete"; entry_count: number; line_count: number; is_reexport: boolean; reexport_reason: string | null; exported_by: string | null; created_at: string };

const STATUS_LABEL: Record<PackageReview["status"], string> = { abierto: "Abierto", revisado: "Revisado", exportado: "Paquete generado (cerrado)" };

function fmtDateTime(d: string | null) {
  if (!d) return "—";
  try { return new Date(d).toLocaleString("es-CR", { dateStyle: "medium", timeStyle: "short" }); } catch { return d; }
}

function MonthPicker({ month, setMonth }: { month: string; setMonth: (m: string) => void }) {
  return (
    <Field label="Mes">
      <input className="input input--mono" type="month" value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} style={{ width: 160 }} />
    </Field>
  );
}

function StatusBadge({ status }: { status: PackageReview["status"] }) {
  const tone = status === "exportado" ? "ok" : status === "revisado" ? "info" : "muted";
  return <span className={`badge badge--${tone}`}>{STATUS_LABEL[status]}</span>;
}

function usePackageReview(month: string, toast: Toast) {
  const [data, setData] = useState<PackageReview | null>(null);
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    try { setData(await api<PackageReview>(`/qb/paquete/${month}`)); }
    catch (ex) { setData(null); toast(ex instanceof Error ? ex.message : "No se pudo revisar el mes", "bad"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [month]); // eslint-disable-line
  return { data, loading, reload: load };
}

function CountCards({ c }: { c: Counts }) {
  const items: [string, string | number][] = [
    ["Facturas de venta", c.invoices],
    ["Facturas de proveedor", c.expenses],
    ["Cobros", c.payments],
    ["Pagos a proveedores", c.supplier_payments],
    ["Notas de crédito", c.credit_notes],
    ["Clientes nuevos / cambiados", `${c.customers_new} / ${c.customers_changed}`],
    ["Proveedores nuevos / cambiados", `${c.suppliers_new} / ${c.suppliers_changed}`],
    ["Ajustes", c.changes],
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10 }}>
      {items.map(([label, v]) => (
        <div key={label} className="card"><div className="card__body" style={{ padding: "10px 12px" }}>
          <div className="meta">{label}</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>{v}</div>
        </div></div>
      ))}
    </div>
  );
}

function Issues({ issues }: { issues: Issue[] }) {
  if (!issues.length) return <div className="notice notice--ok">Sin problemas para este mes.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {issues.map((i, k) => (
        <div key={k} className={`notice notice--${i.severity === "error" ? "bad" : "warn"}`}>
          <b>{i.severity === "error" ? "Hay que corregir" : "Aviso"}:</b> {i.message}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paso 3: Revisar mes
// ---------------------------------------------------------------------------

export function StepRevisarPaquete({ toast, month, setMonth, onReviewed }: { toast: Toast; month: string; setMonth: (m: string) => void; onReviewed: () => void }) {
  const { allows } = useSession();
  const { data, loading, reload } = usePackageReview(month, toast);
  const [busy, setBusy] = useState(false);
  const canEdit = allows("accounting.editar");

  const draft = async () => {
    setBusy(true);
    try { await downloadFile(`/qb/paquete/${month}/control`, `control_${month}_borrador.xlsx`); }
    catch (ex) { toast(ex instanceof Error ? ex.message : "No se pudo descargar el Excel", "bad"); }
    finally { setBusy(false); }
  };

  const mark = async (on: boolean) => {
    setBusy(true);
    try {
      await api(`/qb/paquete/${month}/${on ? "revisado" : "quitar-revisado"}`, { method: "POST" });
      toast(on ? `Mes ${month} marcado como revisado` : `Mes ${month} vuelve a abierto`);
      await reload();
      if (on) onReviewed();
    } catch (ex) { toast(ex instanceof Error ? ex.message : "No se pudo guardar", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <p className="muted" style={{ marginBottom: 16 }}>
        Antes de armar el paquete, alguien revisa el mes: descargá el <b>Excel de control</b> (borrador), cotejá ventas,
        gastos con su IVA por tarifa, cobros y pagos, y corregí en Crimson lo que haga falta. Cuando esté bien, marcá el
        mes como <b>revisado</b>.
      </p>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 16 }}>
        <MonthPicker month={month} setMonth={setMonth} />
        <button className="btn btn--ghost btn--sm" onClick={reload} disabled={loading}><Icon d={I.refresh} /> Actualizar</button>
        <button className="btn btn--ghost btn--sm" onClick={draft} disabled={busy}><Icon d={I.download} /> Excel de control (borrador)</button>
      </div>

      {loading && !data && <Loading />}
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <StatusBadge status={data.status} />
            {data.reviewed_at && <span className="muted">Revisado por {data.reviewed_by ?? "—"} el {fmtDateTime(data.reviewed_at)}</span>}
          </div>
          <CountCards c={data.counts} />
          <Issues issues={data.issues} />
          {canEdit && data.status === "abierto" && (
            <div>
              <button className="btn btn--crimson btn--sm" onClick={() => mark(true)} disabled={busy || !data.can_mark_reviewed}>
                Marcar el mes como revisado
              </button>
              {!data.can_mark_reviewed && <p className="meta" style={{ marginTop: 6 }}>Corregí lo marcado arriba para poder marcarlo.</p>}
            </div>
          )}
          {canEdit && data.status === "revisado" && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn btn--crimson btn--sm" onClick={onReviewed}>Ir a generar el paquete</button>
              <button className="btn btn--ghost btn--sm" onClick={() => mark(false)} disabled={busy}>Quitar «revisado»</button>
            </div>
          )}
          {data.status === "exportado" && (
            <div className="notice notice--info">
              Este mes ya tiene paquete. Lo que cambie desde ahora sale como <b>ajuste</b> en el paquete del mes siguiente.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Paso 4: Generar paquete
// ---------------------------------------------------------------------------

export function StepGenerarPaquete({ toast, month, setMonth }: { toast: Toast; month: string; setMonth: (m: string) => void }) {
  const { allows } = useSession();
  const { data, loading, reload } = usePackageReview(month, toast);
  const [exports, setExports] = useState<Export[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const canExport = allows("accounting.exportar");
  const canAdmin = allows("settings.configurar");

  const loadExports = () => api<Export[]>("/qb/exports").then((r) => setExports(r.filter((e) => e.kind === "paquete"))).catch(() => setExports([]));
  useEffect(() => { loadExports(); }, []); // eslint-disable-line

  const closed = data?.status === "exportado";

  const generate = async () => {
    setBusy(true);
    try {
      const r = await fetch(`/api/qb/paquete/${month}/generar`, {
        method: "POST",
        credentials: "include",
        headers: { Authorization: `Bearer ${auth.token ?? ""}`, "Content-Type": "application/json" },
        body: JSON.stringify(closed ? { reexport_reason: reason } : {}),
      });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(typeof j.detail === "string" ? j.detail : `Error ${r.status}`); }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `paquete_quickbooks_${month}.zip`; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30_000);
      toast(`Paquete de ${month} descargado`);
      setReason("");
      await Promise.all([reload(), loadExports()]);
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "No se pudo generar el paquete", "bad");
    } finally { setBusy(false); }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 16 }}>
        <MonthPicker month={month} setMonth={setMonth} />
        {data && <StatusBadge status={data.status} />}
      </div>

      {loading && !data && <Loading />}
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 20 }}>
          {data.status === "abierto" && (
            <div className="notice notice--warn">Este mes todavía no está revisado. Revisalo y marcalo en el paso 3 antes de generar el paquete.</div>
          )}
          {data.issues.some((i) => i.severity === "error") && <Issues issues={data.issues.filter((i) => i.severity === "error")} />}
          {data.last_package && (
            <div className="notice notice--info">
              Último paquete de {month}: {fmtDateTime(data.last_package.created_at)} por {data.last_package.by ?? "—"} ({data.last_package.documents} documentos).
            </div>
          )}
          {closed && canAdmin && (
            <Field label="Motivo para volver a generarlo (obligatorio)">
              <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ej.: se perdió el archivo" />
            </Field>
          )}
          {canExport && (
            <div>
              <button
                className="btn btn--crimson btn--sm"
                onClick={generate}
                disabled={busy || !data.can_generate || (closed && (!canAdmin || !reason.trim()))}
              >
                <Icon d={I.download} /> {busy ? "Generando…" : closed ? "Volver a generar el paquete" : "Generar paquete"}
              </button>
              <p className="meta" style={{ marginTop: 6 }}>
                Al generarlo el mes queda cerrado y registrado (quién y cuándo). Crimson no escribe en QuickBooks: el ZIP
                lo importa la contadora.
              </p>
            </div>
          )}
        </div>
      )}

      <GuiaImportar />

      <h3 className="h3" style={{ margin: "20px 0 12px" }}>Paquetes generados</h3>
      {exports === null ? <Loading /> : exports.length === 0 ? (
        <Empty title="Sin paquetes" hint="Todavía no se ha generado ningún paquete mensual." />
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="table table--stack">
            <thead><tr><th>Mes</th><th>Generado</th><th>Por</th><th>Documentos</th><th>Tipo</th></tr></thead>
            <tbody>
              {exports.map((e) => (
                <tr key={e.id}>
                  <td data-label="Mes" className="mono">{e.month}</td>
                  <td data-label="Generado">{fmtDateTime(e.created_at)}</td>
                  <td data-label="Por">{e.exported_by ?? "—"}</td>
                  <td data-label="Documentos" className="num">{e.entry_count}</td>
                  <td data-label="Tipo">
                    {e.is_reexport
                      ? <span className="badge badge--warn" title={e.reexport_reason ?? undefined}>Regenerado</span>
                      : <span className="badge badge--ok">Normal</span>}
                  </td>
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
// Guía corta para la contadora
// ---------------------------------------------------------------------------

export function GuiaImportar() {
  return (
    <details className="card" style={{ marginTop: 8 }}>
      <summary className="card__body" style={{ cursor: "pointer", fontWeight: 600 }}>Cómo importarlo en QuickBooks</summary>
      <div className="card__body" style={{ paddingTop: 0 }}>
        <ol style={{ paddingLeft: 20, lineHeight: 1.8, margin: 0 }}>
          <li>Abrí el ZIP y revisá el <b>Excel de control</b> (hojas Resumen, Ventas, Gastos, Ajustes).</li>
          <li>En QuickBooks: <b>Configuración ⚙ → Importar datos</b>. Importá en este orden:
            <b> Clientes</b> (<code>1_clientes.csv</code>), <b>Proveedores</b> (<code>2_proveedores.csv</code>),
            <b> Facturas</b> (<code>3_facturas_venta.csv</code>) y <b>Facturas de proveedor</b> (<code>4_facturas_proveedor.csv</code>).</li>
          <li>En cada importación elegí el <b>formato de fecha</b> configurado (por defecto dd/mm/aaaa) y la opción de
            impuestos <b>sin impuestos incluidos</b> (exclusive).</li>
          <li>Emparejá los códigos <code>IVA 13%</code>, <code>IVA 4%</code>, <code>IVA 2%</code>, <code>IVA 1%</code>,
            <code> IVA 0%</code> y <code>Exento</code> con los códigos de impuesto de QuickBooks. <code>IVA no acreditable</code> va
            con un código de 0 % (ese IVA ya está sumado al gasto).</li>
          <li>Antes de la primera importación, creá en QuickBooks los productos/servicios <i>Ventas de equipos</i>,
            <i> Ventas de servicios</i> y <i>Ventas de proyectos</i>, cada uno con su cuenta de ingresos.</li>
          <li>Cobros, pagos a proveedores y notas de crédito no se importan por archivo: están en el Excel con fecha,
            medio, referencia y banco para registrarlos o conciliarlos.</li>
          <li>La hoja <b>Ajustes</b> trae lo que cambió después de un paquete anterior: se corrige a mano en QuickBooks
            (no se reimporta, se duplicaría).</li>
        </ol>
        <p className="meta" style={{ marginTop: 8 }}>
          Si hay archivos numerados (<code>_1</code>, <code>_2</code>…) es porque QuickBooks acepta hasta 100 facturas y
          1000 filas por archivo: importalos en orden.
        </p>
      </div>
    </details>
  );
}
