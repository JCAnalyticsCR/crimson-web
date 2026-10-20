/* Inventario web: lo publicado en la tienda contra el inventario real (bodegas web - reservas), lo reservado
   por pedidos, bajo pedido y agotado. Alertas de publicados sin stock ni proveedor y ajustes de la tienda.
   La disponibilidad es la de la funcion unica del API (services/availability): los mismos estados en todo lado. */
import { useEffect, useState } from "react";
import { api, fmtDate, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Loading, Modal } from "../../ui/components";

type Row = {
  id: number; code: string; name: string; show_on_web: boolean; web_featured: boolean; web_price: string | null; price: string | null; currency: string; serialized: boolean;
  estado: string; mensaje: string; propio: string; reservado: string; reservado_pedidos: string; libre: string;
  proveedor: number | null; proveedor_al: string | null; proveedor_viejo: boolean; proveedor_nombre?: string | null; proveedor_hace?: string | null;
  proveedor_sin_vincular?: number | null; max_qty?: number | null; alerta: boolean; avisos: number;
};
type WhRow = { id: number; name: string; is_default: boolean; web: boolean };
type Settings = {
  enabled: boolean; warehouse_ids: number[]; quantity_display: string; low_threshold: number; backorder_enabled: boolean;
  backorder_lead: string; supplier_max_age_hours: number; out_of_stock: string; out_of_stock_action: string;
  hold_hours_manual: number; hold_minutes_card: number; cancel_unpaid_on_expiry: boolean; warehouses: WhRow[];
};
type Hold = { id: number; order_id: number | null; order: string | null; order_status: string | null; product_id: number; name: string; warehouse: string; quantity: string; expires_at: string | null };
type Summary = { publicados: number; en_bodega: number; bajo_pedido: number; consultar: number; agotado: number; alertas: number; reservado_pedidos: string; avisos: number };
type Data = { summary: Summary; items: Row[]; settings: Settings; holds: Hold[] };

const ESTADO: Record<string, { label: string; tone: string }> = {
  en_bodega: { label: "En bodega", tone: "ok" }, bajo_pedido: { label: "Bajo pedido", tone: "warn" },
  consultar: { label: "Consultar", tone: "warn" }, agotado: { label: "Agotado", tone: "bad" },
};
const n = (v: string | number | null | undefined) => Number(v ?? 0);

