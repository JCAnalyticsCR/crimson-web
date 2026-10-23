/* Linea discreta del pie de un formulario con autoguardado: que paso con lo escrito y, si quedo una copia en este
   dispositivo que no llego al servidor, la opcion de recuperarla. */
import { useEffect, useState } from "react";
import type { AutosaveMode, AutosaveStatus, Recovery } from "../lib/useAutosave";
import "./autosave.css";

const ago = (t: number, now: number) => {
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 3) return "hace un momento";
  if (s < 60) return `hace ${s} s`;
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  return `a las ${new Date(t).toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit" })}`;
};

/* mode: "pause" (defecto, levantamiento) guarda mientras se escribe; "close" guarda al cerrar; "never" es un borrador
   en el dispositivo que llega al sistema solo con Guardar. isNew: el registro todavia no existe (borrador). */
export function AutosaveBar({ status, recovery, enabled, onRecover, onDiscard, mode = "pause", isNew = false }: {
  status: AutosaveStatus; recovery: Recovery<unknown> | null; enabled: boolean; onRecover: () => void; onDiscard: () => void;
  mode?: AutosaveMode; isNew?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status.state !== "saved" && status.state !== "draft") return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(t);
  }, [status]);

  if (!enabled) return null;
  if (recovery && (isNew || mode === "never")) {
    return (
      <div className="autosave autosave--recover" role="status">
        <span className="autosave__dot" aria-hidden="true" />
        <span className="autosave__text">Hay un borrador de {ago(recovery.at, Date.now())} en este dispositivo.</span>
        <span className="autosave__acts">
          <button type="button" className="btn btn--soft btn--sm" onClick={onRecover}>Recuperarlo</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard}>Descartar</button>
        </span>
      </div>
    );
  }
  if (recovery) {
    return (
      <div className="autosave autosave--recover" role="status">
        <span className="autosave__dot" aria-hidden="true" />
        <span className="autosave__text">
          Hay cambios de {ago(recovery.at, Date.now())} guardados en este dispositivo que no llegaron al servidor{recovery.warn ? ` (${recovery.warn})` : ""}.
        </span>
        <span className="autosave__acts">
          <button type="button" className="btn btn--soft btn--sm" onClick={onRecover}>Recuperarlos</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard}>Descartar</button>
        </span>
      </div>
    );
  }
  const { state, at, message } = status;
  const text =
    // mientras se escribe en "close" o "never" solo se copia al dispositivo: "Guardando…" haria creer que va al sistema
    state === "pending" && mode !== "pause" ? "Guardando copia en este dispositivo…"
    : state === "pending" || state === "saving" ? "Guardando…"
    : state === "saved" && at ? `Guardado ${ago(at, now)}`
    : state === "local" ? "Sin conexión: se guarda en este dispositivo y se sube al volver"
    : state === "warn" ? `Guardado, salvo esto: ${message}. Corregilo; lo demás ya quedó guardado y lo escrito sigue en este dispositivo.`
    : state === "error" ? `No se pudo guardar: ${message}. Lo escrito queda en este dispositivo.`
    : state === "draft" && at ? `Borrador guardado en este dispositivo ${ago(at, now)}${mode === "close" ? " · se guarda en el sistema al cerrar" : ""}`
    : mode === "close" ? "Se guarda al cerrar · mientras tanto queda copia en este dispositivo"
    : mode === "never" ? "Borrador en este dispositivo · se guarda en el sistema al tocar Guardar"
    : "Se guarda solo mientras escribe";
  return (
    <div className={`autosave autosave--${state}`} role="status" aria-live="polite">
      <span className="autosave__dot" aria-hidden="true" />
      <span className="autosave__text">{text}</span>
    </div>
  );
}
