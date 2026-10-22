/* Nueva oportunidad desde el portal del aliado (21/10, ítem 16: "por si a Crimson se le pasa"). El aliado no ve la
   cartera de clientes de Crimson: escribe el cliente y su contacto; Crimson lo liga al aprobarla. Por defecto queda
   «Pendiente de aprobación de Crimson» (pregunta abierta 8): al admin le llega el aviso y la aprueba o la rechaza.
   Mientras tanto se ve en la lista con ese estado y todavía no se levanta. Sin montos. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon, Modal } from "../../ui/components";
import { ROL_PARTICIPACION, type OppFull } from "./shared";

const SOLUCIONES: Record<string, string> = { cctv: "CCTV", redes: "Redes", acceso: "Control de acceso", asistencia: "Asistencia", ups: "UPS", cableado: "Cableado", anpr: "Lectura de placas", otro: "Otro" };
const PAPELES = ["subcontratista", "referido", "suministro", "configuracion", "software"];

export default function AllyNewOpportunity({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const { toast } = useSession();
  const [f, setF] = useState({ title: "", customer_name: "", site: "", solution: "cctv", kind: "proyecto", role: "subcontratista", contact_name: "", phone: "", email: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const up = (p: Partial<typeof f>) => setF({ ...f, ...p });
  const listo = f.title.trim().length >= 3 && f.customer_name.trim().length >= 2;
  const crear = async () => {
    if (!listo || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const o = await api<OppFull>("/aliado/oportunidades", {
        method: "POST",
        json: {
          title: f.title.trim(), customer_name: f.customer_name.trim(), site: f.site.trim() || null, solution: f.kind === "venta" ? null : f.solution, kind: f.kind, role: f.role,
          contact: { name: f.contact_name.trim() || null, phone: f.phone.trim() || null, email: f.email.trim() || null }, description: f.description.trim() || null,
        },
      });
      toast(o.approval === "pendiente" ? `${o.number} creada: queda pendiente de aprobación de Crimson` : `${o.number} creada`);
      onClose();
      nav(`/oportunidades/${o.id}`);
    } catch (e) { setErr(e instanceof Error ? e.message : "No se pudo crear la oportunidad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal title="Nueva oportunidad" onClose={busy ? () => undefined : onClose} wide foot={<>
      <button className="btn btn--ghost" onClick={onClose} disabled={busy}>Cancelar</button>
      <button className="btn btn--crimson" onClick={crear} disabled={!listo || busy} aria-busy={busy}><Icon d={I.check} />{busy ? "Creando…" : "Crear y enviar a Crimson"}</button>
    </>}>
      <p className="muted" style={{ margin: 0, fontSize: 13 }}>Crimson la revisa antes de trabajarla: la verá como «Pendiente de aprobación de Crimson» hasta que la aprueben. Cuando la aprueben puede hacer el levantamiento.</p>
      {err && <p role="alert" style={{ margin: 0, color: "var(--bad)", fontSize: 13 }}>{err}</p>}
      <Field label="Título"><input className="input" autoFocus maxLength={200} value={f.title} placeholder="Cámaras para bodega, Condominio Las Flores" onChange={(e) => up({ title: e.target.value })} /></Field>
      <div className="cp-grid cp-grid--2">
        <Field label="Cliente" hint="Quien recibe la solución."><input className="input" maxLength={160} value={f.customer_name} onChange={(e) => up({ customer_name: e.target.value })} /></Field>
        <Field label="Sitio"><input className="input" maxLength={300} value={f.site} placeholder="Dirección o referencia" onChange={(e) => up({ site: e.target.value })} /></Field>
        <Field label="Contacto en sitio"><input className="input" maxLength={160} value={f.contact_name} onChange={(e) => up({ contact_name: e.target.value })} /></Field>
        <Field label="Teléfono"><input className="input" maxLength={40} inputMode="tel" value={f.phone} onChange={(e) => up({ phone: e.target.value })} /></Field>
        <Field label="Correo del cliente"><input className="input" type="email" value={f.email} onChange={(e) => up({ email: e.target.value })} /></Field>
        <Field label="Tipo">
          <select className="select" value={f.kind} onChange={(e) => up({ kind: e.target.value })}><option value="proyecto">Proyecto con instalación</option><option value="venta">Solo venta de equipo</option></select>
        </Field>
        {f.kind !== "venta" && (
          <Field label="Solución">
            <select className="select" value={f.solution} onChange={(e) => up({ solution: e.target.value })}>{Object.entries(SOLUCIONES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          </Field>
        )}
        <Field label="Su papel">
          <select className="select" value={f.role} onChange={(e) => up({ role: e.target.value })}>{PAPELES.map((r) => <option key={r} value={r}>{ROL_PARTICIPACION[r] || r}</option>)}</select>
        </Field>
      </div>
      <Field label="¿Qué necesita el cliente?"><textarea className="textarea" rows={3} maxLength={4000} value={f.description} onChange={(e) => up({ description: e.target.value })} /></Field>
    </Modal>
  );
}
