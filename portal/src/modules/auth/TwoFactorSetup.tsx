/* Verificación en dos pasos (plan 1.10). La usan Ajustes → Mi cuenta y la pantalla forzada (TwoFactorForced).
   - Primera vez: se genera la clave, se agrega en la app de autenticación y se confirma con el código.
   - Cambio de teléfono (2FA activo): primero contraseña + código de la app actual (o uno de recuperación). La clave
     nueva queda pendiente: la app vieja sigue valiendo hasta que se confirme la nueva con su código.
   Los códigos de recuperación se muestran UNA sola vez (después solo viven hasheados en la base). */
import { useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, I, Icon } from "../../ui/components";

type Secret = { secret: string; otpauth_uri: string };
const errMsg = (e: unknown) => (e instanceof Error ? e.message : "Error");

export default function TwoFactorSetup({ change = false, onDone, onCancel }: { change?: boolean; onDone: (codes: string[]) => void; onCancel?: () => void }) {
  const { toast } = useSession();
  const [cred, setCred] = useState({ password: "", code: "" });
  const [sec, setSec] = useState<Secret | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const generar = async () => {
    setBusy(true); setErr(null);
    try {
      setSec(await api<Secret>("/auth/2fa/setup", { method: "POST", json: change ? { password: cred.password, code: cred.code.trim() } : {} }));
      setCred({ password: "", code: "" });
    } catch (e) { setErr(errMsg(e)); }
    finally { setBusy(false); }
  };
  const confirmar = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api<{ codes: string[] }>("/auth/2fa/verify", { method: "POST", json: { code } });
      toast(change ? "Listo: la verificación en dos pasos ya usa la app nueva" : "Verificación en dos pasos activada");
      onDone(r.codes);
    } catch { setErr("Código inválido o vencido. Escriba el que muestra la app en este momento."); }
    finally { setBusy(false); }
  };

  if (!sec) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {change ? <>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Para pasar la verificación a otro teléfono o app, confirme su identidad. Su app actual sigue sirviendo hasta que termine.</p>
          <Field label="Su contraseña"><input className="input" type="password" autoComplete="current-password" value={cred.password} onChange={(e) => setCred({ ...cred, password: e.target.value })} /></Field>
          <Field label="Código de su app actual" hint="Si ya no tiene la app, use uno de sus códigos de recuperación."><input className="input input--mono" autoComplete="one-time-code" maxLength={9} placeholder="000000" value={cred.code} onChange={(e) => setCred({ ...cred, code: e.target.value.toUpperCase() })} style={{ width: 160 }} /></Field>
        </> : (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Necesita una app de autenticación en el teléfono (Google Authenticator, Microsoft Authenticator, Authy o la de su gestor de contraseñas). Al entrar, además de la contraseña, escribirá el código de seis dígitos que muestra.</p>
        )}
        {err && <p role="alert" style={{ color: "var(--bad)", fontSize: 13, margin: 0 }}>{err}</p>}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn btn--crimson" onClick={generar} disabled={busy || (change && (!cred.password || cred.code.trim().length < 6))}>{busy ? "Un momento…" : change ? "Continuar" : "Configurar la verificación"}</button>
          {onCancel && <button className="btn btn--ghost" onClick={onCancel} disabled={busy}>Cancelar</button>}
        </div>
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>1. En la app, agregue una cuenta nueva con esta clave (o toque «Abrir en la app» desde el teléfono).</p>
      <code className="mono" style={{ padding: 10, background: "var(--bg-2)", borderRadius: 8, wordBreak: "break-all" }}>{sec.secret}</code>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn--ghost btn--sm" onClick={() => { navigator.clipboard.writeText(sec.secret); toast("Clave copiada"); }}><Icon d={I.copy} />Copiar la clave</button>
        <a className="btn btn--ghost btn--sm" href={sec.otpauth_uri}>Abrir en la app</a>
      </div>
      <p className="muted" style={{ fontSize: 13, margin: "4px 0 0" }}>2. Escriba el código de seis dígitos que muestra la app.</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input className="input input--mono" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="000000" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} onKeyDown={(e) => { if (e.key === "Enter" && code.length === 6) confirmar(); }} style={{ width: 140 }} autoFocus />
        <button className="btn btn--crimson" onClick={confirmar} disabled={busy || code.length !== 6}>{busy ? "Verificando…" : change ? "Confirmar la app nueva" : "Activar"}</button>
        {onCancel && <button className="btn btn--ghost" onClick={onCancel} disabled={busy}>Cancelar</button>}
      </div>
      {err && <p role="alert" style={{ color: "var(--bad)", fontSize: 13, margin: 0 }}>{err}</p>}
      {change && <p className="muted" style={{ fontSize: 12, margin: 0 }}>Hasta que confirme, su app anterior sigue siendo la que vale para entrar.</p>}
    </div>
  );
}

/** Códigos de recuperación recién generados: se ven una sola vez. */
export function RecoveryCodes({ codes, email, onDone }: { codes: string[]; email?: string; onDone: () => void }) {
  const { toast } = useSession();
  const descargar = () => {
    const texto = `Códigos de recuperación · Crimson · ${email || ""}\nGenerados el ${new Date().toLocaleString("es-CR")}\n\n${codes.join("\n")}\n\nCada código sirve UNA sola vez.\n`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([texto], { type: "text/plain" }));
    a.download = "crimson-codigos-recuperacion.txt";
    a.click();
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p style={{ fontSize: 14, margin: 0 }}>Guarde estos códigos ahora: es la <b>única vez</b> que se muestran. Si pierde el teléfono, con uno de ellos entra al panel.</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 8 }}>
        {codes.map((c) => <code key={c} className="mono" style={{ padding: "10px 12px", background: "var(--bg-2)", borderRadius: 8, textAlign: "center", fontSize: 15, letterSpacing: ".05em" }}>{c}</code>)}
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Cada uno sirve una sola vez. Guardalos en tu gestor de contraseñas o impresos, no en el mismo teléfono que tiene la app.</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn btn--ghost" onClick={() => { navigator.clipboard.writeText(codes.join("\n")); toast("Códigos copiados"); }}><Icon d={I.copy} />Copiar</button>
        <button className="btn btn--ghost" onClick={descargar}>Descargar</button>
        <button className="btn btn--crimson" onClick={onDone}>Ya los guardé</button>
      </div>
    </div>
  );
}
