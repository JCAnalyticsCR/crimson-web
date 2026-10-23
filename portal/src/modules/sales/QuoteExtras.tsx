/* Ventas etapa 3 · piezas de la cotización:
   - KitPicker: inserta un kit del administrador. Las líneas llegan con el precio de catálogo de hoy y se pueden editar;
     al guardar pasan por el mismo tope de descuento, margen mínimo y aprobación que cualquier línea.
   - QuoteShare: plantillas de mensaje (correo o WhatsApp) con el enlace de aceptación en línea, y cuándo lo abrió el
     cliente. Enviar por WhatsApp deja la cotización como enviada (versión congelada), igual que «Marcar enviada». */
import { useEffect, useState } from "react";
import { api, type Line, type Quote } from "../../lib/api";
import { useSession } from "../../app/session";
import { Empty, Field, I, Icon, Loading, Modal } from "../../ui/components";

type Kit = { id: number; name: string; description: string | null; lines: { product_id: number; quantity: string; name: string; active: boolean }[] };
export type Tpl = { id: number; name: string; channel: "correo" | "whatsapp"; body: string; active: boolean };

export function KitPicker({ currency, onClose, onPick }: { currency: string; onClose: () => void; onPick: (lines: Partial<Line>[]) => void }) {
  const { toast } = useSession();
  const [kits, setKits] = useState<Kit[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  useEffect(() => { api<Kit[]>("/kits").then(setKits).catch((e) => { toast(e instanceof Error ? e.message : "Error", "bad"); setKits([]); }); }, [toast]);
  const pick = async (k: Kit) => {
    setBusy(k.id);
    try {
      const r = await api<{ lines: Record<string, unknown>[]; omitidos: string[] }>(`/kits/${k.id}/lines?currency=${currency}`);
      if (r.omitidos.length) toast(`No se agregó (producto inactivo): ${r.omitidos.join(", ")}`, "bad");
      onPick(r.lines.map((x) => ({ ...x, quantity: String(x.quantity), unit_price: String(x.unit_price), discount_value: String(x.discount_value ?? 0), tax_rate: String(x.tax_rate ?? 13) }) as Partial<Line>));
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(null); }
  };
  return (
    <Modal title="Insertar un kit" onClose={onClose} foot={<button className="btn btn--ghost" onClick={onClose}>Cerrar</button>}>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>Las líneas entran con el precio de catálogo de hoy. Después las puede editar como cualquier línea.</p>
      {kits === null ? <Loading /> : kits.length === 0 ? <Empty title="Todavía no hay kits" hint="El administrador los arma en Ajustes → Ventas." /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {kits.map((k) => (
            <div key={k.id} style={{ border: "1px solid var(--hair)", borderRadius: 10, padding: "10px 12px", display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 220px", minWidth: 0 }}>
                <b>{k.name}</b>
                <div className="muted" style={{ fontSize: 12.5 }}>{k.lines.map((l) => `${Number(l.quantity)} × ${l.name}`).join(" · ")}</div>
              </div>
              <button className="btn btn--crimson btn--sm" disabled={busy !== null} onClick={() => pick(k)}>{busy === k.id ? "Agregando…" : "Insertar"}</button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

const when = (s: string) => new Date(s).toLocaleString("es-CR", { timeZone: "America/Costa_Rica", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export function QuoteShare({ quote, emailTpl, setEmailTpl, onSent }: { quote: Partial<Quote>; emailTpl: string; setEmailTpl: (text: string) => void; onSent: () => void }) {
  const { toast } = useSession();
  const [tpls, setTpls] = useState<Tpl[]>([]);
  const [waTpl, setWaTpl] = useState("");
  const [emailId, setEmailId] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api<{ items: Tpl[] }>("/message-templates").then((r) => setTpls(r.items)).catch(() => setTpls([])); }, []);
  const link = quote.online_link;
  const id = quote.id;

  const pickEmail = async (v: string) => {
    setEmailId(v);
    if (!v) { setEmailTpl(""); return; }
    try { const r = await api<{ text: string }>(`/quotes/${id}/message?template_id=${v}`, { method: "POST" }); setEmailTpl(r.text); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  const whatsapp = async () => {
    setBusy(true);
    const w = window.open("", "_blank"); // se abre ya (los navegadores bloquean una ventana abierta despues de un await)
    try {
      const r = await api<{ whatsapp_url: string }>(`/quotes/${id}/message${waTpl ? `?template_id=${waTpl}` : ""}`, { method: "POST" });
      if (w) w.location.href = r.whatsapp_url; else window.location.href = r.whatsapp_url;
      await api(`/quotes/${id}/send`, { method: "POST", json: { channel: "whatsapp" } });
      toast("Cotización marcada como enviada por WhatsApp"); onSent();
    } catch (e) { w?.close(); toast(e instanceof Error ? e.message : "Error", "bad"); } finally { setBusy(false); }
  };
  const copy = () => { if (link) navigator.clipboard.writeText(link.url).then(() => toast("Enlace copiado")).catch(() => toast("No se pudo copiar", "bad")); };
  const wa = tpls.filter((t) => t.channel === "whatsapp");
  const mail = tpls.filter((t) => t.channel === "correo");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--hair)", paddingTop: 10 }}>
      {mail.length > 0 && (
        <Field label="Mensaje del correo" hint={emailTpl ? "Este texto va en el correo al usar «Guardar & enviar»." : undefined}>
          <select className="select" value={emailId} onChange={(e) => pickEmail(e.target.value)}><option value="">El de siempre</option>{mail.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
        </Field>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        {wa.length > 0 && <div style={{ flex: "1 1 160px" }}><Field label="Plantilla de WhatsApp"><select className="select" value={waTpl} onChange={(e) => setWaTpl(e.target.value)}><option value="">La de siempre</option>{wa.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field></div>}
        <button className="btn btn--soft" disabled={busy} onClick={whatsapp}><Icon d={I.whatsapp} />Enviar por WhatsApp</button>
      </div>
      {link ? (
        <div style={{ fontSize: 12.5, display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}><b>Enlace de aceptación en línea</b><button className="btn btn--ghost btn--sm" onClick={copy}><Icon d={I.copy} size={14} />Copiar</button></div>
          <span className="muted">{link.first_opened_at ? `El cliente lo abrió por primera vez el ${when(link.first_opened_at)}${link.last_opened_at && link.open_count > 1 ? ` y la última el ${when(link.last_opened_at)} (${link.open_count} veces)` : ""}.` : "El cliente todavía no lo ha abierto."} Vence el {when(link.expires_at)}.</span>
        </div>
      ) : <span className="muted" style={{ fontSize: 12.5 }}>Al enviarla, la cotización lleva un enlace para que el cliente la vea y la acepte en línea.</span>}
    </div>
  );
}
