/* Facturas de proveedores: "Por revisar" (con plazo) y "Recibidas" (todo, con filtros y paginacion). En "Por revisar"
   cada comprobante trae su respuesta a Hacienda ya preparada (reunion 2, pedido 4): se aprueba con un clic, una por una
   o todas las listas, y sale por el mismo flujo que "Responder". El estado del buzon se dice en palabras. */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Badge, Card, Empty, I, Icon, Loading } from "../../../ui/components";
import { ACTION_LABEL, DOC_TYPES, ivaLabel, plazo, type InboxStatus, type Proposal, type Reception } from "../hacienda";
import type { ReceptionSummary } from "../useReceptionCount";
import UploadDialog from "./UploadDialog";

type Page = { items: Reception[]; total: number; counts: Record<string, number> };
const PAGE = 50;

function Files({ r }: { r: Reception }) {
  const sup = r.supplier_hacienda_status === "rechazado" ? "✗" : r.has_supplier_response ? "✓" : "—";
  return (
    <span className="mono" style={{ fontSize: 11.5, whiteSpace: "nowrap" }} title="XML · PDF · respuesta de Hacienda al proveedor">
      XML ✓ · PDF {r.has_pdf ? "✓" : "—"} · Resp. <span style={{ color: sup === "✗" ? "var(--bad)" : undefined }}>{sup}</span>
    </span>
  );
}

function Sugerida({ p }: { p: Proposal | null | undefined }) {
  if (!p) return <span className="muted">—</span>;
  const txt = `${ACTION_LABEL[p.action] || p.action} · ${ivaLabel(p.iva_condition)}${p.link_expense_id ? " · vincula su gasto" : p.create_expense ? ` · gasto${p.category ? ` en ${p.category}` : ""}` : " · sin gasto"}`;
  return (
    <span style={{ fontSize: 12.5 }} title={[...p.reasons, ...p.blockers].join(" · ")}>
      {txt}
      {!p.ready && <div className="meta" style={{ textTransform: "none", color: "var(--warn)" }}>{p.blockers[0] || "Revísela a mano"}</div>}
    </span>
  );
}

function Deadline({ r }: { r: Reception }) {
  if (r.status !== "por_revisar") return <span className="muted">{fmtDate(r.deadline)}</span>;
  const d = r.business_days_left;
  const color = d !== null && d <= 2 ? "var(--bad)" : undefined;
  return <span style={{ color, fontWeight: color ? 700 : undefined }}>{fmtDate(r.deadline)}<div className="meta" style={{ textTransform: "none", color }}>{plazo(d)}</div></span>;
}

