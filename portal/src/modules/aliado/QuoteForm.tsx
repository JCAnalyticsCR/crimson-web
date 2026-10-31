/* OFERTA del aliado (15:29-15:45: "no pueden cotizar, pueden darme los precios de ellos"; revisión 2: la petición de
   Crimson y esta oferta son dos momentos): monto O desglose por líneas (concepto, cantidad, unidad, precio unitario,
   total en vivo), moneda, vigencia, condiciones, sus exclusiones y adjuntos.
   Sirve para responder una solicitud de Crimson o para enviar un precio por iniciativa propia. */
import { useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";
import { useUpload } from "../../ui/MediaPicker";
import { ROL_PARTICIPACION } from "./shared";

type Line = { concept: string; quantity: string; unit: string; unit_price: string };
const nueva = (): Line => ({ concept: "", quantity: "1", unit: "u", unit_price: "" });

export default function QuoteForm({ path, method, ask, roles, onDone, onCancel }: {
  path: string; method: "PUT" | "POST"; ask?: boolean; roles?: { id: number; role: string }[]; onDone: () => void; onCancel?: () => void;
}) {
  const { toast } = useSession();
  const { upload, busy: subiendo } = useUpload("/aliado/media");
  const [modo, setModo] = useState<"monto" | "lineas">("monto");
  const [what, setWhat] = useState("");
  const [amount, setAmount] = useState("");
  const [lines, setLines] = useState<Line[]>([nueva()]);
  const [currency, setCurrency] = useState("CRC");
  const [validUntil, setValidUntil] = useState("");
  const [exclusions, setExclusions] = useState("");
  const [conditions, setConditions] = useState("");
  const [files, setFiles] = useState<{ id: number; filename: string }[]>([]);
  const [part, setPart] = useState<number | "">(roles && roles.length === 1 ? roles[0].id : "");
  const [busy, setBusy] = useState(false);
  const total = lines.reduce((s, l) => s + (Number(l.quantity) || 0) * (Number(l.unit_price) || 0), 0);
  const set = (i: number, k: keyof Line, v: string) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  const enviar = async () => {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { currency, valid_until: validUntil || null, conditions: conditions || null, exclusions: exclusions || null, attachment_ids: files.map((f) => f.id) };
      if (modo === "monto") body.amount = amount;
      else body.lines = lines.filter((l) => l.concept.trim()).map((l) => ({ concept: l.concept.trim(), quantity: l.quantity, unit: l.unit || null, unit_price: l.unit_price || "0" }));
      if (ask) { body.what = what; if (part) body.participation_id = part; }
      await api(path, { method, json: body });
      toast("Oferta enviada a Crimson");
      onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo enviar", "bad"); }
    finally { setBusy(false); }
  };
  const listo = (modo === "monto" ? amount !== "" && Number(amount) >= 0 : lines.some((l) => l.concept.trim() && Number(l.quantity) > 0)) && (!ask || what.trim().length >= 2) && (!roles || roles.length < 2 || !!part);

  return (
    <div className="cp-card cp-form">
      <p className="meta" style={{ margin: 0 }}>Su oferta: precio, moneda, vigencia y condiciones. Crimson la revisa y, si la acepta, queda como acuerdo.</p>
      {ask && <Field label="¿Qué cotiza?"><input className="input" value={what} maxLength={300} onChange={(e) => setWhat(e.target.value)} placeholder="Ej.: instalación de 8 cámaras" /></Field>}
      {ask && roles && roles.length > 1 && (
        <Field label="Papel en el que envía el precio">
          <select className="select" value={part} onChange={(e) => setPart(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Elegí…</option>
            {roles.map((r) => <option key={r.id} value={r.id}>{ROL_PARTICIPACION[r.role] || r.role}</option>)}
          </select>
        </Field>
      )}
      <div className="cp-seg">
        <button type="button" className={modo === "monto" ? "is-on" : ""} onClick={() => setModo("monto")}>Monto total</button>
        <button type="button" className={modo === "lineas" ? "is-on" : ""} onClick={() => setModo("lineas")}>Desglose por líneas</button>
      </div>
      {modo === "monto" ? (
        <Field label="Monto (sin IVA)"><input className="input input--mono" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
      ) : (
        <div className="al-lines">
          {lines.map((l, i) => (
            <div className="al-line" key={i}>
              <Field label="Concepto"><input className="input" value={l.concept} onChange={(e) => set(i, "concept", e.target.value)} /></Field>
              <Field label="Cantidad"><input className="input input--mono" inputMode="decimal" value={l.quantity} onChange={(e) => set(i, "quantity", e.target.value)} /></Field>
              <Field label="Unidad"><input className="input" value={l.unit} maxLength={10} onChange={(e) => set(i, "unit", e.target.value)} /></Field>
              <Field label="Precio unitario"><input className="input input--mono" inputMode="decimal" value={l.unit_price} onChange={(e) => set(i, "unit_price", e.target.value)} /></Field>
              <button type="button" className="btn btn--ghost btn--icon" aria-label="Quitar línea" onClick={() => setLines(lines.length > 1 ? lines.filter((_, j) => j !== i) : [nueva()])}><Icon d={I.x} /></button>
            </div>
          ))}
          <button type="button" className="btn btn--soft btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => setLines([...lines, nueva()])}><Icon d={I.plus} size={14} />Agregar línea</button>
          <b>Total: {fmtMoney(total, currency)} (sin IVA)</b>
        </div>
      )}
      <div className="cp-grid cp-grid--2">
        <Field label="Moneda"><select className="select" value={currency} onChange={(e) => setCurrency(e.target.value)}><option value="CRC">Colones (CRC)</option><option value="USD">Dólares (USD)</option></select></Field>
        <Field label="Vigencia del precio"><input className="input" type="date" value={validUntil} onChange={(e) => setValidUntil(e.target.value)} /></Field>
      </div>
      <Field label="Condiciones (forma de pago, plazos, garantía)"><textarea className="textarea" rows={2} value={conditions} maxLength={4000} onChange={(e) => setConditions(e.target.value)} /></Field>
      <Field label="Exclusiones (lo que no incluye)"><textarea className="textarea" rows={2} value={exclusions} maxLength={4000} onChange={(e) => setExclusions(e.target.value)} /></Field>
      <Field label="Adjuntos (PDF o fotos)">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {files.map((f) => <span key={f.id} className="badge badge--muted">{f.filename} <button type="button" className="x" aria-label="Quitar adjunto" onClick={() => setFiles(files.filter((x) => x.id !== f.id))}>×</button></span>)}
          <label className="btn btn--ghost btn--sm">
            {subiendo ? <span className="spinner" /> : <><Icon d={I.upload} size={14} />Adjuntar</>}
            <input type="file" accept="application/pdf,image/*" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (!f) return; const m = await upload(f); if (m) setFiles([...files, { id: m.id, filename: m.filename }]); }} />
          </label>
        </div>
      </Field>
      <div className="al-actions">
        <button type="button" className="btn btn--crimson" disabled={!listo || busy} onClick={enviar}><Icon d={I.check} />Enviar mi oferta</button>
        {onCancel && <button type="button" className="btn btn--ghost" onClick={onCancel}>Cancelar</button>}
      </div>
    </div>
  );
}
