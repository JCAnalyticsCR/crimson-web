/* Ventas por asignar (bandeja de ventas-asignar, ventas etapa 3): facturas sin vendedor (tienda, caja, recurrencias)
   y consultas de la tienda sin responsable. Se asigna un vendedor (sugerido: el vendedor de la cuenta) o se marca
   «venta de la casa» (sin comisión). Solo para quien tiene sales.asignar_venta: el admin y, si Ajustes → Ventas lo
   habilita, el jefe de ventas. En el Inicio sale compacta; la página completa es /ventas/por-asignar.
   Tablet 768 px: las tablas pasan a tarjetas (table--stack). */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Loading } from "../../ui/components";

type Fac = { id: number; number: string; customer_name: string | null; total: string; currency: string; issue_date: string | null; origen: string; items: string[]; suggested_seller_id: number | null };
type Con = { id: number; number: string; title: string; customer_name: string | null; contact_name: string | null; amount: string | null; currency: string | null; antigüedad_dias: number | null; suggested_seller_id: number | null };
type Resp = { facturas: Fac[]; consultas: Con[]; equipo: { id: number; name: string }[] };

const ORIGEN: Record<string, string> = { web: "Tienda web", recurrencia: "Recurrencia", panel: "Panel o caja" };

export default function PorAsignar({ compact = false }: { compact?: boolean }) {
  const { toast } = useSession();
  const [d, setD] = useState<Resp | null>(null);
  const [sel, setSel] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => api<Resp>("/sales/unassigned").then((r) => {
    setD(r);
    const s: Record<string, string> = {};
    r.facturas.forEach((f) => { if (f.suggested_seller_id) s[`f${f.id}`] = String(f.suggested_seller_id); });
    r.consultas.forEach((c) => { if (c.suggested_seller_id) s[`c${c.id}`] = String(c.suggested_seller_id); });
    setSel((x) => ({ ...s, ...x }));
  }).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad")), [toast]);
  useEffect(() => { load(); }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(key);
    try { await fn(); toast(ok); load(); } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(null); }
  };
  const asignarFactura = (f: Fac) => run(`f${f.id}`, () => api(`/invoices/${f.id}/assign-seller`, { method: "POST", json: { seller_id: Number(sel[`f${f.id}`]) } }), `${f.number} asignada`);
  const casa = (f: Fac) => { if (confirm(`¿Marcar ${f.number} como venta de la casa? No genera comisión para nadie.`)) run(`f${f.id}`, () => api(`/invoices/${f.id}/assign-seller`, { method: "POST", json: { house_sale: true } }), `${f.number}: venta de la casa`); };
  const asignarConsulta = (c: Con) => run(`c${c.id}`, () => api(`/opportunities/${c.id}/assign-owner`, { method: "POST", json: { owner_id: Number(sel[`c${c.id}`]) } }), `${c.number} asignada`);

  if (!d) return compact ? null : <Loading />;
  const total = d.facturas.length + d.consultas.length;
  if (compact && total === 0) return null;
  const fac = compact ? d.facturas.slice(0, 5) : d.facturas;
  const con = compact ? d.consultas.slice(0, 5) : d.consultas;
  const selector = (key: string) => (
    <select className="select" value={sel[key] || ""} onChange={(e) => setSel({ ...sel, [key]: e.target.value })} aria-label="Vendedor">
      <option value="">Elija vendedor…</option>
      {d.equipo.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
    </select>
  );

  const body = (
    <>
      <Card title={`Facturas sin vendedor · ${d.facturas.length}`} flush extra={compact && d.facturas.length > 5 ? <Link className="btn btn--ghost btn--sm" to="/ventas/por-asignar">Ver todas</Link> : undefined}>
        {fac.length === 0 ? <Empty title="Nada por asignar" hint="Las ventas que entran sin vendedor aparecen aquí." /> : (
          <table className="table table--sm table--stack">
            <thead><tr><th>Factura</th><th>Cliente</th><th>Origen</th><th style={{ textAlign: "right" }}>Total</th><th>Vendedor</th><th /></tr></thead>
            <tbody>{fac.map((f) => (
              <tr key={f.id}>
                <td data-label="Factura"><Link to={`/facturas/${f.id}`}><b className="mono">{f.number}</b></Link><div className="meta">{f.issue_date ? fmtDate(f.issue_date) : ""}</div></td>
                <td data-label="Cliente">{f.customer_name || "—"}{f.items.length > 0 && <div className="muted" style={{ fontSize: 12 }}>{f.items.join(", ")}</div>}</td>
                <td data-label="Origen">{ORIGEN[f.origen] || f.origen}</td>
                <td data-label="Total" style={{ textAlign: "right" }} className="money">{fmtMoney(f.total, f.currency)}</td>
                <td data-label="Vendedor" className="stack-full">{selector(`f${f.id}`)}</td>
                <td className="stack-actions" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn--crimson btn--sm" disabled={busy === `f${f.id}` || !sel[`f${f.id}`]} onClick={() => asignarFactura(f)}>Asignar</button>{" "}
                  <button className="btn btn--ghost btn--sm" disabled={busy === `f${f.id}`} onClick={() => casa(f)}>Venta de la casa</button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      <Card title={`Consultas sin responsable · ${d.consultas.length}`} flush extra={compact && d.consultas.length > 5 ? <Link className="btn btn--ghost btn--sm" to="/ventas/por-asignar">Ver todas</Link> : undefined}>
        {con.length === 0 ? <Empty title="Nada por asignar" hint="Las consultas de la tienda sin vendedor aparecen aquí." /> : (
          <table className="table table--sm table--stack">
            <thead><tr><th>Consulta</th><th>Cliente</th><th>Días</th><th>Responsable</th><th /></tr></thead>
            <tbody>{con.map((c) => (
              <tr key={c.id}>
                <td data-label="Consulta"><Link to={`/oportunidades?id=${c.id}`}><b className="mono">{c.number}</b></Link><div className="muted" style={{ fontSize: 12 }}>{c.title}</div></td>
                <td data-label="Cliente">{c.customer_name || c.contact_name || "—"}</td>
                <td data-label="Días">{c.antigüedad_dias ?? "—"}</td>
                <td data-label="Responsable" className="stack-full">{selector(`c${c.id}`)}</td>
                <td className="stack-actions"><button className="btn btn--crimson btn--sm" disabled={busy === `c${c.id}` || !sel[`c${c.id}`]} onClick={() => asignarConsulta(c)}>Asignar</button></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
  if (compact) return <><div className="meta" style={{ marginTop: 6 }}>Ventas por asignar · {total}</div>{body}</>;
  return (
    <>
      <div className="page-head"><div><div className="meta">Ventas</div><h1 className="h1">Ventas por asignar</h1></div></div>
      <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>Elija el vendedor que atendió cada venta (se sugiere el vendedor de la cuenta) o márquela como venta de la casa. Al asignar, el vendedor recibe un aviso y se le devengan las comisiones de lo ya cobrado.</p>
      {body}
    </>
  );
}
