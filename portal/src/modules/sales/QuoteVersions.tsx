/* Panel "Versiones" del editor de cotizaciones: lo que realmente se le envio al cliente (v1, v2…), cada una
   descargable tal como salio, y la respuesta del cliente (quien acepto o rechazo, por que medio y que version).
   Emision (borrador/enviada) y aceptacion (pendiente/aceptada/rechazada) son estados separados. */
import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney, parseTs, type Quote } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Field, I, Icon, Modal } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import "./quote-versions.css";

type Version = { id: number; version: number; sent_at: string; sent_by: string | null; channel: string; recipient: string | null; currency: string; total: string; lines: number; accepted: boolean };
type Acceptance = { id: number; status: string; version: number | null; contact_name: string | null; channel: string | null; decided_on: string | null; notes: string | null; recorded_by: string | null; recorded_at: string };
type Info = { acceptance_status: string; accepted_version: number | null; status: string; current_version: number | null; has_unsent_changes: boolean; versions: Version[]; acceptances: Acceptance[] };

const CANAL: Record<string, string> = { correo: "Correo", whatsapp: "WhatsApp", firma: "Firma", portal: "Portal", telefono: "Teléfono", presencial: "En persona", manual: "Manual" };
const ACEPT: Record<string, { label: string; tone: string }> = {
  pendiente: { label: "Esperando respuesta", tone: "muted" }, aceptada: { label: "Aceptada", tone: "ok" }, rechazada: { label: "Rechazada", tone: "bad" },
};
const when = (s: string) => parseTs(s).toLocaleString("es-CR", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });

