/* Metas del equipo (ventas etapa 3): meta del mes contra lo vendido por vendedor, tasa de cierre, ticket promedio,
   embudo abierto y ranking. Lo ven y lo editan el administrador y el jefe de ventas (crm.cartera).
   «Vendido» = lo cobrado y confirmado en el mes, sin IVA y en colones (la misma base que la comisión por cobro): un
   cobro por confirmar no cuenta. Sin costos ni márgenes. Tablet 768 px: la tabla pasa a tarjetas (table--stack). */
import { useCallback, useEffect, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Loading } from "../../ui/components";

type Row = {
  id: number; name: string; ranking: number; meta: string | null; meta_propia: string | null; vendido: string; avance_pct: number | null; falta: string | null;
  tasa_cierre: number | null; ganadas: number; cerradas: number; ticket_promedio: string | null; facturas: number; embudo_abierto: string; oportunidades_abiertas: number;
};
type Board = { mes: string; vendedores: Row[]; total: { meta: string; vendido: string; avance_pct: number | null } };

const thisMonth = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" }).slice(0, 7);

export function Progress({ pct }: { pct: number | null }) {
  const v = Math.max(0, Math.min(100, pct ?? 0));
  return (
    <div style={{ height: 8, borderRadius: 99, background: "var(--bg-2)", overflow: "hidden" }} aria-label={`Avance ${pct ?? 0} %`}>
      <div style={{ width: `${v}%`, height: "100%", background: v >= 100 ? "var(--ok)" : "var(--crimson)" }} />
    </div>
  );
}

export default function Metas() {
  const { toast } = useSession();
  const [mes, setMes] = useState(thisMonth());
  const [b, setB] = useState<Board | null>(null);
  const [edit, setEdit] = useState<Record<number, string>>({});
  const load = useCallback(() => api<Board>(`/goals/board?month=${mes}`).then((r) => { setB(r); setEdit({}); }).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad")), [mes, toast]);
  useEffect(() => { load(); }, [load]);

  const save = async (r: Row) => {
    const v = Number(edit[r.id]);
    if (!(v >= 0)) { toast("Escriba un monto válido", "bad"); return; }
    try { await api("/goals", { method: "PUT", json: { user_id: r.id, month: mes, amount: v } }); toast(`Meta de ${r.name}: ${fmtMoney(v, "CRC")}`); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">Ventas</div><h1 className="h1">Metas del equipo</h1></div>
        <div className="page-head__actions"><input className="input" type="month" value={mes} onChange={(e) => setMes(e.target.value || thisMonth())} aria-label="Mes" /></div>
      </div>
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>«Vendido» es lo cobrado y confirmado en el mes, sin IVA y en colones: la misma base que las comisiones. Una meta sin cambiar se mantiene en los meses siguientes.</p>
      {!b ? <Loading /> : (
        <>
          <div className="kpi" style={{ marginBottom: 16 }}>
            <div className="kpi__card"><div className="kpi__label">Meta del equipo</div><div className="kpi__value money">{fmtMoney(b.total.meta, "CRC")}</div></div>
            <div className="kpi__card kpi__card--light"><div className="kpi__label">Vendido</div><div className="kpi__value money">{fmtMoney(b.total.vendido, "CRC")}</div><div className="kpi__sub">{b.total.avance_pct != null ? `${b.total.avance_pct} % de la meta` : "sin meta"}</div></div>
          </div>
          <Card flush>
            {b.vendedores.length === 0 ? <Empty title="Sin vendedores" hint="Asigne el rol Vendedor o Jefe de ventas en Ajustes → Usuarios." /> : (
              <table className="table table--stack">
                <thead><tr><th>#</th><th>Vendedor</th><th>Meta del mes</th><th style={{ textAlign: "right" }}>Vendido</th><th>Avance</th><th style={{ textAlign: "right" }}>Cierre</th><th style={{ textAlign: "right" }}>Ticket promedio</th><th style={{ textAlign: "right" }}>Embudo abierto</th></tr></thead>
                <tbody>{b.vendedores.map((r) => (
                  <tr key={r.id}>
                    <td data-label="Ranking"><b>{r.ranking}</b></td>
                    <td data-label="Vendedor"><b>{r.name}</b></td>
                    <td data-label="Meta del mes" className="stack-full">
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                        <input className="input" style={{ maxWidth: 160 }} type="number" min={0} step="50000" inputMode="numeric" placeholder={r.meta != null ? String(Number(r.meta)) : "Sin meta"} value={edit[r.id] ?? ""} onChange={(e) => setEdit({ ...edit, [r.id]: e.target.value })} />
                        {edit[r.id] !== undefined && edit[r.id] !== "" && <button className="btn btn--soft btn--sm" onClick={() => save(r)}>Guardar</button>}
                        {r.meta != null && r.meta_propia == null && <span className="muted" style={{ fontSize: 12 }}>(viene de un mes anterior o de Ajustes)</span>}
                      </div>
                    </td>
                    <td data-label="Vendido" style={{ textAlign: "right" }} className="money">{fmtMoney(r.vendido, "CRC")}</td>
                    <td data-label="Avance" style={{ minWidth: 120 }}><Progress pct={r.avance_pct} /><div className="meta" style={{ marginTop: 4 }}>{r.avance_pct != null ? `${r.avance_pct} %` : "sin meta"}</div></td>
                    <td data-label="Cierre" style={{ textAlign: "right" }}>{r.tasa_cierre != null ? `${r.tasa_cierre} % (${r.ganadas}/${r.cerradas})` : "—"}</td>
                    <td data-label="Ticket promedio" style={{ textAlign: "right" }} className="money">{r.ticket_promedio != null ? fmtMoney(r.ticket_promedio, "CRC") : "—"}</td>
                    <td data-label="Embudo abierto" style={{ textAlign: "right" }} className="money">{fmtMoney(r.embudo_abierto, "CRC")}<div className="meta">{r.oportunidades_abiertas} abiertas</div></td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>
        </>
      )}
    </>
  );
}
