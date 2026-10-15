/* Inventarios -> Movimientos: kardex completo — quién movió qué, cuándo y desde qué documento.
   Filtros: búsqueda libre, producto (con autocompletar), bodega, tipo, usuario, fechas y limpiar.
   Muestra la columna "Saldo" solo cuando hay un producto seleccionado (backend la manda solo entonces). */
import { useEffect, useState } from "react";
import { api, parseTs } from "../../lib/api";
import { Card, Empty, Loading } from "../../ui/components";
import { Lookup, searchProducts } from "../../ui/Lookup";
import "./kardex.css";

type Movement = {
  id: number; at: string; product_id: number; code: string; name: string;
  warehouse_id: number; warehouse: string; kind: string; kind_label: string;
  movement_type: string | null; quantity: string; balance: string | null;
  reference: string | null; note: string | null; user_id: number | null; user: string;
  invoice_id: number | null; invoice_number: string | null;
  project_id: number | null; project_number: string | null;
  order_id: number | null; order_number: string | null;
  serial: string | null; unit_cost?: number;
};
export type KardexProduct = { id: number; code: string; name: string };
type WhLite = { id: number; name: string };
type UserRow = { id: number; name: string; email: string };

const KINDS: [string, string][] = [
  ["entrada", "Entrada"], ["salida", "Salida"], ["venta", "Venta"],
  ["devolucion", "Devolución"], ["ajuste", "Ajuste"],
  ["transferencia_out", "Traslado sale"], ["transferencia_in", "Traslado entra"],
];
const LIMIT = 100;
const n = (v: string | number | null | undefined) => Number(v ?? 0);
const fmtAt = (iso: string) => parseTs(iso).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const docLabel = (m: Movement) => {
  if (m.invoice_number) return `Factura ${m.invoice_number}`;
  if (m.project_number) return `Proyecto ${m.project_number}`;
  if (m.order_number) return `Pedido ${m.order_number}`;
  return m.reference;
};

