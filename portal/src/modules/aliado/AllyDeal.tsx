/* Revisión 2 en el portal del aliado: solicitudes de precio (la PETICIÓN de Crimson separada de SU oferta), alcances y
   responsables (confirma y avanza los suyos) y acuerdos (los suyos con versiones; de los demás, solo los montos
   acordados que Crimson comparte; el desglose de otros solo con costos abiertos). Todo viene filtrado por el servidor. */
import { useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { ESTADO_PRECIO, errorText, fmtWhen, type AllyDeals, type AllyScope, type AllyScopeItem, type OfferLine, type Req } from "./shared";

const SCOPE_TONE: Record<string, string> = { por_confirmar: "warn", confirmado: "info", en_curso: "info", completado: "ok" };
const NEXT: Record<string, { status: string; label: string }[]> = {
  por_confirmar: [{ status: "confirmado", label: "Confirmar mi alcance" }],
  confirmado: [{ status: "en_curso", label: "En curso" }, { status: "completado", label: "Completado" }],
  en_curso: [{ status: "completado", label: "Completado" }],
};
const qty = (q: string | null, u: string | null) => (q ? `${Number(q).toLocaleString("es-CR")} ${u || ""}`.trim() : "");

function Lines({ lines, currency }: { lines: OfferLine[]; currency: string }) {
  return (
    <ul style={{ margin: "4px 0", paddingLeft: 18, fontSize: 13 }}>
      {lines.map((l, i) => <li key={i}>{l.concept}: {Number(l.quantity).toLocaleString("es-CR")} {l.unit || ""} × {fmtMoney(l.unit_price, currency)}</li>)}
    </ul>
  );
}

/** Solicitudes de precio. Abrir una la marca "vista" (GET /aliado/solicitudes/{id}). */
export function RequestList({ rows, onRespond }: { rows: Req[]; onRespond?: (r: Req) => void }) {
  const [open, setOpen] = useState<Record<number, Req>>({});
  const { toast } = useSession();
  if (!rows.length) return <p className="muted">Crimson no le ha pedido precios aquí.</p>;
  const toggle = async (r: Req) => {
    if (open[r.id]) { const n = { ...open }; delete n[r.id]; setOpen(n); return; }
    try { const d = await api<Req>(`/aliado/solicitudes/${r.id}`); setOpen({ ...open, [r.id]: d }); }
    catch (e) { toast(errorText(e), "bad"); }
  };
  return (
    <div className="cp-list">
      {rows.map((row) => {
        const r = open[row.id] || row;
        const est = ESTADO_PRECIO[r.status];
        return (
          <div key={r.id} className="cp-row al-req">
            <div className="cp-row__main">
              <b>{r.what}</b>
              <div className="cp-row__meta">
                {r.source === "aliado" ? "Enviado por usted" : `Pedido por Crimson${r.responsible ? ` · seguimiento: ${r.responsible}` : ""}`}
                {r.due_date ? ` · responder antes del ${fmtDate(r.due_date)}` : ""}
                {r.amount != null ? ` · su oferta: ${fmtMoney(r.amount, r.currency)}` : ""}
                {r.responded_at ? ` · respondida el ${fmtDate(r.responded_at)}` : ""}
                {r.agreement_version ? ` · acuerdo versión ${r.agreement_version}` : ""}
              </div>
              {open[r.id] && (
                <div className="al-req__detail">
                  {r.source !== "aliado" && (
                    <div className="al-req__block">
                      <span className="meta">Lo que Crimson pide</span>
                      {r.scope && <p>Alcance: {r.scope}</p>}
                      {(r.req_lines || []).length > 0 && <ul>{(r.req_lines || []).map((l, i) => <li key={i}>{l.concept}{l.quantity ? ` · ${qty(l.quantity, l.unit)}` : ""}</li>)}</ul>}
                      {r.site && <p>Sitio: {r.site}</p>}
                      {r.inclusions && <p>Incluye: {r.inclusions}</p>}
                      {r.req_exclusions && <p>No incluye: {r.req_exclusions}</p>}
                      {(r.req_attachments || []).length > 0 && <p>{(r.req_attachments || []).map((x) => <a key={x.id} href={x.url} target="_blank" rel="noreferrer" style={{ marginRight: 10 }}>{x.filename}</a>)}</p>}
                      {!r.scope && !(r.req_lines || []).length && !r.site && !r.inclusions && !r.req_exclusions && <p className="muted">Sin más detalle que el título.</p>}
                    </div>
                  )}
                  {r.amount != null && (
                    <div className="al-req__block">
                      <span className="meta">Su oferta</span>
                      <p><b>{fmtMoney(r.amount, r.currency)}</b> sin IVA{r.valid_until ? ` · vigente hasta ${fmtDate(r.valid_until)}` : ""}</p>
                      {(r.lines || []).length > 0 && <Lines lines={r.lines} currency={r.currency} />}
                      {r.conditions && <p>Condiciones: {r.conditions}</p>}
                      {r.exclusions && <p>Sus exclusiones: {r.exclusions}</p>}
                      {(r.attachments || []).length > 0 && <p>{r.attachments.map((x) => <a key={x.id} href={x.url} target="_blank" rel="noreferrer" style={{ marginRight: 10 }}>{x.filename}</a>)}</p>}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="cp-row__side">
              <span className={`badge badge--${est?.tone || "muted"}`}>{est?.label || r.status_label || r.status}</span>
              <button className="btn btn--ghost btn--sm" onClick={() => toggle(row)}>{open[r.id] ? "Ocultar" : "Ver solicitud"}</button>
              {r.can_respond && onRespond && <button className="btn btn--soft btn--sm" onClick={() => onRespond(r)}>{r.status === "respondido" ? "Corregir oferta" : "Enviar mi oferta"}</button>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Alcances y responsables: los suyos con acciones; los compartidos de otros, solo lectura. */
export function AllyScopeList({ scope, onChange, readOnly }: { scope: AllyScope | undefined; onChange: (s: AllyScope) => void; readOnly: boolean }) {
  const { toast } = useSession();
  const [busy, setBusy] = useState<number | null>(null);
  if (!scope || scope.items.length === 0) return <p className="muted">Crimson todavía no definió los alcances de esta obra.</p>;
  const act = async (it: AllyScopeItem, status: string) => {
    const note = status === "confirmado" ? window.prompt("¿Algún comentario sobre su alcance? (opcional)", "") : "";
    if (note === null) return;
    setBusy(it.id);
    try {
      const s = await api<AllyScope>(`/aliado/alcances/${it.id}`, { method: "POST", json: { status, note: note?.trim() || null } });
      onChange(s);
      toast(status === "confirmado" ? "Alcance confirmado: Crimson ya lo ve" : "Avance registrado");
    } catch (e) { toast(errorText(e), "bad"); }
    finally { setBusy(null); }
  };
  const s = scope.summary;
  return (
    <>
      <p className="meta" style={{ margin: "0 0 6px" }}>{s.total} partida(s) · {s.completados} completada(s){s.por_estado.por_confirmar ? ` · ${s.por_estado.por_confirmar} por confirmar` : ""}</p>
      <div className="cp-list">
        {scope.items.map((it) => (
          <div key={it.id} className={`cp-row${it.mine ? " al-scope--mine" : ""}`}>
            <div className="cp-row__main">
              <b>{it.kind_label} · {it.description}</b>
              <div className="cp-row__meta">
                {qty(it.quantity, it.unit) && `${qty(it.quantity, it.unit)} · `}Suministra: {it.supplier.name} · Ejecuta: {it.executor.name}{it.mine ? " · le toca a su empresa" : ""}
              </div>
              {it.notes && <div className="cp-row__meta" style={{ whiteSpace: "pre-wrap" }}>{it.notes}</div>}
            </div>
            <div className="cp-row__side">
              <span className={`badge badge--${SCOPE_TONE[it.status] || "muted"}`}>{it.status_label}</span>
              {it.can_act && !readOnly && (NEXT[it.status] || []).map((n) => (
                <button key={n.status} className={`btn btn--sm ${n.status === "confirmado" ? "btn--crimson" : "btn--soft"}`} disabled={busy === it.id} onClick={() => act(it, n.status)}>{n.label}</button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/** Acuerdos: los suyos (con versiones), los montos acordados de los demás y, con costos abiertos, sus ofertas originales. */
export function AllyAgreements({ deals }: { deals: AllyDeals | undefined }) {
  if (!deals || (!deals.mine.length && !deals.shared.length && !deals.open_offers.length)) return <p className="muted">Todavía no hay acuerdos aceptados.</p>;
  return (
    <div className="cp-grid cp-grid--2">
      <div className="cp-card">
        <b>Mis acuerdos con Crimson</b>
        {deals.mine.length === 0 ? <p className="muted">Ninguno todavía.</p> : deals.mine.map((g) => (
          <div key={g.request_id} style={{ marginTop: 10 }}>
            <div><b>{g.what}</b> <span className={`badge badge--${g.status === "vigente" ? "ok" : "muted"}`}>{g.status === "vigente" ? `Vigente · versión ${g.version}` : g.status}</span></div>
            <div className="meta">{fmtMoney(g.amount, g.currency)} sin IVA{g.valid_until ? ` · vigente hasta ${fmtDate(g.valid_until)}` : ""}</div>
            {g.conditions && <div className="meta">Condiciones: {g.conditions}</div>}
            {g.exclusions && <div className="meta">Exclusiones: {g.exclusions}</div>}
            {(g.versions || []).length > 1 && (
              <details style={{ marginTop: 4 }}>
                <summary className="meta" style={{ cursor: "pointer" }}>Historial ({(g.versions || []).length} versiones)</summary>
                <ol style={{ margin: "4px 0", paddingLeft: 18, fontSize: 13 }}>
                  {(g.versions || []).map((v) => <li key={v.id}>v{v.version} · {fmtMoney(v.amount, v.currency)} · {fmtWhen(v.created_at)} · {v.changes.join(" · ") || "—"}{v.note ? ` · ${v.note}` : ""}</li>)}
                </ol>
              </details>
            )}
          </div>
        ))}
      </div>
      <div className="cp-card">
        <b>Montos acordados del presupuesto común</b>
        <p className="meta" style={{ margin: "4px 0 0" }}>Lo que Crimson acordó con los demás participantes y comparte con usted.{deals.open_costs ? " Esta oportunidad trabaja con costos abiertos." : ""}</p>
        {deals.shared.length === 0 ? <p className="muted">Nada compartido todavía.</p> : deals.shared.map((g, i) => (
          <div key={i} style={{ marginTop: 8 }}>
            <b>{g.ally}</b> · {g.what}
            <div className="meta">{fmtMoney(g.amount, g.currency)} sin IVA{g.valid_until ? ` · vigente hasta ${fmtDate(g.valid_until)}` : ""}</div>
            {g.lines && g.lines.length > 0 && <Lines lines={g.lines} currency={g.currency} />}
          </div>
        ))}
        {deals.open_costs && deals.open_offers.length > 0 && (
          <>
            <b style={{ display: "block", marginTop: 12 }}>Ofertas originales (costos abiertos)</b>
            {deals.open_offers.map((g, i) => (
              <div key={i} style={{ marginTop: 6 }}>
                <span>{g.ally} · {g.what}</span>
                <div className="meta">{fmtMoney(g.amount, g.currency)} · {ESTADO_PRECIO[g.status]?.label || g.status}</div>
                {g.lines.length > 0 && <Lines lines={g.lines} currency={g.currency} />}
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
