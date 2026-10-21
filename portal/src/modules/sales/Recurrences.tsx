/* Recurrencias: plantilla + frecuencia -> factura (ingreso) o gasto (egreso) automatico en la fecha (worker),
   o manual con "Generar ahora". Ej.: mantenimiento mensual a clientes, alquiler o internet de la oficina. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Customer, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Card, Empty, Field, I, Icon, Modal, Loading } from "../../ui/components";

type Rec = {
  id: number; name: string; kind: "factura" | "gasto"; customer_id: number | null; customer: string | null; frequency: string; next_date: string; end_date: string | null;
  auto_send: boolean; active: boolean; runs: number; last_invoice_id: number | null; last_expense_id: number | null; anchor_day: number | null; anchor_warning: string | null;
  /* plan 1.4: la primera factura espera "Aprobar y enviar"; resultado del ultimo envio */
  first_run_requires_approval: boolean; last_send_status: SendStatus | null; last_send_note: string | null;
  last_invoice: { id: number; number: string; status: string; sent_at: string | null; balance: string; currency: string } | null;
  /* mensualidad de un aliado: su plantilla la arma el plan; se edita desde la ficha del aliado */
  plan?: boolean; ally_id?: number | null;
  template: { lines?: { product_id: number | null; name?: string; quantity: string }[]; description?: string; subtotal?: string; tax_rate?: string; category_id?: number | null; supplier_id?: number | null; iva_credit?: string; bank_account_id?: number | null; currency?: string };
};
type Named = { id: number; name: string };
type SendStatus = "por_aprobar" | "enviando" | "pendiente" | "enviada" | "fallida" | "sin_correo";
const SEND: Record<SendStatus, { label: string; tone: string }> = {
  por_aprobar: { label: "por aprobar", tone: "warn" },
  enviando: { label: "envío sin confirmar", tone: "warn" },
  pendiente: { label: "pendiente de envío", tone: "muted" },
  enviada: { label: "enviada", tone: "ok" },
  fallida: { label: "fallida", tone: "bad" },
  sin_correo: { label: "sin correo del cliente", tone: "bad" },
};
type EditState = {
  id?: number; kind: "factura" | "gasto"; name: string; customer_id: string; frequency: string; next_date: string; end_date: string; auto_send: boolean; active: boolean; anchor_day: string; first_run_requires_approval: boolean;
  lines: { product_id: string; quantity: string }[];
  expense: { description: string; subtotal: string; tax_rate: string; category_id: string; supplier_id: string; iva_credit: string; bank_account_id: string; currency: string };
};

/* Gasto recurrente: por defecto sin credito (un hosting en dolares del exterior no trae factura electronica de CR; si el
   proveedor si la manda, el credito llega al vincular la recepcion con el gasto generado, sin duplicarlo). */
const blankExpense = { description: "", subtotal: "", tax_rate: "0", category_id: "", supplier_id: "", iva_credit: "no_credito", bank_account_id: "", currency: "CRC" };
const IVA_OPTS = [["credito", "01 · Con crédito"], ["bienes_capital", "03 · Bienes de capital"], ["no_credito", "04 · Sin crédito"], ["proporcional", "05 · Proporcional"]];

