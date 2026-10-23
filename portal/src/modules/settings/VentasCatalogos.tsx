/* Ajustes → Ventas (ventas etapa 3): kits de cotización y plantillas de mensaje. Solo el administrador los arma.
   - Kit: productos del catálogo con cantidades; al insertarlo en una cotización los precios salen del catálogo de ese
     momento (el kit no guarda precios), así el tope de descuento y la aprobación aplican igual.
   - Plantilla: texto de correo o WhatsApp con variables {cliente}, {numero}, {total}, {enlace}, {vendedor}, {validez},
     {empresa}. Va siempre como texto. Una columna en tablet (768 px). */
import { useCallback, useEffect, useState } from "react";
import { api, type Product } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Empty, Field, Modal } from "../../ui/components";

type KitLine = { product_id: number; quantity: string; name: string; code?: string | null; active?: boolean };
type Kit = { id?: number; name: string; description: string | null; active: boolean; lines: KitLine[] };
type Tpl = { id?: number; name: string; channel: "correo" | "whatsapp"; body: string; active: boolean };

const err = (e: unknown) => (e instanceof Error ? e.message : "Error");

export function KitsAdmin() {
  const { toast } = useSession();
  const [kits, setKits] = useState<Kit[]>([]);
  const [edit, setEdit] = useState<Kit | null>(null);
  const load = useCallback(() => api<Kit[]>("/kits?todos=true").then(setKits).catch(() => setKits([])), []);
  useEffect(() => { load(); }, [load]);
  return (
    <Card title="Kits de cotización" extra={<button className="btn btn--soft btn--sm" onClick={() => setEdit({ name: "", description: "", active: true, lines: [] })}>Nuevo kit</button>}>
      {kits.length === 0 ? <Empty title="Sin kits" hint="Por ejemplo: 4 cámaras + grabador + disco + instalación. El vendedor lo inserta en un clic." /> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          {kits.map((k) => (
            <li key={k.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", border: "1px solid var(--hair)", borderRadius: 10, padding: "8px 12px" }}>
              <div style={{ flex: "1 1 220px", minWidth: 0 }}><b>{k.name}</b>{!k.active && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Inactivo</span>}<div className="muted" style={{ fontSize: 12.5 }}>{k.lines.map((l) => `${Number(l.quantity)} × ${l.name}`).join(" · ")}</div></div>
              <button className="btn btn--ghost btn--sm" onClick={() => setEdit({ ...k, lines: k.lines.map((l) => ({ ...l, quantity: String(Number(l.quantity)) })) })}>Editar</button>
            </li>
          ))}
        </ul>
      )}
      {edit && <KitModal kit={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); toast("Kit guardado"); }} />}
    </Card>
  );
}

function KitModal({ kit, onClose, onSaved }: { kit: Kit; onClose: () => void; onSaved: () => void }) {
  const { toast } = useSession();
  const [k, setK] = useState<Kit>(kit);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Product[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = setTimeout(() => api<{ items: Product[] }>(`/products?limit=8&q=${encodeURIComponent(q.trim())}`).then((r) => setHits(r.items)).catch(() => setHits([])), 250);
    return () => clearTimeout(t);
  }, [q]);
  const add = (p: Product) => { setK({ ...k, lines: [...k.lines, { product_id: p.id, quantity: "1", name: p.name, code: p.code }] }); setQ(""); setHits([]); };
  const save = async () => {
    setBusy(true);
    try {
      const body = { name: k.name.trim(), description: (k.description || "").trim() || null, active: k.active, lines: k.lines.map((l) => ({ product_id: l.product_id, quantity: Number(l.quantity) })) };
      await api(k.id ? `/kits/${k.id}` : "/kits", { method: k.id ? "PUT" : "POST", json: body });
      onSaved();
    } catch (e) { toast(err(e), "bad"); } finally { setBusy(false); }
  };
  const ok = k.name.trim().length >= 2 && k.lines.length > 0 && k.lines.every((l) => Number(l.quantity) > 0);
  return (
    <Modal title={k.id ? "Editar kit" : "Nuevo kit"} onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" onClick={save} disabled={busy || !ok}>{busy ? "Guardando…" : "Guardar kit"}</button></>}>
      <Field label="Nombre"><input className="input" maxLength={160} value={k.name} onChange={(e) => setK({ ...k, name: e.target.value })} placeholder="4 cámaras + grabador + disco + instalación" /></Field>
      <Field label="Descripción (opcional)"><input className="input" maxLength={300} value={k.description || ""} onChange={(e) => setK({ ...k, description: e.target.value })} /></Field>
      <div className="meta">Productos del kit</div>
      {k.lines.length === 0 && <p className="muted" style={{ fontSize: 13, margin: 0 }}>Busque productos del catálogo para agregarlos.</p>}
      {k.lines.map((l, i) => (
        <div key={`${l.product_id}-${i}`} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input className="input" style={{ width: 90 }} type="number" min={0.001} step="1" inputMode="decimal" value={l.quantity} aria-label={`Cantidad de ${l.name}`} onChange={(e) => setK({ ...k, lines: k.lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)) })} />
          <span style={{ flex: "1 1 160px", minWidth: 0 }}>{l.name}{l.active === false && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Inactivo</span>}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => setK({ ...k, lines: k.lines.filter((_, j) => j !== i) })}>Quitar</button>
        </div>
      ))}
      <Field label="Agregar producto"><input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nombre o código…" /></Field>
      {hits.length > 0 && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, border: "1px solid var(--hair)", borderRadius: 10 }}>
          {hits.map((p) => <li key={p.id}><button className="btn btn--ghost btn--sm" style={{ width: "100%", justifyContent: "flex-start" }} onClick={() => add(p)}>{p.code} · {p.name}</button></li>)}
        </ul>
      )}
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}><input type="checkbox" checked={k.active} onChange={(e) => setK({ ...k, active: e.target.checked })} />Activo (los vendedores lo pueden insertar)</label>
    </Modal>
  );
}

