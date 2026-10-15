/* Comisiones (economia). Andres: "la mayoria de mis negocios se van a basar en comisionar" (22/09) y, el 13/10,
   reglas por vendedor y por aliado, aprobacion manual, lo que recibe cada aliado y "mis numeros".
   Las pestañas dependen de quien entra: el vendedor ve lo suyo en tiempo real; contabilidad, la bandeja; el admin,
   todo. Los aliados no entran aqui: ven su dinero en su portal (/aliado, "Mis ganancias"). */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty } from "../../ui/components";
import AllyEarnings from "./AllyEarnings";
import Config from "./Config";
import Inbox from "./Inbox";
import Ledger from "./Ledger";
import MyCommissions from "./MyCommissions";
import Numbers from "./Numbers";
import Rules from "./Rules";
import { errText } from "./types";
import "./commissions.css";

type Tab = { key: string; label: string; show: boolean };
type AllyRow = { ally_company_id: number; name: string; active: boolean; proyectos: number; recibido: string; por_recibir: string; proyectado: string; ultimo_movimiento: string | null };

function Allies({ seesCosts }: { seesCosts: boolean }) {
  const { toast } = useSession();
  const [rows, setRows] = useState<AllyRow[] | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  const load = useCallback(() => { api<AllyRow[]>("/commissions/allies").then(setRows).catch((e) => toast(errText(e), "bad")); }, [toast]);
  useEffect(() => { load(); }, [load]);
  if (!rows) return <div style={{ padding: 40, textAlign: "center" }}><span className="spinner" /></div>;
  if (sel) {
    const a = rows.find((r) => r.ally_company_id === sel);
    return (
      <>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
          <button className="btn btn--ghost btn--sm" onClick={() => setSel(null)}>← Todos los aliados</button>
          <select className="select select--sm" style={{ width: "auto" }} aria-label="Aliado" value={sel} onChange={(e) => setSel(Number(e.target.value))}>{rows.map((r) => <option key={r.ally_company_id} value={r.ally_company_id}>{r.name}</option>)}</select>
          <b>{a?.name}</b>
        </div>
        <AllyEarnings base={`/commissions/earnings?ally_company_id=${sel}`} projectBase={(pid) => `/commissions/earnings/projects/${pid}?ally_company_id=${sel}`} />
        {seesCosts && <Numbers allyId={sel} />}
      </>
    );
  }
  return (
    <Card title="Aliados" flush extra={<span className="meta">lo que recibe cada empresa aliada</span>}>
      {rows.length === 0 ? <Empty title="Todavía no hay empresas aliadas" hint="Se crean en Administración → Aliados." /> : (
        <div className="com-scroll">
          <table className="table">
            <thead><tr><th>Aliado</th><th className="num">Proyectos</th><th className="num">Recibido</th><th className="num">Por recibir</th><th className="num">Proyectado</th><th>Último movimiento</th><th /></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.ally_company_id}>
                <td style={{ fontWeight: 600 }}>{r.name}{!r.active && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Inactivo</span>}</td>
                <td className="num">{r.proyectos}</td><td className="num money">{fmtMoney(r.recibido)}</td><td className="num money">{fmtMoney(r.por_recibir)}</td><td className="num money">{fmtMoney(r.proyectado)}</td>
                <td className="muted">{fmtDate(r.ultimo_movimiento)}</td>
                <td className="num"><button className="btn btn--ghost btn--sm" onClick={() => setSel(r.ally_company_id)}>Abrir</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export default function Commissions() {
  const { allows } = useSession();
  const [params, setParams] = useSearchParams();
  const verTodo = allows("commissions.ver_todo");
  const aprueba = allows("commissions.aprobar");
  const configura = allows("commissions.configurar");
  const costos = allows("catalog.costos");
  const tabs: Tab[] = useMemo(() => [
    { key: "mias", label: "Mis comisiones", show: !verTodo },
    { key: "bandeja", label: "Bandeja", show: aprueba },
    { key: "movimientos", label: "Movimientos", show: verTodo },
    { key: "reglas", label: "Reglas", show: verTodo },
    { key: "aliados", label: "Aliados", show: verTodo },
    { key: "numeros", label: "Mis números", show: verTodo && costos },
    { key: "config", label: "Configuración", show: configura },
  ].filter((t) => t.show), [verTodo, aprueba, configura, costos]);
  // ?estado= (la bandeja de Aprobaciones) abre Movimientos con ese filtro
  const pedido = params.get("tab") || (params.get("estado") && verTodo ? "movimientos" : "");
  const tab = tabs.find((t) => t.key === pedido)?.key || tabs[0]?.key || "mias";
  const go = (k: string) => { const p = new URLSearchParams(params); p.set("tab", k); p.delete("estado"); setParams(p, { replace: true }); };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">14 · Administración</div><h1 className="h1">Comisiones</h1></div>
      </div>
      {tabs.length > 1 && <div className="tabs com-tabs" role="tablist">{tabs.map((t) => <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "is-active" : ""} onClick={() => go(t.key)}>{t.label}</button>)}</div>}
      {tab === "mias" && <MyCommissions />}
      {tab === "bandeja" && <Inbox />}
      {tab === "movimientos" && <Ledger />}
      {tab === "reglas" && <Rules />}
      {tab === "aliados" && <Allies seesCosts={costos} />}
      {tab === "numeros" && <Numbers />}
      {tab === "config" && <Config />}
    </>
  );
}
