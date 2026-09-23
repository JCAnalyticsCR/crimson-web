/* Activos del cliente: que equipo quedo instalado, donde, con que serie y hasta cuando tiene garantia.
   Es lo que convierte una instalacion en mantenimiento recurrente: el sistema avisa antes de que venza. */
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtDate } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, I, Icon, Modal } from "../../ui/components";
import { Lookup, searchCustomers, searchProducts } from "../../ui/Lookup";

type Asset = {
  id: number; customer_id: number; customer: string | null; project_id: number | null; project: string | null; product_id: number | null;
  name: string; model: string | null; serial: string | null; location: string | null; ip: string | null; mac: string | null;
  firmware: string | null; installed_at: string | null; warranty_until: string | null; status: string; notes: string | null; warranty_days: number | null;
};

const blank = { customer_id: "", customer_name: "", project_id: "", product_id: "", product_name: "", name: "", model: "", serial: "", location: "", ip: "", mac: "", firmware: "", installed_at: "", warranty_until: "", notes: "" };
type Draft = typeof blank & { id?: number };

export default function Assets() {
  const { toast, allows } = useSession();
  const [params] = useSearchParams();
  const [rows, setRows] = useState<Asset[]>([]);
  const [q, setQ] = useState("");
  const [expiring, setExpiring] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);

  const proyecto = params.get("proyecto");
  const load = useCallback(() => {
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    if (expiring) qs.set("expiring", "true");
    api<Asset[]>(`/assets?${qs}`).then((r) => setRows(proyecto ? r.filter((a) => a.project_id === Number(proyecto)) : r));
  }, [q, expiring, proyecto]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);
  const save = async () => {
    if (!draft) return;
    const body = {
      customer_id: Number(draft.customer_id), project_id: draft.project_id ? Number(draft.project_id) : null,
      product_id: draft.product_id ? Number(draft.product_id) : null, name: draft.name, model: draft.model || null, serial: draft.serial || null,
      location: draft.location || null, ip: draft.ip || null, mac: draft.mac || null, firmware: draft.firmware || null,
      installed_at: draft.installed_at || null, warranty_until: draft.warranty_until || null, notes: draft.notes || null,
    };
    try { await api(draft.id ? `/assets/${draft.id}` : "/assets", { method: draft.id ? "PUT" : "POST", json: body }); toast("Activo guardado"); setDraft(null); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const garantia = (a: Asset) => {
    if (!a.warranty_until) return <span className="muted">—</span>;
    const d = a.warranty_days ?? 0;
    if (d < 0) return <span className="badge badge--muted">Vencida</span>;
    if (d <= 45) return <span className="badge badge--warn">Vence en {d} d</span>;
    return <span className="badge badge--ok">{fmtDate(a.warranty_until)}</span>;
  };

  return (
    <>
      <div className="page-head">
        <div><div className="meta">10 · Operación</div><h1 className="h1">Activos del cliente</h1></div>
        <div className="page-head__actions">
          {allows("assets.crear") && <button className="btn btn--crimson" onClick={() => setDraft({ ...blank, project_id: proyecto || "", installed_at: new Date().toISOString().slice(0, 10) })}><Icon d={I.plus} />Registrar equipo</button>}
        </div>
      </div>
      <Card flush>
        <div className="list-head">
          <div className="search" style={{ maxWidth: 380 }}><Icon d={I.search} size={16} /><input placeholder="Equipo, serie o ubicación…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={expiring} onChange={(e) => setExpiring(e.target.checked)} />Garantía por vencer</label>
          <button className="btn btn--ghost btn--sm" onClick={load}><Icon d={I.refresh} /></button>
        </div>
        {rows.length === 0 ? <Empty title="Sin equipos registrados" hint="Cada cámara, grabador o control de acceso instalado queda aquí con su serie y su garantía." /> : (
          <table className="table">
            <thead><tr><th>Equipo</th><th>Serie</th><th>Cliente</th><th>Ubicación</th><th>Proyecto</th><th>Instalado</th><th>Garantía</th><th /></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id}>
                <td style={{ fontWeight: 600 }}>{a.name}<br /><small className="muted">{a.model || ""}</small></td>
                <td className="mono muted">{a.serial || "—"}</td><td className="muted">{a.customer || "—"}</td><td className="muted">{a.location || "—"}</td>
                <td className="mono muted">{a.project || "—"}</td><td>{fmtDate(a.installed_at)}</td><td>{garantia(a)}</td>
                <td className="num">{allows("assets.editar") && <button className="btn btn--ghost btn--sm" onClick={() => setDraft({ id: a.id, customer_id: String(a.customer_id), customer_name: a.customer || "", project_id: a.project_id ? String(a.project_id) : "", product_id: a.product_id ? String(a.product_id) : "", product_name: "", name: a.name, model: a.model || "", serial: a.serial || "", location: a.location || "", ip: a.ip || "", mac: a.mac || "", firmware: a.firmware || "", installed_at: a.installed_at || "", warranty_until: a.warranty_until || "", notes: a.notes || "" })}>Editar</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>

      {draft && (
        <Modal title={draft.id ? "Editar equipo" : "Registrar equipo instalado"} onClose={() => setDraft(null)} wide foot={<>
          <button className="btn btn--ghost" onClick={() => setDraft(null)}>Cancelar</button>
          <button className="btn btn--crimson" onClick={save} disabled={!draft.customer_id || draft.name.trim().length < 2}>Guardar</button>
        </>}>
          <div className="grid-3">
            <Field label="Cliente"><Lookup value={draft.customer_name} placeholder="Buscar cliente…" fetcher={searchCustomers} onSelect={(it, text) => setDraft({ ...draft, customer_id: it ? String(it.id) : "", customer_name: text })} /></Field>
            <Field label="Producto del catálogo"><Lookup value={draft.product_name} placeholder="Buscar en el catálogo…" fetcher={searchProducts} onSelect={(pr, text) => setDraft({ ...draft, product_id: pr ? String(pr.id) : "", product_name: text, name: pr ? pr.label : draft.name })} /></Field>
            <Field label="Nombre del equipo"><input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Cámara entrada principal" /></Field>
            <Field label="Modelo"><input className="input" value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} placeholder="DS-2CD1047G3" /></Field>
            <Field label="Número de serie"><input className="input input--mono" style={{ textAlign: "left" }} value={draft.serial} onChange={(e) => setDraft({ ...draft, serial: e.target.value })} /></Field>
            <Field label="Ubicación"><input className="input" value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} placeholder="Portón norte, 4 m" /></Field>
            <Field label="Dirección IP"><input className="input input--mono" style={{ textAlign: "left" }} value={draft.ip} onChange={(e) => setDraft({ ...draft, ip: e.target.value })} /></Field>
            <Field label="MAC"><input className="input input--mono" style={{ textAlign: "left" }} value={draft.mac} onChange={(e) => setDraft({ ...draft, mac: e.target.value })} /></Field>
            <Field label="Firmware"><input className="input" value={draft.firmware} onChange={(e) => setDraft({ ...draft, firmware: e.target.value })} /></Field>
            <Field label="Instalado el"><input className="input" type="date" value={draft.installed_at} onChange={(e) => setDraft({ ...draft, installed_at: e.target.value })} /></Field>
            <Field label="Garantía hasta" hint="El sistema avisa 45 días antes."><input className="input" type="date" value={draft.warranty_until} onChange={(e) => setDraft({ ...draft, warranty_until: e.target.value })} /></Field>
          </div>
          <Field label="Notas"><textarea className="textarea" style={{ minHeight: 60 }} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} /></Field>
        </Modal>
      )}
    </>
  );
}
