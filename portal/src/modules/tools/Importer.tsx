/* Importador de datos / herramienta de migración desde Fygaro. */
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { uploadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, I, Icon } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";

type ImportKind = "customers" | "products" | "suppliers" | "invoices" | "catalogo";
type ActionKind = "nuevo" | "actualizar" | "omitir" | "error";
type PreviewRow = { row: number; action: ActionKind; detail: string | null };
type PreviewResult = {
  kind: ImportKind; commit: boolean; rows: number;
  summary: { nuevo: number; actualizar: number; omitir: number; error: number };
  columns: Record<string, string | null>;
  result: PreviewRow[];
};

const KINDS: { id: ImportKind; label: string; desc: string; route: string }[] = [
  { id: "customers", label: "Clientes", desc: "Nombre, cédula, correo y teléfono.", route: "/clientes" },
  { id: "products", label: "Productos y servicios", desc: "Código, nombre, precio, CABYS, impuesto.", route: "/productos" },
  { id: "suppliers", label: "Proveedores", desc: "Nombre, cédula, contacto y condiciones de pago.", route: "/contabilidad" },
  { id: "invoices", label: "Facturas históricas", desc: "Documentos previos como referencia (no se reenvían a Hacienda).", route: "/facturas" },
  { id: "catalogo", label: "Lista de precios de proveedor", desc: "El costo entra tal cual y el precio de venta se calcula con el margen.", route: "/productos" },
];

const ACTION_LABELS: Record<ActionKind, string> = {
  nuevo: "Nuevo", actualizar: "Actualizar", omitir: "Omitir", error: "Error",
};
const ACTION_COLORS: Record<ActionKind, string> = {
  nuevo: "var(--ok)", actualizar: "var(--info)", omitir: "var(--text-3)", error: "var(--bad)",
};

