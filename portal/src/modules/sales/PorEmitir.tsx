/* Facturas -> Por emitir (plan 1.7): lo pagado o enviado que todavia no tiene comprobante con validez fiscal (sin
   emitir, o emitido solo en simulado o en pruebas). Mientras Hacienda no esta en produccion se emite en Fygaro el mismo
   dia y aqui se marca "Emitida en Fygaro" con su numero: lo marcado sale de la lista y la plataforma nunca lo vuelve a
   emitir. Marcar es de contabilidad (sales.emitir_fuera). Con produccion activa solo se marca lo creado antes de
   activarla (el corte: si ya salio en Fygaro, emitirlo aqui lo duplicaria). La definicion es la del servidor
   (services/fiscal_queue.py): la lista, el contador y el correo diario de las 08:30 dicen lo mismo. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Empty, Field, Loading, Modal } from "../../ui/components";

type Row = {
  id: number; number: string; doc_type: string; customer_name: string | null; currency: string; total: string; balance: string; status: string; issue_date: string; days_waiting: number;
  /* sin_emitir, o aceptada/rechazada/error solo en simulado o en pruebas; markable: se puede marcar en este modo */
  einvoice_status: string; markable: boolean;
};
type Queue = { mode: string; production_since: string | null; today: string; total: number; items: Row[]; oldest_issue_date: string | null; emitidas_fuera_mes: number; mes: string; can_mark: boolean };

/** Con produccion activa: lo creado antes de activarla puede ya estar emitido en Fygaro. */
export const CORTE_AVISO = "Hacienda ya está en producción y esta factura es de antes de activarla: confirmá en Fygaro si ya se emitió. Si se emitió, marcala aquí; emitirla de nuevo duplicaría el comprobante.";

/** Hoy en Costa Rica (AAAA-MM-DD): despues de las 18:00 la fecha UTC ya es la de manana. */
export const hoyCR = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });

/** "Marcar emitida en Fygaro": numero y fecha del comprobante de alla. Lo usan la lista y el detalle de la factura. */
export function MarkExternal({ inv, onClose, onDone, warn }: { inv: { id: number; number: string }; onClose: () => void; onDone: () => void; warn?: string }) {
  const { toast } = useSession();
  const [form, setForm] = useState({ number: "", date: hoyCR() });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api(`/fiscal/queue/${inv.id}/external`, { method: "POST", json: { number: form.number.trim(), date: form.date } });
      toast(`${inv.number} marcada como emitida en Fygaro`);
      onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal title={`Marcar ${inv.number} emitida en Fygaro`} onClose={onClose} dirty={!!form.number.trim()} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={busy || !form.number.trim() || !form.date} onClick={save}>Marcar emitida</button></>}>
      {warn && <div className="acc-warn" style={{ marginTop: 0, marginBottom: 10 }}>{warn}</div>}
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>Anotá el número del comprobante que emitiste en Fygaro. Desde ahora la plataforma no la vuelve a emitir ante Hacienda, tampoco al pasar a producción.</p>
      <div className="grid-2">
        <Field label="Número en Fygaro" hint="El consecutivo o la clave del comprobante."><input className="input input--mono" style={{ textAlign: "left" }} autoFocus maxLength={50} value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} /></Field>
        <Field label="Fecha de emisión en Fygaro"><input className="input" type="date" max={hoyCR()} value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

export default function PorEmitir() {
  const { allows } = useSession();
  const [q, setQ] = useState<Queue | null>(null);
  const [failed, setFailed] = useState(false);
  const [mark, setMark] = useState<Row | null>(null);
  const load = useCallback(() => { api<Queue>("/fiscal/queue").then((r) => { setQ(r); setFailed(false); }).catch(() => setFailed(true)); }, []);
  useEffect(() => { load(); }, [load]);
  if (failed) return <Empty title="No se pudo cargar la lista" hint="Probá con Refrescar." />;
  if (!q) return <Loading />;
  const fygaro = q.mode !== "produccion";
  const puede = q.can_mark && allows("sales.emitir_fuera"); // contabilidad: la vendedora ve lo suyo pero no lo marca
  return (
    <>
      <div className="list-filter" role="status">
        <span><b>{q.total}</b> por emitir{q.oldest_issue_date ? ` · la más vieja es del ${fmtDate(q.oldest_issue_date)}` : ""} · <b>{q.emitidas_fuera_mes}</b> emitida{q.emitidas_fuera_mes === 1 ? "" : "s"} en Fygaro este mes</span>
      </div>
      <p className="muted" style={{ fontSize: 13, margin: 0, padding: "10px 18px" }}>
        {fygaro
          ? puede
            ? "Hacienda todavía no está en producción: emití cada una en Fygaro el mismo día y marcala aquí con su número. Lo marcado no se vuelve a emitir."
            : "Hacienda todavía no está en producción: contabilidad emite cada una en Fygaro el mismo día y la marca aquí con su número."
          : `Hacienda está en producción${q.production_since ? ` desde el ${parseTs(q.production_since).toLocaleDateString("es-CR")}` : ""}. Lo de antes de activarla puede ya estar emitido en Fygaro: confirmalo ahí antes de emitirlo y, si ya se emitió, marcalo «Emitida en Fygaro» (emitirlo de nuevo duplicaría el comprobante). Lo demás se emite desde la factura con «Emitir a Hacienda».`}
      </p>
      {q.items.length === 0 ? <Empty title="Nada por emitir" hint="Las facturas pagadas o enviadas que todavía no tienen comprobante electrónico aparecen aquí." /> : (
        <table className="table">
          <thead><tr><th>Código</th><th>Cliente</th><th>Fecha</th><th>Espera</th><th className="num">Monto</th><th>Estado</th><th /></tr></thead>
          <tbody>{q.items.map((d) => (
            <tr key={d.id}>
              <td><Link className="row-link mono" to={`/facturas/${d.id}`}>{d.number}</Link></td>
              <td>{d.customer_name || <span className="muted">Sin cliente</span>}</td>
              <td className="muted">{fmtDate(d.issue_date)}</td>
              <td style={d.days_waiting > 0 ? { color: "var(--bad)" } : undefined} className={d.days_waiting > 0 ? "" : "muted"}>{d.days_waiting > 0 ? `${d.days_waiting} d` : "hoy"}</td>
              <td className="num money">{fmtMoney(d.total, d.currency)}</td>
              <td><Badge status={d.status} />{d.einvoice_status !== "sin_emitir" && <div className="meta" style={{ textTransform: "none" }}>Emitida sin validez fiscal (simulado o pruebas)</div>}</td>
              <td className="num row-actions"><span className="row-actions__in">
                {puede && d.markable && <button className="btn btn--soft btn--sm" onClick={() => setMark(d)}>Marcar emitida en Fygaro</button>}
                <Link className="btn btn--ghost btn--sm" to={`/facturas/${d.id}`}>Ver</Link>
              </span></td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {q.total > q.items.length && <p className="muted" style={{ fontSize: 12, padding: "10px 18px", margin: 0 }}>Se muestran las {q.items.length} más viejas de {q.total}.</p>}
      {mark && <MarkExternal inv={mark} warn={fygaro ? undefined : CORTE_AVISO} onClose={() => setMark(null)} onDone={() => { setMark(null); load(); }} />}
    </>
  );
}
