/* DEMOSTRACION ESTATICA: candado de entrada (respaldo simple; si existe ./vip, main-demo.tsx usa VipGate).

   IMPORTANTE: es un candado DISUASIVO del lado del navegador, NO es seguridad real. Todo el contenido de la demo
   (catalogo publico, codigo) se descarga igual y cualquiera con conocimientos lo puede leer o saltarse el candado.
   Sirve para que la vista previa no la recorra cualquiera que encuentre el enlace. El PIN no vive aqui en texto
   plano: solo su SHA-256 con sal (Web Crypto). Tres intentos fallidos -> espera de 30 s. El acceso se recuerda en
   sessionStorage (se olvida al cerrar la pestana). */
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

const SALT = "bbea7ddb31dfe1e99251908322794991";
const PIN_SHA256 = "f125de744d316ad2f8f87b472c1afe5a8a7d8d51a94a15b50d6b488dd3604576"; // sha256(`${SALT}:${pin}`)
const OK_KEY = "crimson-demo:acceso";
const LOCK_KEY = "crimson-demo:espera";
const MAX_TRIES = 3;
const LOCK_MS = 30_000;

const ss = {
  get(k: string) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k: string, v: string) { try { sessionStorage.setItem(k, v); } catch { /* modo privado: no se recuerda */ } },
};
async function sha256(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
export const hasDemoAccess = () => ss.get(OK_KEY) === PIN_SHA256.slice(0, 16);

export default function DemoGate({ children }: { children: ReactNode }) {
  const [ok, setOk] = useState(hasDemoAccess);
  const [pin, setPin] = useState("");
  const [tries, setTries] = useState(0);
  const [until, setUntil] = useState(() => Number(ss.get(LOCK_KEY)) || 0);
  const [now, setNow] = useState(Date.now());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const locked = until > now;

  useEffect(() => {
    if (!locked) return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [locked]);
  useEffect(() => { if (!locked) input.current?.focus(); }, [locked]);

  const check = async (value: string) => {
    if (busy || locked || value.length !== 4) return;
    setBusy(true);
    const good = (await sha256(`${SALT}:${value}`)) === PIN_SHA256;
    setBusy(false);
    if (good) { ss.set(OK_KEY, PIN_SHA256.slice(0, 16)); setOk(true); return; }
    const n = tries + 1;
    setPin("");
    if (n >= MAX_TRIES) {
      const u = Date.now() + LOCK_MS;
      ss.set(LOCK_KEY, String(u));
      setUntil(u); setNow(Date.now()); setTries(0);
      setErr("Demasiados intentos. Esperá 30 segundos.");
    } else { input.current?.focus(); setTries(n); setErr(`PIN incorrecto. Te queda${MAX_TRIES - n === 1 ? "" : "n"} ${MAX_TRIES - n} intento${MAX_TRIES - n === 1 ? "" : "s"}.`); }
  };
  const onChange = (v: string) => {
    const d = v.replace(/\D/g, "").slice(0, 4);
    setPin(d); setErr(locked ? err : null);
    if (d.length === 4) void check(d);
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void check(pin); };

  if (ok) return <>{children}</>;
  const secs = Math.max(0, Math.ceil((until - now) / 1000));
  return (
    <main className="demo-gate">
      <form className="demo-gate__card" onSubmit={submit} noValidate>
        <img src={`${import.meta.env.BASE_URL}logo.png`} alt="Crimson Consulting" width={180} height={42} />
        <span className="demo-gate__eyebrow"><i aria-hidden="true" />Tienda Crimson · Vista previa</span>
        <h1>Ingresá el PIN de acceso</h1>
        <p>Demostración privada de la tienda en línea. No se procesan pedidos, pagos ni solicitudes.</p>
        <label className="demo-gate__pin">
          <span className="demo-gate__sr">PIN de 4 dígitos</span>
          <input
            ref={input} type="password" inputMode="numeric" pattern="[0-9]*" autoComplete="off" maxLength={4} enterKeyHint="go"
            value={pin} onChange={(e) => onChange(e.target.value)} disabled={locked} aria-invalid={!!err} aria-describedby="demo-gate-msg"
          />
          <span className="demo-gate__dots" aria-hidden="true">{[0, 1, 2, 3].map((i) => <i key={i} data-on={i < pin.length} />)}</span>
        </label>
        <p id="demo-gate-msg" className="demo-gate__msg" role="status" aria-live="polite">{locked ? `Demasiados intentos. Probá de nuevo en ${secs} s.` : err || " "}</p>
        <button type="submit" className="demo-gate__btn" disabled={locked || busy || pin.length !== 4}>Entrar</button>
      </form>
    </main>
  );
}
