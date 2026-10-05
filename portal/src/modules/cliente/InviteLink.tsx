/* Resultado de una invitacion. Solo dice "le enviamos un correo" si el correo salio de verdad; si no
   (Resend sin configurar o fallo), muestra el enlace para copiarlo o mandarlo por WhatsApp. */
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";

export type InviteOut = { id: number; link: string; emailed: boolean; expires_at?: string };

export function InviteLink({ inv, email }: { inv: InviteOut; email: string }) {
  const { toast } = useSession();
  const copiar = async () => { try { await navigator.clipboard.writeText(inv.link); toast("Enlace copiado"); } catch { toast("No se pudo copiar; selecciónelo a mano", "bad"); } };
  const wa = `https://wa.me/?text=${encodeURIComponent(`Le damos acceso al portal de clientes de Crimson. Cree su contraseña aquí (vence en 7 días): ${inv.link}`)}`;
  return (
    <div className="cp-card" style={{ display: "flex", flexDirection: "column", gap: 8, background: "var(--surface-2)" }}>
      {inv.emailed
        ? <b style={{ color: "var(--ok)" }}>Le enviamos la invitación a {email}.</b>
        : <b>El correo no salió (el envío de correos no está activo). Mande este enlace a {email} por WhatsApp:</b>}
      <div className="cp-link">{inv.link}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn btn--soft btn--sm" onClick={copiar}><Icon d={I.copy} size={14} />Copiar enlace</button>
        <a className="btn btn--ghost btn--sm" href={wa} target="_blank" rel="noopener noreferrer"><Icon d={I.whatsapp} size={14} />WhatsApp</a>
      </div>
      <span className="meta">Sirve una sola vez y vence en 7 días.</span>
    </div>
  );
}