export default function QuoteVersions({ quote, onQuote }: { quote: Quote; onQuote: (q: Quote) => void }) {
  const { toast, allows } = useSession();
  const [info, setInfo] = useState<Info | null>(null);
  const [resp, setResp] = useState<null | { status: "aceptada" | "rechazada"; version: string; contact_name: string; channel: string; decided_on: string; notes: string }>(null);
  const [mark, setMark] = useState<null | { channel: string; to: string }>(null);
  const [busy, setBusy] = useState(false);
  const id = quote.id;

  const load = useCallback(() => { api<Info>(`/quotes/${id}/versions`).then(setInfo).catch(() => setInfo(null)); }, [id]);
  // cambia la version, los cambios sin enviar o la respuesta: se vuelve a pedir el historial
  useEffect(() => { load(); }, [load, quote.current_version, quote.has_unsent_changes, quote.acceptance_status, quote.status]);

  const closedDoc = quote.status === "convertida" || quote.status === "anulada";
  const acc = info?.acceptance_status || quote.acceptance_status || "pendiente";
  const last = info?.versions[0];

  const saveResp = async () => {
    if (!resp) return;
    if (!resp.contact_name.trim()) return toast("Indicá quién respondió del lado del cliente", "bad");
    setBusy(true);
    try {
      const q = await api<Quote>(`/quotes/${id}/acceptance`, { method: "POST", json: { ...resp, version: Number(resp.version), notes: resp.notes || null } });
      toast(resp.status === "aceptada" ? `Aceptación de la v${resp.version} registrada` : "Rechazo registrado");
      setResp(null); onQuote(q); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  const undo = async () => {
    if (!confirm(`¿Deshacer la respuesta del cliente (${ACEPT[acc]?.label.toLowerCase()})? Queda en la bitácora.`)) return;
    try { const q = await api<Quote>(`/quotes/${id}/acceptance`, { method: "POST", json: { status: "pendiente" } }); toast("La cotización vuelve a esperar respuesta"); onQuote(q); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  /* Enviada por fuera del correo (WhatsApp, en mano): igual queda la version congelada. */
  const saveMark = async () => {
    if (!mark) return;
    setBusy(true);
    try {
      const q = await api<Quote>(`/quotes/${id}/send`, { method: "POST", json: { channel: mark.channel, to: mark.to.trim() || null } });
      toast(`Marcada como enviada · v${q.current_version}`);
      setMark(null); onQuote(q); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <div className="card qv"><div className="card__body">
      <div className="meta" style={{ marginBottom: 10 }}>Versiones y respuesta del cliente</div>
      <div className="qv-states">
        <div><span className="muted">Emisión</span>{quote.status === "creado" ? <span className="badge badge--muted">Borrador</span> : <Badge status={quote.status} />}</div>
        <div><span className="muted">Cliente</span><span className={`badge badge--${ACEPT[acc]?.tone || "muted"}`}>{ACEPT[acc]?.label || acc}{acc === "aceptada" && info?.accepted_version ? ` · v${info.accepted_version}` : ""}</span></div>
      </div>
      {info?.has_unsent_changes && <p className="qv-warn">Hay cambios sin enviar: el próximo envío queda como <b>v{(info.current_version || 0) + 1}</b>. Lo que el cliente tiene es la v{info.current_version}.</p>}
      {acc === "aceptada" && <p className="muted qv-note">Aceptada: la cotización queda bloqueada y al convertir se usa la versión aceptada.</p>}

      {info && info.versions.length === 0 && <p className="muted qv-note">Todavía no se ha enviado. Cada envío (correo, WhatsApp o en mano) guarda una versión que no cambia.</p>}
      {!!info?.versions.length && (
        <ul className="qv-list">
          {info.versions.map((v) => (
            <li key={v.id} className={v.accepted ? "is-ok" : ""}>
              <div className="qv-list__top"><b>v{v.version}</b>{v.accepted && <span className="badge badge--ok">Aceptada</span>}<span className="money">{fmtMoney(v.total, v.currency)}</span></div>
              <div className="muted qv-list__meta">{when(v.sent_at)} · {CANAL[v.channel] || v.channel}{v.recipient ? ` a ${v.recipient}` : ""}{v.sent_by ? ` · por ${v.sent_by}` : ""}</div>
              <div className="qv-list__act">
                <AuthLink path={`/quotes/${id}/versions/${v.version}/html`}>Ver</AuthLink>
                <AuthLink path={`/quotes/${id}/versions/${v.version}/pdf`}>PDF</AuthLink>
              </div>
            </li>
          ))}
        </ul>
      )}

      {!closedDoc && (
        <div className="qv-actions">
          {allows("sales.editar") && !!last && <button className="btn btn--soft btn--sm" onClick={() => setResp({ status: "aceptada", version: String(info?.accepted_version || last.version), contact_name: "", channel: "correo", decided_on: today(), notes: "" })}><Icon d={I.check} size={14} />Registrar respuesta</button>}
          {allows("sales.editar") && acc !== "pendiente" && <button className="btn btn--ghost btn--sm" onClick={undo}>Deshacer respuesta</button>}
          {allows("sales.enviar") && quote.status !== "por_aprobar" && acc !== "aceptada" && <button className="btn btn--ghost btn--sm" onClick={() => setMark({ channel: "whatsapp", to: "" })} title="Si la mandaste por WhatsApp o la entregaste en mano">Marcar enviada…</button>}
        </div>
      )}

      {!!info?.acceptances.length && (
        <details className="qv-hist">
          <summary>Bitácora de respuestas ({info.acceptances.length})</summary>
          <ul>{info.acceptances.map((a) => (
            <li key={a.id}>
              <b>{a.status === "pendiente" ? "Respuesta deshecha" : `${ACEPT[a.status]?.label || a.status} v${a.version}`}</b>
              {a.contact_name && ` · ${a.contact_name}`}{a.channel && ` por ${CANAL[a.channel] || a.channel}`}{a.decided_on && ` el ${a.decided_on}`}
              <div className="muted">Anotado por {a.recorded_by || "—"} · {when(a.recorded_at)}{a.notes ? ` · «${a.notes}»` : ""}</div>
            </li>
          ))}</ul>
        </details>
      )}

      {resp && info && (
        <Modal title="Respuesta del cliente" onClose={() => setResp(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setResp(null)}>Cancelar</button>
          <button className="btn btn--crimson" disabled={busy} onClick={saveResp}>Registrar</button>
        </>}>
          <div className="tabs" style={{ alignSelf: "flex-start" }}>
            <button className={resp.status === "aceptada" ? "is-active" : ""} onClick={() => setResp({ ...resp, status: "aceptada" })}>Aceptó</button>
            <button className={resp.status === "rechazada" ? "is-active" : ""} onClick={() => setResp({ ...resp, status: "rechazada" })}>Rechazó</button>
          </div>
          <div className="grid-2">
            <Field label="Versión"><select className="select" value={resp.version} onChange={(e) => setResp({ ...resp, version: e.target.value })}>{info.versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {fmtMoney(v.total, v.currency)}</option>)}</select></Field>
            <Field label="Fecha de la respuesta"><input className="input" type="date" max={today()} value={resp.decided_on} onChange={(e) => setResp({ ...resp, decided_on: e.target.value })} /></Field>
            <Field label="Quién respondió" hint="Nombre de la persona del cliente."><input className="input" autoFocus value={resp.contact_name} onChange={(e) => setResp({ ...resp, contact_name: e.target.value })} placeholder="Ana Mora (gerente)" /></Field>
            <Field label="Medio"><select className="select" value={resp.channel} onChange={(e) => setResp({ ...resp, channel: e.target.value })}>{["correo", "whatsapp", "firma", "portal", "telefono", "presencial"].map((c) => <option key={c} value={c}>{CANAL[c]}</option>)}</select></Field>
          </div>
          <Field label="Notas" hint="Opcional: condiciones, orden de compra, motivo del rechazo…"><textarea className="textarea" value={resp.notes} onChange={(e) => setResp({ ...resp, notes: e.target.value })} /></Field>
          {resp.status === "aceptada" && (info.has_unsent_changes || Number(resp.version) !== info.current_version) && (
            <p className="qv-warn">La cotización vuelve a quedar igual a la <b>v{resp.version}</b> (lo que el cliente aceptó). Los cambios posteriores se descartan.</p>
          )}
        </Modal>
      )}

      {mark && (
        <Modal title="Marcar como enviada" onClose={() => setMark(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setMark(null)}>Cancelar</button>
          <button className="btn btn--crimson" disabled={busy} onClick={saveMark}>Marcar enviada</button>
        </>}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>Para cuando la cotización salió por otro medio. Se guarda como {info?.has_unsent_changes || !info?.current_version ? `v${(info?.current_version || 0) + 1}` : `la misma v${info.current_version} (no cambió)`}.</p>
          <div className="grid-2">
            <Field label="Medio"><select className="select" value={mark.channel} onChange={(e) => setMark({ ...mark, channel: e.target.value })}><option value="whatsapp">WhatsApp</option><option value="presencial">En persona</option><option value="manual">Otro</option></select></Field>
            <Field label="A quién" hint="Teléfono, nombre o correo."><input className="input" value={mark.to} onChange={(e) => setMark({ ...mark, to: e.target.value })} placeholder={quote.customer_name || ""} /></Field>
          </div>
        </Modal>
      )}
    </div></div>
  );
}
