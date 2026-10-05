/* Documentos (solo el encargado de la empresa): cotizaciones enviadas y facturas, en PDF. Solo lectura.
   El PDF se descarga (no abre otra pestana). */
import { useEffect, useState } from "react";
import { api, downloadFile, fmtDate, fmtMoney, STATUS } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

type Doc = { id: number; kind: "quotes" | "invoices"; number: string; issue_date: string; due_date: string | null; currency: string; total: string; status: string; balance?: string };

export default function ClientDocs() {
  const { toast } = useSession();
  const [d, setD] = useState<{ quotes: Doc[]; invoices: Doc[] } | null>(null);
  const [tab, setTab] = useState<"invoices" | "quotes">("invoices");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<{ quotes: Doc[]; invoices: Doc[] }>("/cliente/documentos").then(setD).catch((e) => setErr(e.message)); }, []);
  const bajar = (x: Doc) => downloadFile(`/cliente/documentos/${x.kind}/${x.id}/pdf`, `${x.number}.pdf`).catch((e) => toast(e.message, "bad"));
  const rows = d ? d[tab] : [];
  return (
    <>
      <div className="cp-h"><div><div className="meta">Solo lectura</div><h1 className="h1">Documentos</h1></div></div>
      <div className="cp-seg" role="tablist">
        <button role="tab" aria-selected={tab === "invoices"} className={tab === "invoices" ? "is-on" : ""} onClick={() => setTab("invoices")}>Facturas</button>
        <button role="tab" aria-selected={tab === "quotes"} className={tab === "quotes" ? "is-on" : ""} onClick={() => setTab("quotes")}>Cotizaciones</button>
      </div>
      {err ? <p className="muted">{err}</p> : !d ? <span className="spinner" /> : rows.length === 0 ? <p className="muted">No hay {tab === "invoices" ? "facturas" : "cotizaciones"} todavía.</p> : (
        <div className="cp-list">
          {rows.map((x) => {
            const s = STATUS[x.status] ?? { label: x.status, tone: "muted" };
            const saldo = x.balance !== undefined && Number(x.balance) > 0;
            return (
              <div key={`${x.kind}${x.id}`} className="cp-row">
                <div className="cp-row__main">
                  <b>{x.number}</b>
                  <div className="cp-row__meta">{fmtDate(x.issue_date)}{x.due_date ? ` · vence ${fmtDate(x.due_date)}` : ""}{saldo ? ` · saldo ${fmtMoney(x.balance, x.currency)}` : ""}</div>
                </div>
                <div className="cp-row__side">
                  <b className="money">{fmtMoney(x.total, x.currency)}</b>
                  <span className={`badge badge--${s.tone}`}>{s.label}</span>
                  <button className="btn btn--ghost btn--sm" onClick={() => bajar(x)}><Icon d={I.upload} size={14} />PDF</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