export default function Inbox({ all = false }: { all?: boolean }) {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [page, setPage] = useState<Page | null>(null);
  const [sum, setSum] = useState<ReceptionSummary | null>(null);
  const [inbox, setInbox] = useState<InboxStatus | null>(null);
  const [upload, setUpload] = useState(false);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ status: "", doc_type: "", month: "", q: "", offset: 0 });

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: String(PAGE), offset: String(all ? f.offset : 0) });
    if (!all) qs.set("status", "por_revisar");
    else {
      if (f.status) qs.set("status", f.status);
      if (f.doc_type) qs.set("doc_type", f.doc_type);
      if (f.q.trim()) qs.set("q", f.q.trim());
      if (f.month) {
        const [y, m] = f.month.split("-").map(Number);
        qs.set("from", `${f.month}-01`);
        qs.set("to", new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10));
      }
    }
    api<Page>(`/reception/documents?${qs}`).then(setPage).catch((e) => toast(e.message, "bad"));
    api<ReceptionSummary>("/reception/summary").then(setSum).catch(() => setSum(null));
  }, [all, f, toast]);
  useEffect(load, [load]);
  useEffect(() => { api<InboxStatus>("/reception/inbox/status").then(setInbox).catch(() => setInbox(null)); }, []);

  const runInbox = async () => {
    setBusy(true);
    try {
      const r = await api<{ messages: number; nuevos: number; errores: string[] }>("/reception/inbox/run", { method: "POST" });
      toast(`${r.messages} correos revisados · ${r.nuevos} comprobantes nuevos${r.errores.length ? ` · ${r.errores.length} con error` : ""}`);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const approve = async (ids: number[]) => {
    if (!ids.length) return;
    setBusy(true);
    try {
      if (ids.length === 1) {
        const r = await api<Reception>(`/reception/${ids[0]}/approve`, { method: "POST" });
        toast(r.hacienda_status === "simulado" ? "Respuesta aprobada (simulado: no se envió a Hacienda)" : `Respuesta aprobada y enviada · Hacienda: ${r.hacienda_status}`);
      } else {
        const r = await api<{ aprobadas: { id: number }[]; omitidas: { id: number; motivo: string }[] }>("/reception/approve-batch", { method: "POST", json: { ids } });
        toast(`${r.aprobadas.length} respuesta(s) aprobada(s)${r.omitidas.length ? ` · ${r.omitidas.length} quedaron para revisar a mano: ${r.omitidas[0].motivo}` : ""}`, r.omitidas.length ? "bad" : undefined);
      }
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const items = page?.items ?? [];
  const listas = !all ? items.filter((r) => r.proposal?.ready).map((r) => r.id) : [];
  const puedeAprobar = allows("accounting.editar");
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "flex-end" }}>
        {allows("accounting.crear") && <Link className="btn btn--ghost btn--sm" to="/importar?kind=recepciones"><Icon d={I.upload} />Importar histórico</Link>}
        {allows("accounting.crear") && (
          <button className="btn btn--soft btn--sm" onClick={runInbox} disabled={busy || !inbox || inbox.source === "ninguno"} title={inbox?.source === "ninguno" ? "Apagado: configúrelo en Ajustes → XML de proveedores o en las variables del servidor" : undefined}>
            <Icon d={I.refresh} />{busy ? "Revisando…" : inbox?.source === "ninguno" ? "Buzón apagado" : "Revisar buzón ahora"}
          </button>
        )}
        {allows("accounting.crear") && <button className="btn btn--crimson btn--sm" onClick={() => setUpload(true)}><Icon d={I.plus} />Cargar archivos</button>}
      </div>

      {!all && sum?.buzon && (
        <div className={`acc-warn`} role="status" style={sum.buzon.state === "ok" ? { borderColor: "var(--hair)", background: "transparent", color: "var(--text-2)" } : undefined}>
          <b>Buzón:</b> {sum.buzon.label}{sum.buzon.last_run_at ? ` · última revisión ${parseTs(sum.buzon.last_run_at).toLocaleString("es-CR")}` : ""}
        </div>
      )}
      {!all && puedeAprobar && listas.length > 0 && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className="muted" style={{ fontSize: 13, flex: 1, minWidth: 220 }}>{listas.length} comprobante(s) con la respuesta lista: revise la sugerencia y apruébela; sale a Hacienda por el flujo de siempre.</span>
          <button className="btn btn--crimson btn--sm" disabled={busy} onClick={() => approve(listas)}>Aprobar las {listas.length} sugeridas</button>
        </div>
      )}

      {!all && sum && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 10 }}>
          {([["Por revisar", sum.por_revisar, undefined], ["Vencen pronto", sum.vencen_pronto, sum.vencen_pronto ? "var(--bad)" : undefined], ["Vencidos", sum.vencidos, sum.vencidos ? "var(--bad)" : undefined], ["Mensajes rechazados", sum.mensajes_rechazados, sum.mensajes_rechazados ? "var(--bad)" : undefined]] as [string, number, string | undefined][]).map(([l, n, c]) => (
            <div key={l} className="card"><div className="card__body"><div className="meta">{l}</div><div style={{ fontWeight: 700, fontSize: 22, color: c }}>{n}</div></div></div>
          ))}
        </div>
      )}

      {all && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <select className="select" style={{ maxWidth: 200 }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value, offset: 0 })}>
            <option value="">Todos los estados</option>
            {["por_revisar", "respondido", "sin_respuesta", "ajeno", "invalido", "descartado", "historico"].map((s) => <option key={s} value={s}>{s.replace("_", " ")} {page?.counts[s] !== undefined ? `(${page.counts[s]})` : ""}</option>)}
          </select>
          <select className="select" style={{ maxWidth: 170 }} value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value, offset: 0 })}><option value="">Todos los tipos</option>{Object.entries(DOC_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <input className="input" style={{ maxWidth: 160 }} type="month" value={f.month} onChange={(e) => setF({ ...f, month: e.target.value, offset: 0 })} />
          <input className="input" style={{ maxWidth: 260 }} placeholder="Proveedor, cédula, consecutivo o clave" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value, offset: 0 })} />
        </div>
      )}

      <Card flush>
        {page === null ? <Loading /> : items.length === 0 ? <Empty title={all ? "Sin comprobantes" : "Nada por revisar"} hint="Los comprobantes llegan solos por el buzón o se cargan con «Cargar archivos»." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>Proveedor</th><th>Tipo</th><th>Documento</th><th className="num">Impuesto</th><th className="num">Total</th>{all && <><th>Acción</th><th>Condición</th><th className="num">IVA acreditable</th><th className="num">Gasto aplicable</th><th>Hacienda</th></>}{!all && <th>Respuesta sugerida</th>}<th>Archivos</th><th>{all ? "Estado" : "Plazo"}</th><th /></tr></thead>
              <tbody>{items.map((r) => (
                <tr key={r.id}>
                  <td><b>{r.issuer_name || "—"}</b><div className="meta" style={{ textTransform: "none" }}>{r.issuer_id}{r.issuer_email ? ` · ${r.issuer_email}` : ""}</div></td>
                  <td><span className="badge badge--muted">{r.doc_type}</span></td>
                  <td className="mono" style={{ fontSize: 12 }}>{r.consecutive || r.clave.slice(-10)}<div className="muted">{fmtDate(r.issue_date)}</div></td>
                  <td className="num money">{fmtMoney(r.tax_total, r.currency)}</td>
                  <td className="num money" style={{ fontWeight: 700 }}>{fmtMoney(r.total, r.currency)}{r.currency !== "CRC" && <div className="muted" style={{ fontWeight: 400 }}>{fmtMoney(r.total_crc)}</div>}</td>
                  {all && <>
                    <td className="muted">{r.action ? ACTION_LABEL[r.action] : "—"}</td>
                    <td className="muted" style={{ fontSize: 12 }}>{r.action ? ivaLabel(r.iva_condition) : "—"}</td>
                    <td className="num money">{r.credit_amount !== null ? fmtMoney(r.credit_amount, r.currency) : "—"}</td>
                    <td className="num money">{r.applicable_amount !== null ? fmtMoney(r.applicable_amount, r.currency) : "—"}</td>
                    <td><Badge status={r.hacienda_status} /></td>
                  </>}
                  {!all && <td><Sugerida p={r.proposal} /></td>}
                  <td><Files r={r} /></td>
                  <td>{all ? <Badge status={r.status} /> : <Deadline r={r} />}</td>
                  <td className="num" style={{ whiteSpace: "nowrap" }}>
                    {!all && puedeAprobar && r.proposal?.ready && <button className="btn btn--sm btn--crimson" style={{ marginRight: 6 }} disabled={busy} onClick={() => approve([r.id])}>Aprobar y enviar</button>}
                    <button className={`btn btn--sm ${r.status === "por_revisar" && !r.proposal?.ready ? "btn--crimson" : "btn--ghost"}`} onClick={() => nav(`/contabilidad/facturas/${r.id}`)}>{r.status === "por_revisar" ? "Revisar" : "Ver"}</button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {all && page && page.total > PAGE && (
          <div className="list-head">
            <span className="muted" style={{ fontSize: 13 }}>{f.offset + 1}–{Math.min(f.offset + PAGE, page.total)} de {page.total}</span>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn btn--ghost btn--sm" disabled={f.offset === 0} onClick={() => setF({ ...f, offset: Math.max(0, f.offset - PAGE) })}>Anterior</button>
              <button className="btn btn--ghost btn--sm" disabled={f.offset + PAGE >= page.total} onClick={() => setF({ ...f, offset: f.offset + PAGE })}>Siguiente</button>
            </div>
          </div>
        )}
      </Card>
      {upload && <UploadDialog onClose={() => setUpload(false)} onDone={load} />}
    </>
  );
}
