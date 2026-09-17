/* Editor de documento (cotizacion o factura): contenido + columna derecha fija con totales/acciones/tipo de cambio.
   Los totales se recalculan en vivo contra /documents/preview (misma logica que el guardado). */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, fmtMoney, type Customer, type Invoice, type Line, type Product, type Quote } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Field, I, Icon, Modal } from "../../ui/components";

type Preview = { subtotal: string; discount_total: string; tax_total: string; total: string; lines: { subtotal: string; tax_amount: string; total: string; unit_price: string; name: string; tax_rate: string }[] };
const num = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? String(Number(n.toFixed(5))) : String(v ?? ""); };
const blankLine = (): Line => ({ product_id: null, code: null, name: "", description: "", unit: "Unid", quantity: "1", unit_price: "0", discount_type: "percent", discount_value: "0", tax_rate: "13" });

export default function DocEditor({ kind }: { kind: "quote" | "invoice" }) {
  const { id } = useParams();
  const nav = useNavigate();
  const { toast, me } = useSession();
  const isNew = !id || id === "nueva";
  const isQ = kind === "quote";
  const [doc, setDoc] = useState<Partial<Quote & Invoice>>({ currency: "CRC", discount_type: "percent", discount_value: "0", status: "creado", lines: [] });
  const [lines, setLines] = useState<Line[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [prev, setPrev] = useState<Preview | null>(null);
  const [pick, setPick] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fx, setFx] = useState<{ sell: string; buy: string } | null>(null);
  const locked = !isNew && (doc.status === "convertida" || doc.status === "anulada" || (kind === "invoice" && ((doc as Invoice).payments?.length ?? 0) > 0));

  useEffect(() => {
    api<{ items: Customer[] }>("/customers?limit=100").then((r) => setCustomers(r.items));
    api<{ sell: string; buy: string } | null>("/fx/today").then((r) => r && setFx(r)).catch(() => {});
    if (!isNew) api<Quote & Invoice>(`/${isQ ? "quotes" : "invoices"}/${id}`).then((d) => { setDoc({ ...d, discount_value: num(d.discount_value) }); setLines(d.lines.map((l) => ({ ...l, quantity: num(l.quantity), unit_price: num(l.unit_price), discount_value: num(l.discount_value), tax_rate: num(l.tax_rate) }))); });
  }, [id, isNew, isQ]);

  const payload = useMemo(() => ({
    customer_id: doc.customer_id ?? null, currency: doc.currency || "CRC", discount_type: doc.discount_type || "percent", discount_value: doc.discount_value || "0",
    internal_notes: doc.internal_notes || null, external_notes: doc.external_notes || null, external_order: doc.external_order || null, activity_code: doc.activity_code || null,
    medical_exemption_card: !!doc.medical_exemption_card, issue_date: doc.issue_date || null, due_date: doc.due_date || null,
    lines: lines.filter((l) => l.name.trim()).map((l) => ({ product_id: l.product_id, code: l.code, name: l.name, description: l.description, unit: l.unit, quantity: l.quantity || "1", unit_price: l.unit_price || "0", discount_type: l.discount_type, discount_value: l.discount_value || "0", tax_rate: l.tax_rate || "13" })),
  }), [doc, lines]);

  useEffect(() => {
    const t = setTimeout(() => { api<Preview>("/documents/preview", { method: "POST", json: payload }).then(setPrev).catch(() => {}); }, 250);
    return () => clearTimeout(t);
  }, [payload]);

  const setLine = (i: number, patch: Partial<Line>) => setLines((ls) => ls.map((l, k) => (k === i ? { ...l, ...patch } : l)));
  const addProducts = (ps: Product[]) => { setLines((ls) => [...ls, ...ps.map((p) => ({ ...blankLine(), product_id: p.id, code: p.code, name: p.name, unit: p.unit, unit_price: String(p.price), tax_rate: String(p.tax_rate ?? 13) }))]); setPick(false); };

  const save = useCallback(async (then?: "send" | "convert") => {
    if (payload.lines.length === 0) return toast("Agregá al menos una línea", "bad");
    setBusy(true);
    try {
      const path = isQ ? "quotes" : "invoices";
      const saved = isNew ? await api<Quote & Invoice>(`/${path}`, { method: "POST", json: payload }) : await api<Quote & Invoice>(`/${path}/${id}`, { method: "PUT", json: payload });
      if (then === "send") await api(`/${path}/${saved.id}/send`, { method: "POST" });
      if (then === "convert") { const inv = await api<Invoice>(`/quotes/${saved.id}/convert`, { method: "POST" }); toast(`Factura ${inv.number} creada`); nav(`/facturas/${inv.id}`); return; }
      toast(`${isQ ? "Cotización" : "Factura"} ${saved.number} guardada`);
      nav(`/${isQ ? "cotizaciones" : "facturas"}/${saved.id}`, { replace: true });
      if (!isNew) setDoc(saved);
    } catch (e) { toast(e instanceof Error ? e.message : "Error al guardar", "bad"); }
    finally { setBusy(false); }
  }, [payload, isQ, isNew, id, nav, toast]);

  const act = async (action: "void" | "duplicate") => {
    const path = isQ ? "quotes" : "invoices";
    if (action === "void" && !confirm(`¿Anular ${isQ ? "la cotización" : "la factura"} ${doc.number}? Esta acción no se deshace.`)) return;
    try {
      const r = await api<Quote>(`/${path}/${id}/${action}`, { method: "POST" });
      if (action === "duplicate") { toast(`Duplicada como ${r.number}`); nav(`/cotizaciones/${r.id}`); } else { setDoc(r); toast("Documento anulado"); }
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const cur = doc.currency || "CRC";
  const title = isNew ? (isQ ? "Nueva cotización" : "Nueva factura") : `${isQ ? "Cotización" : "Factura"}: ${doc.number}`;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="status-line"><span>{isQ ? "02 · Cotización" : "03 · Factura"}</span>{doc.status && <Badge status={doc.status} />}{(doc as Invoice).consecutive && <span>· {(doc as Invoice).consecutive}</span>}</div>
          <h1 className="h1" style={{ marginTop: 6 }}>{title}</h1>
        </div>
        <div className="page-head__actions">
          {!isNew && <button className="btn btn--ghost btn--sm" onClick={() => window.print()}>Imprimir</button>}
          {!isNew && isQ && <button className="btn btn--ghost btn--sm" onClick={() => act("duplicate")}>Duplicar</button>}
        </div>
      </div>

      <div className="doc">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card"><div className="card__body grid-3">
            <Field label="Cliente">
              <select className="select" value={doc.customer_id ?? ""} disabled={locked} onChange={(e) => setDoc({ ...doc, customer_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Seleccione…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
            <Field label="Divisa" hint={fx ? `Tipo de cambio hoy: venta ${fx.sell} · compra ${fx.buy}` : undefined}>
              <select className="select" value={cur} disabled={locked} onChange={(e) => setDoc({ ...doc, currency: e.target.value })}><option>CRC</option><option>USD</option></select>
            </Field>
            <Field label="Grupo de facturación"><select className="select" disabled><option>{isQ ? "COT · Cotizaciones" : "FEC · Facturas electrónicas"}</option></select></Field>
          </div></div>

          <div className="card">
            <div className="card__head"><h3 className="h3">Productos & Servicios</h3><button className="btn btn--soft btn--sm" disabled={locked} onClick={() => setPick(true)}><Icon d={I.plus} />Agregar Productos & Servicios</button></div>
            <div className="card__body" style={{ padding: 0 }}>
              <table className="lines">
                <thead><tr><th style={{ width: "42%" }}>Concepto</th><th style={{ width: 80 }}>Cant.</th><th style={{ width: 130 }}>Precio</th><th style={{ width: 90 }}>Desc. %</th><th style={{ width: 90 }}>IVA %</th><th className="num">Subtotal</th><th /></tr></thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={i}>
                      <td>
                        <input className="input line-name" placeholder="Nombre del producto o servicio" value={l.name} disabled={locked} onChange={(e) => setLine(i, { name: e.target.value })} style={{ height: 34, marginBottom: 4 }} />
                        <textarea className="line-desc" rows={1} placeholder="Descripción (click para editar)" value={l.description || ""} disabled={locked} onChange={(e) => setLine(i, { description: e.target.value })} />
                        {l.code && <span className="meta">{l.code}</span>}
                      </td>
                      <td><input className="input input--mono" value={l.quantity} disabled={locked} onChange={(e) => setLine(i, { quantity: e.target.value })} /></td>
                      <td><input className="input input--mono" value={l.unit_price} disabled={locked} onChange={(e) => setLine(i, { unit_price: e.target.value })} /></td>
                      <td><input className="input input--mono" value={l.discount_value} disabled={locked} onChange={(e) => setLine(i, { discount_value: e.target.value })} /></td>
                      <td><select className="select" value={l.tax_rate} disabled={locked} onChange={(e) => setLine(i, { tax_rate: e.target.value })} style={{ height: 34, padding: "0 8px" }}>{["13", "4", "2", "1", "0"].map((r) => <option key={r} value={r}>{r}%</option>)}</select></td>
                      <td className="num money" style={{ paddingTop: 14 }}>{prev?.lines[i] ? fmtMoney(prev.lines[i].subtotal, cur) : "—"}</td>
                      <td>{!locked && <button className="x" onClick={() => setLines((ls) => ls.filter((_, k) => k !== i))} aria-label="Eliminar"><Icon d={I.x} size={14} /></button>}</td>
                    </tr>
                  ))}
                  {lines.length === 0 && <tr><td colSpan={7}><div className="empty" style={{ padding: 28 }}><span className="meta">Sin líneas</span><div className="h3">Agregá productos o escribí una línea libre</div><button className="btn btn--ghost btn--sm" onClick={() => setLines([blankLine()])}>Línea libre</button></div></td></tr>}
                </tbody>
              </table>
              {lines.length > 0 && !locked && <div style={{ padding: 10 }}><button className="btn btn--ghost btn--sm" onClick={() => setLines((ls) => [...ls, blankLine()])}><Icon d={I.plus} />Línea libre</button></div>}
            </div>
          </div>

          <div className="grid-2">
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field label="Notas internas" hint="No salen en el documento."><textarea className="textarea" value={doc.internal_notes || ""} disabled={locked} onChange={(e) => setDoc({ ...doc, internal_notes: e.target.value })} /></Field>
              <Field label="Notas externas" hint="Se imprimen y se envían al cliente."><textarea className="textarea" value={doc.external_notes || ""} disabled={locked} onChange={(e) => setDoc({ ...doc, external_notes: e.target.value })} /></Field>
            </div></div>
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="meta">Otros ajustes</div>
              <Field label="Orden externa #"><input className="input" value={doc.external_order || ""} disabled={locked} onChange={(e) => setDoc({ ...doc, external_order: e.target.value })} /></Field>
              <Field label="Código de actividad"><select className="select" value={doc.activity_code || ""} disabled={locked} onChange={(e) => setDoc({ ...doc, activity_code: e.target.value })}><option value="">Seleccione…</option><option>6202.0</option><option>4322.0</option></select></Field>
              <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={!!doc.medical_exemption_card} disabled={locked} onChange={(e) => setDoc({ ...doc, medical_exemption_card: e.target.checked })} />Exoneración médica IVA (tarjeta de crédito)</label>
            </div></div>
          </div>
        </div>

        <aside className="doc__side">
          <div className="card"><div className="card__body">
            <div className="meta" style={{ marginBottom: 10 }}>Totales · {cur}</div>
            <div className="totals">
              <div><span>Subtotal</span><span className="money">{fmtMoney(prev?.subtotal, cur)}</span></div>
              <div><span>Descuento</span><span className="money">−{fmtMoney(prev?.discount_total, cur)}</span></div>
              <div><span>Impuestos</span><span className="money">{fmtMoney(prev?.tax_total, cur)}</span></div>
              <div className="total"><span>Total</span><span className="money">{fmtMoney(prev?.total, cur)}</span></div>
              {kind === "invoice" && !isNew && <div className="total" style={{ color: "var(--crimson)" }}><span>Saldo</span><span className="money">{fmtMoney((doc as Invoice).balance, cur)}</span></div>}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 14 }}>
              <Field label="Descuento global"><select className="select" value={doc.discount_type} disabled={locked} onChange={(e) => setDoc({ ...doc, discount_type: e.target.value })}><option value="percent">%</option><option value="amount">Monto</option></select></Field>
              <Field label="Valor"><input className="input input--mono" value={doc.discount_value ?? "0"} disabled={locked} onChange={(e) => setDoc({ ...doc, discount_value: e.target.value })} /></Field>
            </div>
          </div></div>

          {!locked && (
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <button className="btn btn--crimson" disabled={busy} onClick={() => save()}><Icon d={I.check} />Guardar</button>
              <button className="btn" disabled={busy} onClick={() => save("send")}><Icon d={I.whatsapp} />Guardar & enviar al cliente</button>
              {isQ && <button className="btn btn--soft" disabled={busy} onClick={() => save("convert")}><Icon d={I.invoice} />Convertir a factura</button>}
              {!isNew && <button className="btn btn--danger" disabled={busy} onClick={() => act("void")}>Anular {isQ ? "cotización" : "factura"}</button>}
            </div></div>
          )}
          {locked && doc.status === "convertida" && (doc as Quote).converted_invoice_id && (
            <div className="card"><div className="card__body"><p className="muted" style={{ fontSize: 13 }}>Convertida a factura.</p><button className="btn btn--soft" style={{ marginTop: 10, width: "100%" }} onClick={() => nav(`/facturas/${(doc as Quote).converted_invoice_id}`)}>Ver factura <Icon d={I.arrow} /></button></div></div>
          )}
          <div className="card"><div className="card__body">
            <div className="meta" style={{ marginBottom: 8 }}>Tipos de cambio</div>
            <div className="totals"><div><span>Venta</span><span className="money">{cur === "CRC" ? "—" : num(doc.fx_sell ?? fx?.sell ?? "")}</span></div><div><span>Compra</span><span className="money">{cur === "CRC" ? "—" : num(doc.fx_buy ?? fx?.buy ?? "")}</span></div></div>
            <p className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>Se guarda en el documento al emitir. Fuente: BCCR{me?.tenant.default_currency ? ` · base ${me.tenant.default_currency}` : ""}.</p>
          </div></div>
        </aside>
      </div>

      {pick && <ProductPicker onClose={() => setPick(false)} onPick={addProducts} />}
    </>
  );
}