/** kind="gasto": la sub-pestana Contable -> Recurrentes (solo gastos, sin selector de tipo). Sin kind: ambos. */
export default function Recurrences({ kind: only }: { kind?: "factura" | "gasto" } = {}) {
  const { toast, allows } = useSession();
  const [itemsSt, setItems] = useState<Rec[] | null>(null); // null = cargando
  const items = itemsSt ?? [];
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [cats, setCats] = useState<Named[]>([]);
  const [suppliers, setSuppliers] = useState<Named[]>([]);
  const [banks, setBanks] = useState<Named[]>([]);
  const [kind, setKind] = useState<"" | "factura" | "gasto">(only || "");
  const [edit, setEdit] = useState<EditState | null>(null);
  const load = () => {
    api<Rec[]>(only ? `/recurrences?kind=${only}` : "/recurrences").then(setItems).catch(() => setItems([]));
    if (only !== "gasto") {
      api<{ items: Customer[] }>("/customers?limit=100").then((r) => setCustomers(r.items)).catch(() => setCustomers([]));
      api<{ items: Product[] }>("/products?limit=100").then((r) => setProducts(r.items)).catch(() => setProducts([]));
    }
    api<Named[]>("/expense-categories").then(setCats).catch(() => setCats([]));
    api<Named[]>("/suppliers").then(setSuppliers).catch(() => setSuppliers([]));
    api<Named[]>("/banking/accounts").then(setBanks).catch(() => setBanks([])); // contabilidad no entra a /settings
  };
  useEffect(load, []);

  const save = async () => {
    if (!edit) return;
    try {
      const { id, lines, expense, ...b } = edit;
      const template = b.kind === "factura"
        ? { lines: lines.filter((l) => l.product_id).map((l) => ({ product_id: Number(l.product_id), quantity: l.quantity || "1" })) }
        : { description: expense.description, subtotal: Number(expense.subtotal || 0), tax_rate: Number(expense.tax_rate || 0), iva_credit: expense.iva_credit, category_id: expense.category_id ? Number(expense.category_id) : null, supplier_id: expense.supplier_id ? Number(expense.supplier_id) : null, bank_account_id: expense.bank_account_id ? Number(expense.bank_account_id) : null, currency: expense.currency || "CRC" };
      const body = { ...b, customer_id: b.kind === "factura" && b.customer_id ? Number(b.customer_id) : null, end_date: b.end_date || null, anchor_day: b.anchor_day ? Number(b.anchor_day) : null, template };
      await api(id ? `/recurrences/${id}` : "/recurrences", { method: id ? "PUT" : "POST", json: body });
      toast("Recurrencia guardada"); setEdit(null); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const run = async (r: Rec) => {
    try {
      const x = await api<{ number?: string; expense_id?: number; total?: string; send_status?: SendStatus; sent?: boolean }>(`/recurrences/${r.id}/run`, { method: "POST" });
      toast(x.number ? `Factura ${x.number} generada${x.send_status ? ` · ${SEND[x.send_status].label}` : ""}` : `Gasto registrado por ${fmtMoney(x.total, r.template.currency || "CRC")}`, x.send_status === "fallida" || x.send_status === "sin_correo" ? "bad" : undefined); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const approve = async (r: Rec) => {
    if (!r.last_invoice || !confirm(`Le llega ${r.last_invoice.number} a ${r.customer || "el cliente"} con su PDF y enlace de pago. ¿Enviar?`)) return;
    try {
      const x = await api<{ sent: boolean; note: string | null; number: string }>(`/recurrences/${r.id}/approve-send`, { method: "POST" });
      toast(x.sent ? `${x.number} enviada; las siguientes salen solas` : x.note || "No salió el correo", x.sent ? undefined : "bad"); load();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const openEdit = (r: Rec) => setEdit({
    id: r.id, kind: r.kind, name: r.name, customer_id: String(r.customer_id ?? ""), frequency: r.frequency, next_date: r.next_date, end_date: r.end_date || "", auto_send: r.auto_send, active: r.active, anchor_day: String(r.anchor_day ?? ""), first_run_requires_approval: r.first_run_requires_approval,
    lines: (r.template.lines || []).map((l) => ({ product_id: String(l.product_id ?? ""), quantity: String(Number(l.quantity)) })),
    expense: r.kind === "gasto" ? { description: r.template.description || "", subtotal: String(Number(r.template.subtotal || 0)), tax_rate: String(Number(r.template.tax_rate ?? 13)), category_id: String(r.template.category_id ?? ""), supplier_id: String(r.template.supplier_id ?? ""), iva_credit: r.template.iva_credit || "credito", bank_account_id: String(r.template.bank_account_id ?? ""), currency: r.template.currency || "CRC" } : { ...blankExpense },
  });
  const newRec = (k: "factura" | "gasto") => setEdit({ kind: k, name: "", customer_id: "", frequency: "mensual", next_date: new Date().toISOString().slice(0, 10), end_date: "", auto_send: true, active: true, anchor_day: "", first_run_requires_approval: true, lines: [{ product_id: "", quantity: "1" }], expense: { ...blankExpense } });
  const shown = items.filter((r) => (!kind || r.kind === kind) && (!only || r.kind === only));
  const ex = edit?.expense;
  const exTotal = ex ? Number(ex.subtotal || 0) * (1 + Number(ex.tax_rate || 0) / 100) : 0;

  return (
    <>
      {only ? (
        <div style={{ display: "flex", justifyContent: "flex-end" }}><button className="btn btn--crimson btn--sm" onClick={() => newRec(only)}><Icon d={I.plus} />{only === "gasto" ? "Gasto recurrente" : "Factura recurrente"}</button></div>
      ) : (
        <div className="page-head">
          <div><div className="meta">02 · Facturación</div><h1 className="h1">Recurrencias</h1></div>
          <div className="page-head__actions">
            <button className="btn btn--soft" onClick={() => newRec("gasto")}><Icon d={I.accounting} />Gasto recurrente</button>
            <button className="btn btn--crimson" onClick={() => newRec("factura")}><Icon d={I.plus} />Factura recurrente</button>
          </div>
        </div>
      )}
      <Card flush>
        <div className="list-head">
          <span className="muted" style={{ fontSize: 13 }}>Se generan solas cada madrugada en la fecha programada.</span>
          {!only && <div className="tabs">{[["", "Todas"], ["factura", "Facturas"], ["gasto", "Gastos"]].map(([k, l]) => <button key={k} className={kind === k ? "is-active" : ""} onClick={() => setKind(k as typeof kind)}>{l}</button>)}</div>}
        </div>
        {itemsSt === null ? <Loading /> : shown.length === 0 ? <Empty hint="Contratos de mantenimiento, monitoreo mensual, alquiler, internet… se registran solos en la fecha programada." /> : (
          <table className="table"><thead><tr><th>Nombre</th><th>Tipo</th><th>Cliente / detalle</th><th>Frecuencia</th><th>Próxima</th><th className="num">Generadas</th><th>Última</th><th>Estado</th><th /></tr></thead>
            <tbody>{shown.map((r) => <tr key={r.id}>
              <td style={{ fontWeight: 600 }}>{r.name}</td>
              <td><span className={`badge ${r.kind === "gasto" ? "badge--warn" : "badge--info"}`}>{r.kind === "gasto" ? "Gasto" : "Factura"}</span></td>
              <td className="muted">{r.kind === "factura" ? r.customer : `${r.template.description || ""} · ${fmtMoney(Number(r.template.subtotal || 0) * (1 + Number(r.template.tax_rate || 0) / 100), r.template.currency || "CRC")}`}</td>
              <td className="muted" style={{ textTransform: "capitalize" }}>{r.frequency}{r.anchor_warning && <div className="meta" style={{ textTransform: "none", color: "var(--warn)" }}>{r.anchor_warning}</div>}</td><td className="muted">{fmtDate(r.next_date)}</td><td className="num mono">{r.runs}</td>
              <td>{r.last_invoice ? <>
                <Link className="row-link mono" to={`/facturas/${r.last_invoice.id}`}>{r.last_invoice.number}</Link>
                {r.last_send_status && <span className={`badge badge--${SEND[r.last_send_status]?.tone || "muted"}`} style={{ marginLeft: 6 }} title={r.last_send_note || undefined}>{SEND[r.last_send_status]?.label || r.last_send_status}</span>}
              </> : r.last_expense_id ? <Link className="row-link mono" to={`/contabilidad/contable?expense=${r.last_expense_id}`}>Gasto #{r.last_expense_id}</Link> : "—"}</td>
              <td><Badge status={r.active ? "confirmado" : "anulada"} /></td>
              <td className="num" style={{ whiteSpace: "nowrap" }}>{r.kind === "factura" && r.last_invoice && !r.last_invoice.sent_at && r.last_invoice.status !== "anulada" && Number(r.last_invoice.balance) > 0 && (r.last_send_status === "por_aprobar" || r.last_send_status === "enviando" || r.last_send_status === "fallida" || r.last_send_status === "sin_correo") && allows("sales.enviar") && <button className="btn btn--crimson btn--sm" onClick={() => approve(r)}>{r.last_send_status === "por_aprobar" ? "Aprobar y enviar" : "Reenviar"}</button>} <button className="btn btn--soft btn--sm" onClick={() => run(r)}>Generar ahora</button> {r.plan
                ? r.ally_id ? <Link className="btn btn--ghost btn--sm" to={`/aliados/${r.ally_id}`} title="La mensualidad se cambia desde la ficha del aliado">Ver aliado</Link> : null
                : <button className="btn btn--ghost btn--sm" onClick={() => openEdit(r)}>Ver</button>}</td>
            </tr>)}</tbody></table>
        )}
      </Card>
      {edit && (
        <Modal title={`${edit.id ? "" : "Nueva "}${edit.kind === "gasto" ? "recurrencia de gasto" : "recurrencia de factura"}`} onClose={() => setEdit(null)} wide foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Nombre"><input className="input" placeholder={edit.kind === "gasto" ? "Alquiler bodega" : "Monitoreo mensual"} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            {edit.kind === "factura"
              ? <Field label="Cliente"><select className="select" value={edit.customer_id} onChange={(e) => setEdit({ ...edit, customer_id: e.target.value })}><option value="">Seleccione…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
              : <Field label="Proveedor (opcional)"><select className="select" value={ex!.supplier_id} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, supplier_id: e.target.value } })}><option value="">—</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>}
            <Field label="Frecuencia"><select className="select" value={edit.frequency} onChange={(e) => setEdit({ ...edit, frequency: e.target.value })}><option value="semanal">Semanal</option><option value="quincenal">Quincenal</option><option value="mensual">Mensual</option><option value="anual">Anual</option></select></Field>
            <Field label="Próxima fecha"><input className="input" type="date" value={edit.next_date} onChange={(e) => setEdit({ ...edit, next_date: e.target.value, anchor_day: "" })} /></Field>
            <Field label="Fecha fin (opcional)"><input className="input" type="date" value={edit.end_date} onChange={(e) => setEdit({ ...edit, end_date: e.target.value })} /></Field>
            <Field label="Día ancla (opcional)" hint="Un 31 sigue siendo fin de mes en los meses cortos. Vacío = el día de la próxima fecha."><input className="input input--mono" inputMode="numeric" value={edit.anchor_day} onChange={(e) => setEdit({ ...edit, anchor_day: e.target.value.replace(/[^0-9]/g, "").slice(0, 2) })} /></Field>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, justifyContent: "flex-end", fontSize: 13 }}>
              {edit.kind === "factura" && <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={edit.auto_send} onChange={(e) => setEdit({ ...edit, auto_send: e.target.checked })} />Enviar al cliente al generar (PDF y enlace de pago)</label>}
              {/* quitar la revisión (que salgan solas) es de quien envía facturas; cambiar el cliente o el monto la vuelve a pedir */}
              {edit.kind === "factura" && edit.auto_send && <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={edit.first_run_requires_approval} disabled={!allows("sales.enviar") && edit.first_run_requires_approval && (items.find((x) => x.id === edit.id)?.first_run_requires_approval ?? true)} onChange={(e) => setEdit({ ...edit, first_run_requires_approval: e.target.checked })} />La próxima espera mi «Aprobar y enviar»; después salen solas</label>}
              {edit.kind === "factura" && edit.auto_send && edit.id && <p className="meta" style={{ margin: 0 }}>Si cambiás el cliente, el monto o la moneda, la próxima vuelve a esperar tu «Aprobar y enviar».</p>}
              <label style={{ display: "flex", gap: 10 }}><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Activa</label>
            </div>
          </div>
          {edit.kind === "factura" ? <>
            <div className="meta">Líneas de la plantilla</div>
            {edit.lines.map((l, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 100px 40px", gap: 8 }}>
                <select className="select" value={l.product_id} onChange={(e) => setEdit({ ...edit, lines: edit.lines.map((x, k) => (k === i ? { ...x, product_id: e.target.value } : x)) })}><option value="">Producto o servicio…</option>{products.map((p) => <option key={p.id} value={p.id}>{p.code} · {p.name}</option>)}</select>
                <input className="input input--mono" value={l.quantity} onChange={(e) => setEdit({ ...edit, lines: edit.lines.map((x, k) => (k === i ? { ...x, quantity: e.target.value } : x)) })} />
                <button className="x" onClick={() => setEdit({ ...edit, lines: edit.lines.filter((_, k) => k !== i) })}><Icon d={I.x} size={14} /></button>
              </div>
            ))}
            <button className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setEdit({ ...edit, lines: [...edit.lines, { product_id: "", quantity: "1" }] })}><Icon d={I.plus} />Línea</button>
          </> : <>
            <div className="meta">Gasto que se registra en cada fecha</div>
            <Field label="Descripción"><input className="input" value={ex!.description} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, description: e.target.value } })} /></Field>
            <div className="grid-3">
              <Field label="Divisa" hint="En dólares, cada gasto toma el tipo de cambio de su fecha."><select className="select" value={ex!.currency} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, currency: e.target.value } })}><option>CRC</option><option>USD</option></select></Field>
              <Field label="Subtotal (sin IVA)"><input className="input input--mono" value={ex!.subtotal} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, subtotal: e.target.value } })} /></Field>
              <Field label="IVA %"><select className="select" value={ex!.tax_rate} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, tax_rate: e.target.value } })}>{["13", "4", "2", "1", "0"].map((t) => <option key={t} value={t}>{t}%</option>)}</select></Field>
              <Field label="Total"><div className="input input--mono" style={{ display: "flex", alignItems: "center" }}>{fmtMoney(exTotal, ex!.currency || "CRC")}</div></Field>
              <Field label="Categoría"><select className="select" value={ex!.category_id} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, category_id: e.target.value } })}><option value="">—</option>{cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
              <Field label="Condición IVA" hint="Si el proveedor manda factura electrónica, el crédito llega al vincularla con el gasto."><select className="select" value={ex!.iva_credit} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, iva_credit: e.target.value } })}>{IVA_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
              <Field label="Cuenta bancaria"><select className="select" value={ex!.bank_account_id} onChange={(e) => setEdit({ ...edit, expense: { ...ex!, bank_account_id: e.target.value } })}><option value="">—</option>{banks.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
            </div>
          </>}
        </Modal>
      )}
    </>
  );
}
