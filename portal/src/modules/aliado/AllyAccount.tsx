/* Mi cuenta del usuario aliado: contraseña (patrón de la cuenta del portal del cliente) y verificación en dos pasos
   con la misma API /auth/2fa/* del panel interno (aliado_final §10.3). Los códigos de recuperación se muestran una
   sola vez. En la vista previa del admin no se muestra (la pantalla no sale del menú). */
import { useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon, Modal } from "../../ui/components";
import ClientAccount from "../cliente/ClientAccount";

function DosPasos() {
  const { me, reload, toast } = useSession();
  const [totp, setTotp] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [pw, setPw] = useState<{ password: string; regenerar: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const fallo = (e: unknown, msg: string) => toast(e instanceof Error ? e.message : msg, "bad");
  const configurar = async () => {
    setBusy(true);
    try { setTotp(await api<{ secret: string; otpauth_uri: string }>("/auth/2fa/setup", { method: "POST" })); setCode(""); }
    catch (e) { fallo(e, "No se pudo configurar"); }
    finally { setBusy(false); }
  };
  const activar = async () => {
    setBusy(true);
    try { const r = await api<{ codes: string[] }>("/auth/2fa/verify", { method: "POST", json: { code } }); await reload(); setTotp(null); setCode(""); setRecovery(r.codes); toast("Verificación en dos pasos activada"); }
    catch { toast("Código inválido", "bad"); }
    finally { setBusy(false); }
  };
  const confirmar = async () => {
    if (!pw) return;
    setBusy(true);
    try {
      if (pw.regenerar) {
        const r = await api<{ codes: string[] }>("/auth/2fa/recovery-codes", { method: "POST", json: { password: pw.password } });
        setRecovery(r.codes);
      } else {
        await api("/auth/2fa/disable", { method: "POST", json: { password: pw.password } });
        await reload();
        toast("Verificación en dos pasos desactivada");
      }
      setPw(null);
    } catch (e) { fallo(e, "No se pudo"); }
    finally { setBusy(false); }
  };
  return (
    <div className="cp-card cp-form" style={{ maxWidth: 460, marginTop: 16 }}>
      <b>Verificación en dos pasos</b>
      {me?.user.totp_enabled ? (
        <>
          <span className="muted" style={{ fontSize: 13 }}>Activa para {me.user.email}. Si pierde el teléfono, entra con uno de sus códigos de recuperación.</span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn--ghost btn--sm" onClick={() => setPw({ password: "", regenerar: true })}>Generar códigos nuevos</button>
            <button className="btn btn--danger btn--sm" onClick={() => setPw({ password: "", regenerar: false })}>Desactivar</button>
          </div>
        </>
      ) : totp ? (
        <>
          <span className="muted" style={{ fontSize: 13 }}>Agregue esta clave en su app de autenticación y confirme con el código de seis dígitos.</span>
          <code className="mono" style={{ padding: 10, background: "var(--bg-2)", borderRadius: 8, wordBreak: "break-all" }}>{totp.secret}</code>
          <div style={{ display: "flex", gap: 8 }}>
            <input className="input input--mono" inputMode="numeric" maxLength={6} placeholder="000000" aria-label="Código de seis dígitos" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 140 }} />
            <button className="btn btn--crimson" disabled={busy || code.length < 6} onClick={activar}>Activar</button>
          </div>
        </>
      ) : (
        <>
          <span className="muted" style={{ fontSize: 13 }}>Además de la contraseña, pide un código de su teléfono al entrar.</span>
          <button className="btn btn--soft" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={configurar}><Icon d={I.shield} />Configurar</button>
        </>
      )}
      {recovery && (
        <Modal title="Guarde estos códigos ahora" onClose={() => setRecovery(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => { navigator.clipboard.writeText(recovery.join("\n")); toast("Códigos copiados"); }}><Icon d={I.copy} />Copiar</button>
          <button className="btn btn--crimson" onClick={() => setRecovery(null)}>Ya los guardé</button>
        </>}>
          <p style={{ marginTop: 0 }}>Es la <b>única vez</b> que se muestran. Cada uno sirve una sola vez para entrar si pierde el teléfono.</p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 8 }}>
            {recovery.map((c) => <code key={c} className="mono" style={{ padding: "10px 12px", background: "var(--bg-2)", borderRadius: 8, textAlign: "center" }}>{c}</code>)}
          </div>
        </Modal>
      )}
      {pw && (
        <Modal title={pw.regenerar ? "Generar códigos nuevos" : "Desactivar la verificación en dos pasos"} onClose={() => setPw(null)} foot={<>
          <button className="btn btn--ghost" onClick={() => setPw(null)}>Cancelar</button>
          <button className={`btn ${pw.regenerar ? "btn--crimson" : "btn--danger"}`} disabled={busy || !pw.password} onClick={confirmar}>{pw.regenerar ? "Generar" : "Desactivar"}</button>
        </>}>
          <p style={{ marginTop: 0 }}>Confirme con su contraseña.</p>
          <input className="input" type="password" autoComplete="current-password" aria-label="Contraseña" value={pw.password} onChange={(e) => setPw({ ...pw, password: e.target.value })} />
        </Modal>
      )}
    </div>
  );
}

export default function AllyAccount() {
  return (
    <>
      <ClientAccount />
      <DosPasos />
    </>
  );
}
