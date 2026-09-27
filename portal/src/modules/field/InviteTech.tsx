/* Acceso directo "+ Técnico" donde se elige técnico (levantamientos y órdenes de trabajo).
   Andrés no encontró cómo crear un técnico: vive en Ajustes → Usuarios → Invitar. Esto abre la misma
   invitación (POST /settings/invitations) con el rol técnico ya elegido; no hay lógica nueva de usuarios.
   Solo se muestra a quien puede invitar (settings.configurar). */
import { useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon, Modal } from "../../ui/components";

export function InviteTechButton({ label = "Técnico" }: { label?: string }) {
  const { toast, allows } = useSession();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState<string | null>(null);

  if (!allows("settings.configurar")) return null;

  const close = () => { setOpen(false); setEmail(""); setLink(null); };
  const send = async () => {
    setBusy(true);
    try {
      const r = await api<{ link: string }>("/settings/invitations", { method: "POST", json: { email: email.trim(), role: "tecnico" } });
      setLink(r.link);
      toast("Invitación enviada");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <>
      <button type="button" className="btn btn--ghost btn--sm" title="Invitar a un técnico nuevo (rol técnico)" onClick={() => setOpen(true)}><Icon d={I.plus} size={14} />{label}</button>
      {open && (
        <Modal title="Invitar técnico" onClose={close} foot={link
          ? <button className="btn btn--ghost" onClick={close}>Listo</button>
          : <><button className="btn btn--ghost" onClick={close}>Cancelar</button><button className="btn btn--crimson" disabled={busy || !email.includes("@")} onClick={send}>Enviar invitación</button></>}>
          {link ? (
            <>
              <p style={{ fontSize: 13.5, margin: 0 }}>Le llegó un correo a <b>{email}</b>. Cuando cree su contraseña aparece en la lista de técnicos. También podés mandarle el enlace por WhatsApp:</p>
              <div className="search" style={{ maxWidth: "none" }}><Icon d={I.link} size={16} /><input readOnly value={link} onFocus={(e) => e.currentTarget.select()} /></div>
              <button className="btn btn--soft" style={{ alignSelf: "flex-start" }} onClick={() => { navigator.clipboard.writeText(link); toast("Enlace copiado"); }}><Icon d={I.copy} />Copiar</button>
            </>
          ) : (
            <>
              <Field label="Correo del técnico" hint="Recibe un enlace para crear su contraseña (vence en 7 días). Entra con rol técnico: no ve precios ni costos.">
                <input className="input" type="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="tecnico@crimsoncr.com" />
              </Field>
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Es la misma invitación de Ajustes → Usuarios, con el rol técnico ya elegido.</p>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