function ProductPicker({ onClose, onPick }: { onClose: () => void; onPick: (ps: Product[]) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Product[]>([]);
  const [sel, setSel] = useState<Record<number, Product>>({});
  const [cursor, setCursor] = useState<number | null>(null);
  const load = useCallback(async (append = false, c: number | null = null) => {
    const qs = new URLSearchParams({ limit: "12" }); if (q) qs.set("q", q); if (c) qs.set("cursor", String(c));
    const r = await api<{ items: Product[]; next_cursor: number | null }>(`/products?${qs}`);
    setItems((p) => (append ? [...p, ...r.items] : r.items)); setCursor(r.next_cursor);
  }, [q]);
  useEffect(() => { const t = setTimeout(() => load(), 200); return () => clearTimeout(t); }, [load]);
  const n = Object.keys(sel).length;
  return (
    <Modal title="Productos & Servicios" onClose={onClose} wide foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={!n} onClick={() => onPick(Object.values(sel))}>Seleccionar{n ? ` (${n})` : ""}</button></>}>
      <div className="search" style={{ maxWidth: "none" }}><Icon d={I.search} size={16} /><input autoFocus placeholder="Buscar por nombre, código o CABYS…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {items.length === 0 ? <Empty hint="Creá el producto desde Productos & Servicios." /> : (
        <table className="table">
          <thead><tr><th /><th>Código</th><th>Nombre</th><th className="num">Precio</th><th>IVA</th></tr></thead>
          <tbody>{items.map((p) => (
            <tr key={p.id} onClick={() => setSel((s) => { const c = { ...s }; if (c[p.id]) delete c[p.id]; else c[p.id] = p; return c; })} style={{ cursor: "pointer" }}>
              <td><input type="checkbox" readOnly checked={!!sel[p.id]} /></td><td className="mono muted">{p.code}</td><td style={{ fontWeight: 600 }}>{p.name}<div className="meta" style={{ marginTop: 2 }}>{p.item_type}{p.cabys_code ? ` · CABYS ${p.cabys_code}` : ""}</div></td><td className="num money">{fmtMoney(p.price, p.currency)}</td><td className="muted">{p.tax_rate ?? 13}%</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {cursor && <button className="btn btn--soft btn--sm" style={{ alignSelf: "center" }} onClick={() => load(true, cursor)}>Más resultados</button>}
    </Modal>
  );
}

// Empty local para el picker (evita importar el de components por el estilo compacto)
function Empty({ hint }: { hint: string }) { return <div className="empty" style={{ padding: 24 }}><span className="meta">Sin resultados</span><p className="muted">{hint}</p></div>; }
