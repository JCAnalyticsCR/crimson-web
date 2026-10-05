/* Pagina publica "Solicitar acceso" al portal de clientes. Es una SOLICITUD: no da acceso a nada por si
   sola. Crimson la revisa, confirma a que cliente pertenece la persona y le manda una invitacion.
   La respuesta es la misma siempre (exista o no la empresa), y el campo "website" es una trampa para bots. */
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { I, Icon } from "../../ui/components";
import "../cliente/cliente.css";

const blank = { name: "", email: "", phone: "", company: "", id_number: "", message: "", website: "" };

export default function RequestAccess() {
  const [f, setF] = useState(blank);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof blank) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/public/access-requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
      const j = await r.json().catch(() => ({}));
      if (r.status === 429) throw new ApiError(429, "Recibimos muchas solicitudes desde esta conexión. Intente de nuevo en un rato.");
      if (!r.ok) throw new ApiError(r.status, j?.detail?.message || "Revise los datos del formulario");
      setDone(j.message || "Recibimos su solicitud.");
    } catch (ex) { setErr(ex instanceof Error ? ex.message : "No se pudo enviar"); }
    finally { setBusy(false); }
  };

  return (
    <div className="ra">
      <form className="ra__box cp-card cp-form" onSubmit={submit}>
        <img src="/logo.png" alt="Crimson Consulting" />
        {done ? (
          <>
            <h1 className="h2" style={{ margin: 0 }}>¡Listo!</h1>
            <p>{done}</p>
            <p className="muted" style={{ fontSize: 14 }}>Por seguridad, revisamos cada solicitud a mano antes de dar acceso a la información de una empresa. Cuando la aprobemos, le enviamos un enlace (por correo o por WhatsApp) para crear su contraseña.</p>
            <Link className="btn btn--ghost" to="/login">Ya tengo cuenta: entrar</Link>
          </>
        ) : (
          <>
            <div>
              <h1 className="h2" style={{ margin: 0 }}>Solicitar acceso al portal de clientes</h1>
              <p className="muted" style={{ fontSize: 14, marginTop: 6 }}>Desde el portal puede reportar fallas, seguir sus tickets y ver sus equipos instalados. Revisamos cada solicitud antes de dar acceso.</p>
            </div>
            <label className="field" style={{ margin: 0 }}><span className="meta">Nombre completo *</span><input className="input" required minLength={2} maxLength={120} autoComplete="name" value={f.name} onChange={set("name")} /></label>
            <label className="field" style={{ margin: 0 }}><span className="meta">Correo *</span><input className="input" type="email" required maxLength={200} autoComplete="email" value={f.email} onChange={set("email")} /></label>
            <label className="field" style={{ margin: 0 }}><span className="meta">Teléfono</span><input className="input" type="tel" maxLength={25} autoComplete="tel" placeholder="8888-8888" value={f.phone} onChange={set("phone")} /></label>
            <label className="field" style={{ margin: 0 }}><span className="meta">Empresa o residencial *</span><input className="input" required minLength={2} maxLength={160} autoComplete="organization" value={f.company} onChange={set("company")} /></label>
            <label className="field" style={{ margin: 0 }}><span className="meta">Cédula jurídica o física (opcional)</span><input className="input" maxLength={30} inputMode="numeric" placeholder="3-101-123456" value={f.id_number} onChange={set("id_number")} /></label>
            <label className="field" style={{ margin: 0 }}><span className="meta">Mensaje (opcional)</span><textarea className="textarea" rows={3} maxLength={1000} placeholder="Ej.: soy la administradora del condominio" value={f.message} onChange={set("message")} /></label>
            {/* trampa para bots: una persona no ve ni llena este campo */}
            <div className="ra__hp" aria-hidden="true"><label>Sitio web<input tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} /></label></div>
            {err && <p style={{ color: "var(--bad)", fontSize: 14, margin: 0 }} role="alert">{err}</p>}
            <button className="btn btn--crimson" disabled={busy}><Icon d={I.check} />{busy ? "Enviando…" : "Enviar solicitud"}</button>
            <Link to="/login" className="meta">¿Ya tiene cuenta? Entrar</Link>
          </>
        )}
      </form>
    </div>
  );
}