export default function WebInventory() {
  const { toast, allows } = useSession();
  const [data, setData] = useState<Data | null>(null);
  const [estado, setEstado] = useState("");
  const [q, setQ] = useState("");
  const [todos, setTodos] = useState(false);
  const [edit, setEdit] = useState<{ row: Row; web_price: string; web_featured: boolean; show_on_web: boolean } | null>(null);
  const [cfg, setCfg] = useState<Settings | null>(null);
  const canEdit = allows("catalog.editar");
  // el tecnico y el aliado ven cantidades, nunca precios (13/10, 22:07): la API los manda vacios y la columna no sale
  const verPrecio = allows("catalog.precios");
  const canConfig = allows("settings.configurar");

  const load = () => {
    const p = new URLSearchParams();
    if (estado) p.set("estado", estado);
    if (q.trim()) p.set("q", q.trim());
    if (todos) p.set("todos", "true");
    api<Data>(`/store/inventory?${p}`).then((d) => { setData(d); setCfg((c) => c ?? d.settings); }).catch((e) => { toast(e instanceof Error ? e.message : "Error", "bad"); setData(null); });
  };
  useEffect(load, [estado, todos]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveProduct = async () => {
    if (!edit) return;
    try {
      await api(`/store/inventory/products/${edit.row.id}`, { method: "PATCH", json: { web_price: edit.web_price.trim() ? edit.web_price.trim() : null, web_featured: edit.web_featured, show_on_web: edit.show_on_web } });
      toast("Publicación actualizada"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const saveCfg = async () => {
    if (!cfg) return;
    const { warehouses, ...all } = cfg;
    void warehouses;
    // solo los ajustes vigentes (los obsoletos enforce_stock / sell_when_unknown / dias ya no se mandan)
    const rest = Object.fromEntries(Object.entries(all).filter(([k]) => !["enforce_stock", "sell_when_unknown", "supplier_max_age_days"].includes(k)));
    try {
      const r = await api<Settings>("/store/inventory/settings", { method: "PUT", json: rest });
      setCfg(r); toast("Ajustes de la tienda guardados"); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  if (!data) return <Loading />;
  const s = data.summary;
  const pill = (k: string, label: string, v: number | string, hot = false) => (
    <div key={k} className={estado === k || hot ? "hot" : ""} onClick={() => setEstado(estado === k ? "" : k)} style={{ cursor: "pointer" }}><b>{v}</b><span>{label}</span></div>
  );
  const wh = (id: number, on: boolean) => {
    if (!cfg) return;
    const base = cfg.warehouse_ids.length ? cfg.warehouse_ids : cfg.warehouses.map((w) => w.id);
    const next = on ? Array.from(new Set([...base, id])) : base.filter((x) => x !== id);
    setCfg({ ...cfg, warehouse_ids: next.length === cfg.warehouses.length ? [] : next });
  };
  const whOn = (id: number) => !cfg || !cfg.warehouse_ids.length || cfg.warehouse_ids.includes(id);

  return (
    <>
      <span className="muted" style={{ fontSize: 13, maxWidth: 760 }}>
        Disponible web = existencias en las bodegas de venta que alimentan la tienda menos lo reservado (proyectos, bodega y pedidos web), más lo que el proveedor confirma con dato vigente como «bajo pedido». Un pedido web reserva solo; si no se paga a tiempo, la reserva vence y se libera. Al despachar o facturar sale del inventario una sola vez.
      </span>
      <div className="pending">
        {pill("", "Publicados", s.publicados)}
        {pill("en_bodega", "En bodega", s.en_bodega)}
        {pill("bajo_pedido", "Bajo pedido", s.bajo_pedido)}
        {pill("consultar", "Proveedor sin dato reciente", s.consultar)}
        {pill("agotado", "Agotados", s.agotado)}
        {pill("reservado", "Reservado por pedidos", n(s.reservado_pedidos))}
        {pill("alertas", "Alertas", s.alertas, s.alertas > 0)}
      </div>
      {data.settings.enabled && (
        <div className="list-filter card" role="status">
          <span>En línea solo se vende lo disponible: lo que no hay en bodega ni confirma el proveedor con dato vigente (o pasa de lo que confirma) no se puede comprar y la tienda ofrece <b>«Consultar por WhatsApp»</b>.</span>
        </div>
      )}

      <Card
        title={estado === "alertas" ? "Publicados sin stock ni proveedor" : "Inventario de la tienda"}
        flush
        extra={
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <input className="input" style={{ width: 200 }} placeholder="Buscar código o nombre" aria-label="Buscar" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={todos} onChange={(e) => setTodos(e.target.checked)} />Incluir no publicados</label>
          </div>
        }
      >
        {data.items.length === 0 ? <Empty hint={estado === "alertas" ? "Todo lo publicado tiene existencia o proveedor que lo respalde." : "No hay productos con ese filtro."} /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr><th>Producto</th><th>Estado en la tienda</th><th className="num">En bodegas web</th><th className="num" title="Reservado por pedidos web (y total reservado)">Reservado</th><th className="num">Libre</th><th>Proveedor</th>{verPrecio && <th className="num">Precio web</th>}<th /></tr></thead>
              <tbody>
                {data.items.map((r) => {
                  const st = ESTADO[r.estado] || { label: r.estado, tone: "muted" };
                  return (
                    <tr key={r.id} style={r.alerta ? { background: "var(--bad-soft, transparent)" } : undefined}>
                      <td style={{ minWidth: 180 }}>
                        <b>{r.name}</b>
                        {r.web_featured && <span className="badge badge--info" style={{ marginLeft: 6 }}>Destacado</span>}
                        {!r.show_on_web && <span className="badge badge--muted" style={{ marginLeft: 6 }}>No publicado</span>}
                        {r.serialized && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Serie</span>}
                        <div className="meta mono">{r.code}{r.avisos > 0 ? ` · ${r.avisos} esperan aviso` : ""}</div>
                      </td>
                      <td><span className={`badge badge--${st.tone}`}>{st.label}</span><div className="meta">{r.mensaje}</div></td>
                      <td className="num mono">{n(r.propio)}</td>
                      <td className="num mono" style={{ color: n(r.reservado_pedidos) > 0 ? "var(--warn)" : "var(--text-3)" }}>{n(r.reservado_pedidos) || "—"}{n(r.reservado) > n(r.reservado_pedidos) && <div className="meta">total {n(r.reservado)}</div>}</td>
                      <td className="num mono" style={{ fontWeight: 700 }}>{n(r.libre)}</td>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>{r.proveedor != null ? <>{r.proveedor_nombre ? `${r.proveedor_nombre}: ` : ""}{r.proveedor} u.<div className="meta" style={{ color: r.proveedor_viejo ? "var(--bad)" : undefined }} title={r.proveedor_al ? fmtDate(r.proveedor_al.slice(0, 10)) : undefined}>{r.proveedor_al ? `actualizado ${r.proveedor_hace || fmtDate(r.proveedor_al.slice(0, 10))}` : "sin fecha"}{r.proveedor_viejo ? " · dato viejo" : ""}</div></> : r.proveedor_sin_vincular ? <span title="Tiene proveedor pero ningún ítem del catálogo vinculado: su existencia no cuenta">sin vincular</span> : "—"}</td>
                      {verPrecio && <td className="num mono">{r.web_price != null ? fmtMoney(r.web_price, r.currency) : <span className="muted">{r.price != null ? fmtMoney(r.price, r.currency) : "—"}</span>}</td>}
                      <td className="num">{canEdit && <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ row: r, web_price: r.web_price != null ? String(n(r.web_price)) : "", web_featured: r.web_featured, show_on_web: r.show_on_web })}>Publicación</button>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={`Reservado por pedidos web · ${data.holds.length}`} flush>
        {data.holds.length === 0 ? <Empty title="Sin reservas de pedidos" hint="Cada pedido de la tienda reserva su mercadería hasta que se paga y se despacha." /> : (
          <table className="table">
            <thead><tr><th>Pedido</th><th>Producto</th><th>Bodega</th><th className="num">Cantidad</th><th>Vence</th></tr></thead>
            <tbody>{data.holds.map((h) => <tr key={h.id}><td className="mono">{h.order ?? "—"}<div className="meta">{h.order_status}</div></td><td>{h.name}</td><td className="muted">{h.warehouse}</td><td className="num mono">{n(h.quantity)}</td><td className="muted" style={{ whiteSpace: "nowrap" }}>{h.expires_at ? new Date(h.expires_at).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "Pagado: hasta despachar"}</td></tr>)}</tbody>
          </table>
        )}
      </Card>

      {cfg && (
        <Card title="Ajustes de la tienda · inventario">
          <div className="grid-2">
            <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} />Reservar inventario al recibir pedidos web (apagado: la tienda vende sin control de inventario)</label>
          </div>
          <Field label="Bodegas que alimentan la web" hint="Todas marcadas = todas las activas (también las nuevas).">
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>{cfg.warehouses.map((w) => <label key={w.id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={whOn(w.id)} onChange={(e) => wh(w.id, e.target.checked)} />{w.name}{w.is_default ? " (predeterminada)" : ""}</label>)}</div>
          </Field>
          <div className="grid-3">
            <Field label="Cantidad en la tienda"><select className="select" value={cfg.quantity_display} onChange={(e) => setCfg({ ...cfg, quantity_display: e.target.value })}><option value="exacta">Número exacto</option><option value="umbral">«Últimas unidades» bajo el umbral</option><option value="oculta">Solo «Disponible»</option></select></Field>
            <Field label="Umbral de últimas unidades"><input className="input input--mono" type="number" min={0} value={cfg.low_threshold} onChange={(e) => setCfg({ ...cfg, low_threshold: Number(e.target.value) })} /></Field>
            <Field label="Agotados"><select className="select" value={cfg.out_of_stock} onChange={(e) => setCfg({ ...cfg, out_of_stock: e.target.value })}><option value="mostrar">Mostrar como agotado</option><option value="ocultar">Ocultar del catálogo</option></select></Field>
            <Field label="Botón en agotados"><select className="select" value={cfg.out_of_stock_action} onChange={(e) => setCfg({ ...cfg, out_of_stock_action: e.target.value })}><option value="whatsapp">Consultar por WhatsApp</option><option value="avisarme">Avisarme cuando haya</option><option value="ninguna">Ninguno</option></select></Field>
            <Field label="Plazo bajo pedido"><input className="input" value={cfg.backorder_lead} onChange={(e) => setCfg({ ...cfg, backorder_lead: e.target.value })} /></Field>
            <Field label="Dato del proveedor vigente (horas)" hint="Eurocomp se refresca cada 6 h. Más viejo: «Consultar disponibilidad» y no se vende en línea."><input className="input input--mono" type="number" min={1} value={cfg.supplier_max_age_hours} onChange={(e) => setCfg({ ...cfg, supplier_max_age_hours: Number(e.target.value) })} /></Field>
            <Field label="Reserva sin pago: SINPE / transferencia (horas)"><input className="input input--mono" type="number" min={1} value={cfg.hold_hours_manual} onChange={(e) => setCfg({ ...cfg, hold_hours_manual: Number(e.target.value) })} /></Field>
            <Field label="Reserva sin pago: tarjeta (minutos)"><input className="input input--mono" type="number" min={5} value={cfg.hold_minutes_card} onChange={(e) => setCfg({ ...cfg, hold_minutes_card: Number(e.target.value) })} /></Field>
          </div>
          <div className="grid-2">
            <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={cfg.backorder_enabled} onChange={(e) => setCfg({ ...cfg, backorder_enabled: e.target.checked })} />Vender bajo pedido lo que tiene el proveedor (genera solicitud de compra)</label>
            <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={cfg.cancel_unpaid_on_expiry} onChange={(e) => setCfg({ ...cfg, cancel_unpaid_on_expiry: e.target.checked })} /><span>Cancelar el pedido cuando vence el plazo para pagar<small className="muted" style={{ display: "block", fontSize: 12 }}>Con tarjeta, solo si ONVO confirma que no cobró; con comprobante en revisión, nunca. El cliente recibe un correo para rehacer la compra.</small></span></label>
          </div>
          {canConfig && <div><button className="btn btn--crimson btn--sm" onClick={saveCfg}>Guardar ajustes</button></div>}
        </Card>
      )}

      {edit && (
        <Modal title={`Publicación · ${edit.row.name}`} onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={saveProduct}>Guardar</button></>}>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={edit.show_on_web} onChange={(e) => setEdit({ ...edit, show_on_web: e.target.checked })} />Publicado en la tienda</label>
          <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={edit.web_featured} onChange={(e) => setEdit({ ...edit, web_featured: e.target.checked })} />Destacado</label>
          <Field label={`Precio web sin IVA (${edit.row.currency})`} hint={`Vacío: el precio de siempre (${fmtMoney(edit.row.price, edit.row.currency)}).`}>
            <input className="input input--mono" inputMode="decimal" value={edit.web_price} onChange={(e) => setEdit({ ...edit, web_price: e.target.value })} />
          </Field>
        </Modal>
      )}
    </>
  );
}
