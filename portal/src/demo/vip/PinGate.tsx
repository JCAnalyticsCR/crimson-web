/* Pantalla de acceso: cuatro casillas de codigo.
   - inputMode numeric, sin autocompletado (ni one-time-code ni gestor de contrasenas).
   - Pegar 4 digitos en cualquier casilla los reparte; escribir avanza; Backspace en casilla vacia retrocede.
   - 3 intentos fallidos -> 30 s de espera con cuenta regresiva (sobrevive a recargar: queda en sessionStorage).
   - Errores y esperas se anuncian en una region aria-live; la vibracion es visual (y desaparece con reduced-motion). */
import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { checkPin, KEY, LOCK_MS, MAX_FAILS, PIN_LENGTH, ss } from "./pin";
import { Corners, MonitorBar } from "./chrome";

const empty = () => Array.from({ length: PIN_LENGTH }, () => "");

export default function PinGate({ onUnlock }: { onUnlock: () => void }) {
  const [digits, setDigits] = useState<string[]>(empty);
  const [status, setStatus] = useState<"idle" | "checking" | "error" | "granted">("idle");
  const [msg, setMsg] = useState("");
  const [shake, setShake] = useState(0);
  const [lockUntil, setLockUntil] = useState<number>(() => { const t = Number(ss.get(KEY.lockUntil) || 0); return t > Date.now() ? t : 0; });
  const [now, setNow] = useState(() => Date.now());
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  const locked = lockUntil > now;
  const left = Math.max(0, Math.ceil((lockUntil - now) / 1000));

  const focusAt = (i: number) => {
    const el = refs.current[Math.max(0, Math.min(PIN_LENGTH - 1, i))];
    el?.focus();
    el?.select();
  };

  // Foco diferido: se aplica despues del render que vuelve a habilitar las casillas (rAF no corre en pestanas ocultas).
  const pendingFocus = useRef<number | null>(null);
  useEffect(() => {
    if (pendingFocus.current === null || locked || status === "checking" || status === "granted") return;
    const i = pendingFocus.current;
    pendingFocus.current = null;
    focusAt(i);
  });

  // cuenta regresiva de la espera
  useEffect(() => {
    if (!locked) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [locked]);
  useEffect(() => {
    if (lockUntil && !locked) {
      ss.del(KEY.lockUntil);
      setLockUntil(0);
      setMsg("Ya podés intentar de nuevo.");
      setStatus("idle");
      pendingFocus.current = 0;
    }
  }, [locked, lockUntil]);

  const submit = useCallback(async (code: string) => {
    setStatus("checking");
    const ok = await checkPin(code);
    if (ok) {
      ss.del(KEY.fails);
      ss.set(KEY.ok, "1");
      setStatus("granted");
      setMsg("Acceso concedido.");
      // Confirmacion breve (DUR.drawer) antes de pasar: feedback, no coreografia.
      window.setTimeout(onUnlock, 380);
      return;
    }
    const fails = Number(ss.get(KEY.fails) || 0) + 1;
    setDigits(empty());
    setStatus("error");
    setShake((s) => s + 1);
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches && navigator.userActivation?.isActive) navigator.vibrate?.(12);
    if (fails >= MAX_FAILS) {
      const until = Date.now() + LOCK_MS;
      ss.set(KEY.lockUntil, String(until));
      ss.del(KEY.fails);
      setNow(Date.now());
      setLockUntil(until);
      setMsg(`Demasiados intentos. Esperá ${LOCK_MS / 1000} segundos para volver a probar.`);
      (document.activeElement as HTMLElement | null)?.blur();
    } else {
      ss.set(KEY.fails, String(fails));
      const r = MAX_FAILS - fails;
      setMsg(`Código incorrecto. Te ${r === 1 ? "queda 1 intento" : `quedan ${r} intentos`}.`);
      pendingFocus.current = 0;
    }
  }, [onUnlock]);

  const put = (i: number, raw: string) => {
    if (locked || status === "checking" || status === "granted") return;
    const nums = raw.replace(/\D/g, "");
    const next = [...digits];
    if (!nums) { next[i] = ""; setDigits(next); return; }
    let start = i;
    let src: string;
    if (nums.length >= PIN_LENGTH) { start = 0; src = nums.slice(0, PIN_LENGTH); } // pegado o autollenado completo
    else if (digits[i] && nums.length === 2) src = nums.replace(digits[i], "").slice(-1) || nums.slice(-1); // tecla sobre casilla llena
    else src = nums.slice(0, PIN_LENGTH - i); // una tecla, o pegado parcial que se reparte desde aqui
    for (let k = 0; k < src.length; k++) next[start + k] = src[k];
    setDigits(next);
    if (status === "error") setStatus("idle");
    const hole = next.findIndex((d) => !d);
    if (hole === -1) { (document.activeElement as HTMLElement | null)?.blur(); void submit(next.join("")); }
    else { const after = start + src.length; focusAt(next[after] === "" ? after : hole); }
  };

  const onKey = (i: number, e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace") {
      e.preventDefault();
      const next = [...digits];
      if (next[i]) { next[i] = ""; setDigits(next); }
      else if (i > 0) { next[i - 1] = ""; setDigits(next); focusAt(i - 1); }
    } else if (e.key === "ArrowLeft") { e.preventDefault(); focusAt(i - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); focusAt(i + 1); }
  };

  const onPaste = (i: number, e: ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault();
    put(i, e.clipboardData.getData("text"));
  };

  const disabled = locked || status === "checking" || status === "granted";
  const lockPct = locked ? Math.max(0, Math.min(1, (lockUntil - now) / LOCK_MS)) : 0;

  return (
    <div className="vip vip-gate" data-status={status}>
      <div className="vip-grid" aria-hidden="true" />
      <MonitorBar cam="01" label="Acceso" />
      <main className="vip-gate__main">
        <div className="vip-gate__frame">
          <Corners />
          <span className="vip-gate__badge" aria-hidden="true">
            <svg viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="10" rx="2" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5M12 14.5v2.5" /></svg>
          </span>
          <h1 className="vip-gate__title">Crimson · Tienda <span>— Vista previa privada</span></h1>
          <p className="vip-gate__lead" id="vip-pin-help">Ingresá el código de acceso de {PIN_LENGTH} dígitos.</p>

          <form className="vip-pin" onSubmit={(e) => { e.preventDefault(); if (digits.every(Boolean)) void submit(digits.join("")); }}
            aria-describedby="vip-pin-help" noValidate>
            <fieldset disabled={disabled} className="vip-pin__set">
              <legend className="vip-sr">Código de acceso</legend>
              <div className="vip-pin__row" key={shake} data-shake={shake > 0 && status === "error" ? "1" : undefined}>
                {digits.map((d, i) => (
                  <input
                    key={i}
                    ref={(el) => { refs.current[i] = el; }}
                    className="vip-pin__box"
                    data-filled={d ? "1" : undefined}
                    value={d}
                    onChange={(e) => put(i, e.target.value)}
                    onKeyDown={(e) => onKey(i, e)}
                    onPaste={(e) => onPaste(i, e)}
                    onFocus={(e) => e.target.select()}
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    autoComplete="off"
                    autoCorrect="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    enterKeyHint={i === PIN_LENGTH - 1 ? "done" : "next"}
                    data-1p-ignore=""
                    data-lpignore="true"
                    data-form-type="other"
                    autoFocus={i === 0 && !locked}
                    aria-label={`Dígito ${i + 1} de ${PIN_LENGTH}`}
                    aria-invalid={status === "error" || undefined}
                  />
                ))}
              </div>
            </fieldset>
          </form>

          <p className="vip-gate__msg" data-tone={status === "granted" ? "ok" : status === "error" || locked ? "bad" : undefined} aria-live="assertive" aria-atomic="true">
            {status === "checking" ? <span className="vip-sr">Verificando…</span> : msg}
          </p>
          {locked && (
            <div className="vip-lock" aria-hidden="true">
              <span className="vip-lock__t">Reintento en <b>0:{String(left).padStart(2, "0")}</b></span>
              <span className="vip-lock__bar"><i style={{ transform: `scaleX(${lockPct})` }} /></span>
            </div>
          )}
        </div>
      </main>
      <footer className="vip-gate__foot">
        <span>Acceso solo con invitación</span>
        <span aria-hidden="true">Crimson Seguridad · Costa Rica</span>
      </footer>
    </div>
  );
}