export function TemplatesAdmin() {
  const { toast } = useSession();
  const [items, setItems] = useState<Tpl[]>([]);
  const [vars, setVars] = useState<string[]>([]);
  const [edit, setEdit] = useState<Tpl | null>(null);
  const load = useCallback(() => api<{ variables: string[]; items: Tpl[] }>("/message-templates?todos=true").then((r) => { setItems(r.items); setVars(r.variables); }).catch(() => setItems([])), []);
  useEffect(() => { load(); }, [load]);
  const save = async () => {
    if (!edit) return;
    try { await api(edit.id ? `/message-templates/${edit.id}` : "/message-templates", { method: edit.id ? "PUT" : "POST", json: { name: edit.name.trim(), channel: edit.channel, body: edit.body, active: edit.active } }); setEdit(null); load(); toast("Plantilla guardada"); }
    catch (e) { toast(err(e), "bad"); }
  };
  return (
    <Card title="Plantillas de mensaje" extra={<button className="btn btn--soft btn--sm" onClick={() => setEdit({ name: "", channel: "whatsapp", body: "Hola {cliente}, le compartimos la cotización {numero} por {total}. Puede verla y aceptarla aquí: {enlace}", active: true })}>Nueva plantilla</button>}>
      {items.length === 0 ? <Empty title="Sin plantillas" hint="El vendedor elige una al enviar la cotización por correo o WhatsApp." /> : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          {items.map((t) => (
            <li key={t.id} style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", border: "1px solid var(--hair)", borderRadius: 10, padding: "8px 12px" }}>
              <div style={{ flex: "1 1 220px", minWidth: 0 }}><b>{t.name}</b> <span className="badge badge--info">{t.channel === "whatsapp" ? "WhatsApp" : "Correo"}</span>{!t.active && <span className="badge badge--muted" style={{ marginLeft: 6 }}>Inactiva</span>}<div className="muted" style={{ fontSize: 12.5, whiteSpace: "pre-wrap" }}>{t.body}</div></div>
              <button className="btn btn--ghost btn--sm" onClick={() => setEdit(t)}>Editar</button>
            </li>
          ))}
        </ul>
      )}
      {edit && (
        <Modal title={edit.id ? "Editar plantilla" : "Nueva plantilla"} onClose={() => setEdit(null)} foot={<><button className="btn btn--ghost" onClick={() => setEdit(null)}>Cancelar</button><button className="btn btn--crimson" onClick={save} disabled={edit.name.trim().length < 2 || edit.body.trim().length < 2}>Guardar</button></>}>
          <div className="grid-2">
            <Field label="Nombre"><input className="input" maxLength={120} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Canal"><select className="select" value={edit.channel} onChange={(e) => setEdit({ ...edit, channel: e.target.value as Tpl["channel"] })}><option value="whatsapp">WhatsApp</option><option value="correo">Correo</option></select></Field>
          </div>
          <Field label="Texto" hint={`Variables: ${vars.map((v) => `{${v}}`).join(", ")}`}><textarea className="textarea" rows={5} maxLength={4000} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} /></Field>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 14 }}><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Activa</label>
        </Modal>
      )}
    </Card>
  );
}