export default function Kardex({ initProduct, whs }: { initProduct?: KardexProduct | null; whs: WhLite[] }) {
  const [product, setProduct] = useState<KardexProduct | null>(initProduct ?? null);
  const [prodText, setProdText] = useState(initProduct?.name ?? "");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [whId, setWhId] = useState("");
  const [kind, setKind] = useState("");
  const [userId, setUserId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<Movement[] | null>(null);
  const [total, setTotal] = useState(0);
  const [users, setUsers] = useState<UserRow[]>([]);

  const go0 = () => setOffset(0);

  // sync preselected product when parent changes (click "Movimientos" from existencias row)
  useEffect(() => {
    if (initProduct) { setProduct(initProduct); setProdText(initProduct.name); setOffset(0); }
  }, [initProduct?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // load users for filter (settings permission; silently skip if denied)
  useEffect(() => { api<{ users: UserRow[] }>("/settings/users").then((r) => setUsers(r.users)).catch(() => {}); }, []);

  // debounce free-text search
  useEffect(() => {
    const t = setTimeout(() => { setQ(qInput); setOffset(0); }, 300);
    return () => clearTimeout(t);
  }, [qInput]);

  // load movements whenever any filter or page changes
  useEffect(() => {
    setRows(null);
    const p = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
    if (q) p.set("q", q);
    if (product) p.set("product_id", String(product.id));
    if (whId) p.set("warehouse_id", whId);
    if (kind) p.set("kind", kind);
    if (userId) p.set("user_id", userId);
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    api<{ total: number; items: Movement[] }>(`/stock/movements?${p}`).then((r) => { setRows(r.items); setTotal(r.total); }).catch(() => setRows([]));
  }, [q, product?.id, whId, kind, userId, dateFrom, dateTo, offset]); // eslint-disable-line react-hooks/exhaustive-deps

  const clearAll = () => {
    setQInput(""); setQ(""); setProduct(null); setProdText("");
    setWhId(""); setKind(""); setUserId(""); setDateFrom(""); setDateTo(""); setOffset(0);
  };

  const items = rows ?? [];
  const entradas = items.reduce((a, m) => a + Math.max(0, n(m.quantity)), 0);
  const salidas = items.reduce((a, m) => a + Math.min(0, n(m.quantity)), 0);
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + LIMIT, total);
  const showBal = !!product;

  return (
    <>
      <div className="kdx-filters">
        <input className="input" style={{ flex: "1 1 160px", maxWidth: 220 }} placeholder="Buscar…" aria-label="Buscar movimientos" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        <Lookup
          value={prodText}
          placeholder="Producto…"
          fetcher={searchProducts}
          onSelect={(item, text) => {
            setProdText(text);
            if (item) { setProduct({ id: item.id, code: item.hint ?? "", name: item.label }); go0(); }
            else if (product !== null) { setProduct(null); go0(); }
          }}
        />
        <select className="select" style={{ flex: "1 1 130px", maxWidth: 180 }} aria-label="Bodega" value={whId} onChange={(e) => { setWhId(e.target.value); go0(); }}>
          <option value="">Todas las bodegas</option>{whs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <select className="select" style={{ flex: "1 1 130px", maxWidth: 180 }} aria-label="Tipo de movimiento" value={kind} onChange={(e) => { setKind(e.target.value); go0(); }}>
          <option value="">Todos los tipos</option>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {users.length > 0 && (
          <select className="select" style={{ flex: "1 1 130px", maxWidth: 180 }} aria-label="Usuario" value={userId} onChange={(e) => { setUserId(e.target.value); go0(); }}>
            <option value="">Todos los usuarios</option>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        )}
        <input className="input" type="date" aria-label="Desde" style={{ flex: "0 0 138px" }} value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); go0(); }} />
        <input className="input" type="date" aria-label="Hasta" style={{ flex: "0 0 138px" }} value={dateTo} onChange={(e) => { setDateTo(e.target.value); go0(); }} />
        <button type="button" className="btn btn--ghost btn--sm" onClick={clearAll}>Limpiar</button>
      </div>

      {rows !== null && rows.length > 0 && (
        <div className="kdx-chips">
          <span className="kdx-chip kdx-chip--ok">+{entradas.toLocaleString("es-CR")} entradas</span>
          <span className="kdx-chip kdx-chip--bad">{salidas.toLocaleString("es-CR")} salidas</span>
          <span className="kdx-chip">{from}–{to} de {total}</span>
        </div>
      )}

      <Card flush>
        {rows === null ? <Loading /> : rows.length === 0 ? <Empty hint="No hay movimientos con esos filtros." /> : (
          <div style={{ overflowX: "auto" }}>
            <table className="table kdx-table">
              <thead><tr>
                <th style={{ whiteSpace: "nowrap" }}>Fecha y hora</th>
                <th>Producto</th>
                <th>Bodega</th>
                <th>Tipo</th>
                <th className="num">Cantidad</th>
                {showBal && <th className="num">Saldo</th>}
                <th>Documento</th>
                <th>Usuario</th>
                <th>Nota</th>
              </tr></thead>
              <tbody>{rows.map((m) => {
                const qty = n(m.quantity);
                const d = docLabel(m);
                return (
                  <tr key={m.id}>
                    <td className="muted mono" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{fmtAt(m.at)}</td>
                    <td style={{ minWidth: 150 }}><b>{m.name}</b><div className="meta mono">{m.code}</div></td>
                    <td className="muted" style={{ whiteSpace: "nowrap" }}>{m.warehouse}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{m.kind_label}{m.movement_type && <div className="meta">{m.movement_type.replace(/_/g, " ")}</div>}</td>
                    <td className="num mono" style={{ fontWeight: 700, color: qty > 0 ? "var(--ok)" : qty < 0 ? "var(--bad)" : undefined }}>{qty > 0 ? `+${qty}` : qty}</td>
                    {showBal && <td className="num mono muted">{m.balance != null ? n(m.balance) : "—"}</td>}
                    <td style={{ minWidth: 120 }}>{d ? <span className="muted" style={{ fontSize: 13 }}>{d}</span> : <span className="muted">—</span>}{m.serial && <div className="meta mono">{m.serial}</div>}</td>
                    <td className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{m.user}</td>
                    <td className="muted kdx-note" title={m.note ?? undefined}>{m.note || "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </Card>

      {total > LIMIT && (
        <div className="kdx-pager">
          <span className="muted" style={{ fontSize: 13 }}>Mostrando {from}–{to} de {total}</span>
          <button className="btn btn--ghost btn--sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Anterior</button>
          <button className="btn btn--ghost btn--sm" disabled={to >= total} onClick={() => setOffset(offset + LIMIT)}>Siguiente</button>
        </div>
      )}
    </>
  );
}
