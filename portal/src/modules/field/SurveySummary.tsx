/* Levantamiento en resumen, para la vendedora (13/10 33:16-33:40): "podría ver un levantamiento como un resumen… y
   materiales sugeridos". Solo lectura y sin un solo costo: la API (GET /surveys/{id}/summary) no los manda.
   Mismo orden que el informe preliminar: notas, equipos y materiales, personal y puntos con fotos. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmtDate, openFile, type SurveySummary as Summary } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon, Loading, Modal } from "../../ui/components";
import { Lightbox, type Foto } from "../../ui/Lightbox";
import ScopeItems from "../crm/ScopeItems";
import "./survey-summary.css";

const PASOS = ["Levantamiento", "Revisión", "Costeo", "Cotización"];
const PASO_IDX: Record<Summary["paso"], number> = { borrador: 0, devuelto: 0, revision: 1, costeo: 2, cotizado: 3, cerrado: 3 };
const KIND: Record<string, string> = { equipo: "Equipo", material: "Material", servicio: "Servicio" };
const dias = (v: string) => Number(v).toLocaleString("es-CR", { maximumFractionDigits: 2 });

export default function SurveySummary({ id, onClose }: { id: number; onClose: () => void }) {
  const { toast, allows } = useSession();
  const nav = useNavigate();
  const [s, setS] = useState<Summary | null>(null);
  const [foto, setFoto] = useState<number | null>(null);

  useEffect(() => {
    api<Summary>(`/surveys/${id}/summary`).then(setS).catch((e) => { toast(e instanceof Error ? e.message : "No se pudo abrir", "bad"); onClose(); });
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const sendReport = async () => {
    const to = window.prompt("Correo del cliente (vacío = el de la ficha del cliente):", "");
    if (to === null) return;
    try {
      const r = await api<{ sent: boolean; to: string; note: string | null }>(`/surveys/${id}/report/email`, { method: "POST", json: { to: to.trim() || null } });
      toast(r.sent ? `Informe enviado a ${r.to}` : r.note || "El correo no salió", r.sent ? "ok" : "bad");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  // todas las fotos en un recorrido, cada una con su punto (mismo visor que el levantamiento)
  const galeria: Foto[] = s
    ? [...s.puntos.flatMap((p) => p.photos.map((url) => ({ url, caption: [p.code, p.label].filter(Boolean).join(" · ") }))), ...s.fotos.map((url) => ({ url, caption: "Foto general del sitio" }))]
    : [];
  const inicio = (i: number) => (s ? s.puntos.slice(0, i).reduce((n, p) => n + p.photos.length, 0) : 0);
  const paso = s ? PASO_IDX[s.paso] : 0;

  return (
    <Modal
      title={s ? `${s.number} · ${s.kind_label}` : "Levantamiento"}
      onClose={onClose}
      wide
      foot={<>
        <button className="btn btn--ghost" title="PDF para el cliente, sin precios ni costos" onClick={() => openFile(`/surveys/${id}/report.pdf`).catch((e) => toast(e.message, "bad"))}><Icon d={I.reports} />Informe preliminar (PDF)</button>
        {allows("sales.enviar") && <button className="btn btn--ghost" onClick={sendReport}>Enviar al cliente</button>}
        {s?.cotizacion?.id && <button className="btn btn--soft" onClick={() => nav(`/cotizaciones/${s.cotizacion!.id}`)}><Icon d={I.quote} />Ver cotización</button>}
        <button className="btn btn--crimson" onClick={onClose}>Cerrar</button>
      </>}
    >
      {!s ? <Loading /> : (
        <div className="ssum">
          <div className="ssum__head">
            <span><b>{s.customer || "Sin cliente"}</b>{s.site ? ` · ${s.site}` : ""}</span>
            <span className="muted">Visita {fmtDate(s.visit_date)} · Técnico: {s.technician || "—"}</span>
            {s.opportunity && <button className="btn btn--ghost btn--sm" onClick={() => nav(`/oportunidades?id=${s.opportunity!.id}`)}>{s.opportunity.number} · {s.opportunity.title}</button>}
          </div>

          <ol className="ssum-steps" aria-label="En qué va">
            {PASOS.map((p, i) => (
              <li key={p} className={i < paso ? "is-done" : i === paso ? (s.paso === "devuelto" ? "is-warn" : "is-now") : ""} aria-current={i === paso ? "step" : undefined}>
                <i aria-hidden>{i < paso ? "✓" : i + 1}</i><span>{p}</span>
              </li>
            ))}
          </ol>
          <p className="ssum__now">{s.paso_texto}{s.cotizacion && !s.cotizacion.id ? ` (${s.cotizacion.number})` : ""}</p>

          <div className="ssum-help">
            <b>¿Cómo llegan estos materiales a tu cotización?</b>
            <ol>
              <li>El técnico levanta los puntos y toca «Sugerir materiales»: el sistema propone, no agrega nada solo.</li>
              <li>Lo que el técnico elige pasa a «Equipos y materiales» de este levantamiento.</li>
              <li>El supervisor lo revisa y administración lo costea.</li>
              <li>La cotización sale con estas líneas a tu nombre y te llega un correo.</li>
            </ol>
          </div>

          {s.notes && <section><h4 className="ssum__h">Notas</h4><p className="ssum__notes">{s.notes}</p></section>}

          <section>
            <h4 className="ssum__h">Equipos y materiales · {s.equipos.length}</h4>
            {s.equipos.length === 0 ? <p className="muted" style={{ fontSize: 13, margin: 0 }}>El técnico todavía no agregó equipos ni materiales.</p> : (
              <table className="table">
                <thead><tr><th>Nombre</th><th className="num">Cantidad</th><th>Unidad</th><th>Tipo</th><th>Nota</th></tr></thead>
                <tbody>{s.equipos.map((x, i) => (
                  <tr key={i}><td style={{ fontWeight: 600 }}>{x.name}</td><td className="num mono">{x.quantity}</td><td className="muted">{x.unit}</td><td className="muted">{KIND[x.kind] || x.kind}</td><td className="muted">{x.note || "—"}</td></tr>
                ))}</tbody>
              </table>
            )}
          </section>

          {s.personal.length > 0 && (
            <section>
              <h4 className="ssum__h">Personal requerido</h4>
              <ul className="ssum__list">{s.personal.map((x) => <li key={x.label}>{x.label}: {x.people} {x.people === 1 ? "persona" : "personas"} × {dias(x.days)} {Number(x.days) === 1 ? "día" : "días"}{x.provider_name ? ` · aporta ${x.provider_name}` : ""}</li>)}</ul>
            </section>
          )}

          {/* revision 2 (A3): el mismo resumen de alcances que la oportunidad y su proyecto (solo lectura aquí) */}
          <ScopeItems url={`/surveys/${s.id}/scope`} canEdit={false} />

          {s.puntos.length > 0 && (
            <section>
              <h4 className="ssum__h">Puntos · {s.puntos.length}</h4>
              <div className="ssum-points">
                {s.puntos.map((p, i) => (
                  <div className="ssum-point" key={`${p.code}-${i}`}>
                    <div><b className="mono">{p.code}</b> <span className="muted">{p.kind_label}</span>{p.label ? ` · ${p.label}` : ""}</div>
                    {p.notes && <p className="muted" style={{ fontSize: 12.5, margin: "4px 0 0" }}>{p.notes}</p>}
                    {p.photos.length > 0 && (
                      <div className="ssum-thumbs">
                        {p.photos.map((url, j) => <button type="button" key={url} onClick={() => setFoto(inicio(i) + j)} aria-label={`Ver foto ${j + 1} de ${p.code}`}><img src={url} alt="" loading="lazy" /></button>)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
          {s.fotos.length > 0 && (
            <section>
              <h4 className="ssum__h">Fotos generales del sitio</h4>
              <div className="ssum-thumbs">{s.fotos.map((url, j) => <button type="button" key={url} onClick={() => setFoto(inicio(s.puntos.length) + j)} aria-label={`Ver foto general ${j + 1}`}><img src={url} alt="" loading="lazy" /></button>)}</div>
            </section>
          )}

          {s.sugerencias_pendientes.length > 0 && (
            <details className="ssum-sug">
              <summary>El sistema además sugiere ({s.sugerencias_pendientes.length}) · todavía no agregado por el técnico</summary>
              <table className="table">
                <thead><tr><th>Sugerencia</th><th className="num">Cantidad</th><th>Por qué</th></tr></thead>
                <tbody>{s.sugerencias_pendientes.map((x) => <tr key={x.name}><td style={{ fontWeight: 600 }}>{x.name}</td><td className="num mono">{x.quantity} <span className="muted">{x.unit}</span></td><td className="muted" style={{ fontSize: 12.5 }}>{x.reason}</td></tr>)}</tbody>
              </table>
            </details>
          )}
          {foto !== null && galeria.length > 0 && <Lightbox fotos={galeria} start={Math.min(foto, galeria.length - 1)} onClose={() => setFoto(null)} sign={false} />}
        </div>
      )}
    </Modal>
  );
}