export default function Importer() {
  const { toast } = useSession();
  const fileRef = useRef<HTMLInputElement>(null);

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [kind, setKind] = useState<ImportKind | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [filterAction, setFilterAction] = useState<ActionKind | "">("");
  const [busy, setBusy] = useState(false);
  const [committed, setCommitted] = useState<PreviewResult | null>(null);
  /* La lista del proveedor trae costos, no precios: el margen y la divisa definen a cuánto se vende. */
  const [opts, setOpts] = useState({ margin: "35", supplier: "", brand: "", currency: "USD" });
  const qs = () => (kind === "catalogo" ? `&margin=${encodeURIComponent(opts.margin)}&supplier=${encodeURIComponent(opts.supplier)}&brand=${encodeURIComponent(opts.brand)}&currency=${opts.currency}` : "");

  const chooseKind = (k: ImportKind) => { setKind(k); setStep(2); setFile(null); setPreview(null); setCommitted(null); };

  const pickFile = (f: File) => {
    setFile(f);
    doPreview(f);
  };

  const doPreview = async (f: File) => {
    if (!kind) return;
    setBusy(true);
    try {
      const r = await uploadFile<PreviewResult>(`/import/${kind}?commit=false${qs()}`, f);
      setPreview(r); setStep(3); setFilterAction("");
    } catch (e) { toast(e instanceof Error ? e.message : "Error al previsualizar", "bad"); }
    finally { setBusy(false); }
  };

  const doCommit = async () => {
    if (!kind || !file) return;
    setBusy(true);
    try {
      const r = await uploadFile<PreviewResult>(`/import/${kind}?commit=true${qs()}`, file);
      setCommitted(r); toast(`Importación completa: ${r.summary.nuevo + r.summary.actualizar} registros procesados`);
    } catch (e) { toast(e instanceof Error ? e.message : "Error al importar", "bad"); }
    finally { setBusy(false); }
  };

  const reset = () => { setStep(1); setKind(null); setFile(null); setPreview(null); setCommitted(null); setFilterAction(""); };

  const currentKindMeta = KINDS.find((k) => k.id === kind);
  const filteredRows = preview?.result.filter((r) => !filterAction || r.action === filterAction) ?? [];
  const commitCount = preview ? preview.summary.nuevo + preview.summary.actualizar : 0;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">13 · Herramientas</div><h1 className="h1">Importar datos</h1></div>
        {step > 1 && <div className="page-head__actions"><button className="btn btn--ghost btn--sm" onClick={reset}><Icon d={I.x} />Volver al inicio</button></div>}
      </div>

      {/* guía migración Fygaro */}
      <div style={{ background: "var(--info-soft)", border: "1px solid var(--info)", borderRadius: 12, padding: "14px 18px", fontSize: 13, color: "var(--text-2)", lineHeight: 1.7 }}>
        <b style={{ color: "var(--info)" }}>Migrar desde Fygaro:</b>
        {" "}
        1) En Fygaro exportá <b>Clientes</b>, <b>Productos</b> y <b>Facturas</b> a Excel.{"  "}
        2) Importá en este orden: <b>Clientes → Productos → Facturas históricas</b>.{"  "}
        3) Las facturas históricas quedan como referencia; no se reenvían a Hacienda ni consumen consecutivos.
      </div>

      {/* paso 1: elegir tipo */}
      {step === 1 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: 14, marginTop: 4 }}>
          {KINDS.map((k) => (
            <button
              key={k.id}
              onClick={() => chooseKind(k.id)}
              style={{ textAlign: "left", background: "var(--surface)", border: "1.5px solid var(--hair-2)", borderRadius: 14, padding: "18px 20px", cursor: "pointer", transition: "border-color .15s" }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--crimson)")}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--hair-2)")}
            >
              <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>{k.label}</div>
              <div style={{ fontSize: 13, color: "var(--text-3)", marginBottom: 12 }}>{k.desc}</div>
              <span onClick={(e) => e.stopPropagation()}>
                <AuthLink
                  path={`/import/${k.id}/template`}
                  download={`plantilla-${k.id}.xlsx`}
                  className="btn btn--ghost btn--sm"
                >
                  <Icon d={I.reports} size={14} />Descargar plantilla
                </AuthLink>
              </span>
            </button>
          ))}
        </div>
      )}

      {/* paso 2: elegir archivo */}
      {step === 2 && kind && (
        <Card title={`Importar ${currentKindMeta?.label}`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <p className="muted" style={{ fontSize: 13 }}>{currentKindMeta?.desc}{kind === "catalogo" ? " Subí el archivo tal como lo manda el proveedor: no hay plantilla que llenar." : " Descargá la plantilla, completala y subila aquí."}</p>
            {kind !== "catalogo" && (
              <AuthLink path={`/import/${kind}/template`} download={`plantilla-${kind}.xlsx`} className="btn btn--ghost btn--sm">
                <Icon d={I.reports} />Descargar plantilla
              </AuthLink>
            )}
            {kind === "catalogo" && (
              <div className="grid-3">
                <div className="field"><label>Proveedor</label><input className="input" value={opts.supplier} onChange={(e) => setOpts({ ...opts, supplier: e.target.value })} placeholder="Eurocomp Costa Rica" /><small>Se crea si no existe y queda ligado a cada producto.</small></div>
                <div className="field"><label>Marca por defecto</label><input className="input" value={opts.brand} onChange={(e) => setOpts({ ...opts, brand: e.target.value })} placeholder="Hikvision" /></div>
                <div className="field"><label>Divisa del costo</label><select className="select" value={opts.currency} onChange={(e) => setOpts({ ...opts, currency: e.target.value })}><option>USD</option><option>CRC</option></select></div>
                <div className="field" style={{ gridColumn: "1 / -1" }}><label>Margen sobre venta · {opts.margin} %</label><input type="range" min={0} max={80} step={1} value={opts.margin} onChange={(e) => setOpts({ ...opts, margin: e.target.value })} /><small>El precio se calcula con el tipo de cambio del día y se redondea a la centena.</small></div>
              </div>
            )}
            <div
              role="button"
              tabIndex={0}
              onClick={() => fileRef.current?.click()}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") fileRef.current?.click(); }}
              style={{ border: "1.5px dashed var(--hair-2)", borderRadius: 12, padding: "32px 24px", textAlign: "center", cursor: "pointer", color: "var(--text-3)", fontSize: 14 }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) pickFile(f); }}
            >
              <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) pickFile(f); e.target.value = ""; }} />
              {busy ? <span className="spinner" /> : <><Icon d={I.plus} />{" Arrastrá o hacé clic para elegir un .csv o .xlsx"}</>}
            </div>
          </div>
        </Card>
      )}

      {/* paso 3: previsualización */}
      {step === 3 && preview && !committed && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))", gap: 10 }}>
            {(["nuevo", "actualizar", "omitir", "error"] as ActionKind[]).map((a) => (
              <div key={a} style={{ background: "var(--surface)", border: "1px solid var(--hair-2)", borderRadius: 12, padding: "12px 16px", cursor: "pointer", outline: filterAction === a ? `2px solid ${ACTION_COLORS[a]}` : undefined }}
                onClick={() => setFilterAction(filterAction === a ? "" : a)}>
                <div className="meta">{ACTION_LABELS[a]}</div>
                <div style={{ fontWeight: 700, fontSize: 22, color: ACTION_COLORS[a] }}>{preview.summary[a]}</div>
              </div>
            ))}
          </div>

          {/* columnas reconocidas */}
          <Card title="Columnas detectadas">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {Object.entries(preview.columns).map(([field, header]) => (
                <span key={field} className="badge" style={{ background: header ? "var(--ok-soft)" : "var(--bg-2)", color: header ? "var(--ok)" : "var(--text-3)", border: `1px solid ${header ? "var(--ok)" : "var(--hair-2)"}` }}>
                  {field}{header ? ` ← ${header}` : " (no encontrada)"}
                </span>
              ))}
            </div>
          </Card>

          {/* tabla de filas */}
          <Card title={`Vista previa · ${preview.rows} filas`} flush extra={
            <div className="tabs">
              <button className={filterAction === "" ? "is-active" : ""} onClick={() => setFilterAction("")}>Todos</button>
              {(["nuevo", "actualizar", "omitir", "error"] as ActionKind[]).map((a) => (
                <button key={a} className={filterAction === a ? "is-active" : ""} onClick={() => setFilterAction(a)}>{ACTION_LABELS[a]} ({preview.summary[a]})</button>
              ))}
            </div>
          }>
            {filteredRows.length === 0 ? <Empty /> : (
              <table className="table">
                <thead><tr><th className="num">Fila</th><th>Acción</th><th>Detalle</th></tr></thead>
                <tbody>
                  {filteredRows.map((r) => (
                    <tr key={r.row}>
                      <td className="num mono muted">{r.row}</td>
                      <td><span className="badge" style={{ background: ACTION_COLORS[r.action] + "22", color: ACTION_COLORS[r.action] }}>{ACTION_LABELS[r.action]}</span></td>
                      <td style={{ fontSize: 13 }}>{r.detail || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
            <button className="btn btn--ghost" onClick={() => setStep(2)}>Cambiar archivo</button>
            <button className="btn btn--crimson" onClick={doCommit} disabled={busy || commitCount === 0}>
              {busy ? <span className="spinner" /> : `Importar ${commitCount} registro${commitCount !== 1 ? "s" : ""}`}
            </button>
          </div>
        </>
      )}

      {/* resultado final */}
      {committed && currentKindMeta && (
        <Card title="Importación completada">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))", gap: 10, marginBottom: 16 }}>
            {(["nuevo", "actualizar", "omitir", "error"] as ActionKind[]).map((a) => (
              <div key={a} style={{ background: "var(--surface)", border: "1px solid var(--hair-2)", borderRadius: 12, padding: "12px 16px" }}>
                <div className="meta">{ACTION_LABELS[a]}</div>
                <div style={{ fontWeight: 700, fontSize: 22, color: ACTION_COLORS[a] }}>{committed.summary[a]}</div>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <Link className="btn btn--crimson" to={currentKindMeta.route}>Ver {currentKindMeta.label}</Link>
            <button className="btn btn--ghost" onClick={reset}>Nueva importación</button>
          </div>
        </Card>
      )}
    </>
  );
}
