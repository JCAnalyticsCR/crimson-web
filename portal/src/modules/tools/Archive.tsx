/* Archivo: "documentos archivados y proximos a borrar, para si hay errores" (Andres).
   Dos pestañas. Lo de la papelera muestra cuantos dias le quedan antes de que el worker lo borre. */
import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty } from "../../ui/components";

type Row = {
  kind: "survey" | "project" | "opportunity" | "work_order"; kind_label: string; id: number; number: string; title: string; status: string;
  archived_at: string | null; archived_by: string | null; trashed_at: string | null; trashed_by: string | null; purge_at: string | null; days_left: number | null;
};
type Resp = { purge_days: number; rows: Row[]; is_admin: boolean };

const KINDS: [string, string][] = [["", "Todo"], ["survey", "Levantamientos"], ["project", "Proyectos"], ["opportunity", "Oportunidades"], ["work_order", "Órdenes de trabajo"]];
const LINK: Record<Row["kind"], (id: number) => string> = {
  survey: (id) => `/levantamientos?id=${id}`,
  project: (id) => `/proyectos/${id}`,
  opportunity: (id) => `/oportunidades?id=${id}`,
  work_order: (id) => `/ordenes-trabajo?id=${id}`,
};
const when = (s: string | null) => (s ? parseTs(s).toLocaleDateString("es-CR", { day: "2-digit", month: "short", year: "numeric" }) : "—");

export default function Archive() {
  const { toast } = useSession();
  const [params, setParams] = useSearchParams();
  const state = params.get("tab") === "papelera" ? "papelera" : "archivado";
  const kind = params.get("tipo") || "";
  const [data, setData] = useState<Resp | null>(null);

  const load = useCallback(() => {
    const q = new URLSearchParams({ state });
    if (kind) q.set("kind", kind);
    api<Resp>(`/archive?${q}`).then(setData).catch((e) => { setData({ purge_days: 30, rows: [], is_admin: false }); toast(e.message, "bad"); });
  }, [state, kind, toast]);
  useEffect(() => { load(); }, [load]);

  const set = (k: string, v: string) => { const n = new URLSearchParams(params); if (v) n.set(k, v); else n.delete(k); setParams(n); };

  const act = async (r: Row, what: "restore" | "purge") => {
    if (what === "purge" && !window.confirm(`¿Borrar ${r.number} definitivamente? No se puede deshacer; queda registrado en la auditoría.`)) return;
    try {
      if (what === "restore") await api(`/archive/${r.kind}/${r.id}/restore`, { method: "POST" });
      else await api(`/archive/${r.kind}/${r.id}`, { method: "DELETE" });
      toast(what === "restore" ? `${r.number} restaurado` : `${r.number} borrado`);
      load();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
  };

  const rows = data?.rows || [];
  return (
    <>
      <div className="page-head">
        <div><div className="meta">Administración</div><h1 className="h1">Archivo</h1></div>
      </div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div className="tabs">
          <button className={state === "archivado" ? "is-active" : ""} onClick={() => set("tab", "")}>Archivados</button>
          <button className={state === "papelera" ? "is-active" : ""} onClick={() => set("tab", "papelera")}>Próximos a borrar</button>
        </div>
        <select className="select" style={{ width: "auto" }} value={kind} onChange={(e) => set("tipo", e.target.value)} aria-label="Tipo">
          {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      {state === "papelera" && data && (
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          Lo que está aquí se borra solo a los {data.purge_days} días de haberlo mandado a la papelera (se configura en Ajustes). Mientras tanto se puede restaurar.
        </p>
      )}
      <Card flush>
        {rows.length === 0 ? (
          <Empty title={state === "papelera" ? "La papelera está vacía" : "No hay nada archivado"} hint="Desde el detalle de un levantamiento, proyecto, oportunidad u orden de trabajo: Archivar o Mover a papelera." />
        ) : (
          <table className="table">
            <thead><tr><th>Tipo</th><th>Número</th><th>Descripción</th><th>{state === "papelera" ? "A la papelera" : "Archivado"}</th>{state === "papelera" && <th className="num">Se borra en</th>}<th /></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={`${r.kind}-${r.id}`}>
                <td className="muted">{r.kind_label}</td>
                <td className="mono"><Link to={LINK[r.kind](r.id)}>{r.number}</Link></td>
                <td style={{ fontWeight: 600 }}>{r.title || "—"} <span className="meta">· {r.status}</span></td>
                <td className="muted" style={{ fontSize: 13 }}>
                  {state === "papelera" ? `${when(r.trashed_at)}${r.trashed_by ? ` · ${r.trashed_by}` : ""}` : `${when(r.archived_at)}${r.archived_by ? ` · ${r.archived_by}` : ""}`}
                </td>
                {state === "papelera" && (
                  <td className="num"><span className={`badge badge--${(r.days_left ?? 99) <= 3 ? "bad" : "warn"}`}>{r.days_left === 0 ? "hoy" : `${r.days_left} día${r.days_left === 1 ? "" : "s"}`}</span></td>
                )}
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn--soft btn--sm" onClick={() => act(r, "restore")}>Restaurar</button>
                  {state === "papelera" && data?.is_admin && <button className="btn btn--ghost btn--sm" style={{ color: "var(--crimson, #e2233a)" }} onClick={() => act(r, "purge")}>Borrar ya</button>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}
