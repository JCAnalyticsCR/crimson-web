/* Autoguardado de formularios largos (Andres: "si clickea fuera del pop up, que le guarde todo lo que hizo hasta el
   ultimo segundo").

   Guarda en el servidor unos segundos despues de cada cambio, al cerrar por cualquier via (clic fuera, Escape, X,
   Cerrar), al ocultar la pagina (cambio de app o de pestana), al salir y al navegar a otra pantalla. Si la red
   falla deja una copia en este dispositivo (localStorage) y la sube sola cuando vuelve la conexion; al abrir el
   registro ofrece recuperar una copia que no llego al servidor.

   El hook no sabe nada del formulario: compara `serialize(valor)` con lo ultimo que acepto el servidor y llama a
   `save`. Un registro nuevo (sin id) se crea en el primer guardado y despues sigue por id.

   Sesion = un registro abierto. Empieza cuando el valor pasa de null a algo o cuando cambia el id (otro registro);
   pasar de "sin id" a "con id" es el mismo registro recien creado. Todo guardado lleva su sesion: lo que vuelve
   tarde de un registro ya cerrado nunca se mezcla con el que esta abierto. */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type AutosaveState = "idle" | "pending" | "saving" | "saved" | "local" | "error" | "warn";
export type AutosaveStatus = { state: AutosaveState; at: number | null; message: string | null };
export type AutosaveReason = "debounce" | "close" | "hidden" | "unload" | "online" | "retry";
type Id = number | string;
/** Lo que devuelve `save`: el id si lo creo y, si solo se pudo guardar una parte, que quedo afuera. */
export type SaveResult = { id?: Id; warning?: string | null } | void;
export type SaveCtx = { id: Id | undefined; keepalive: boolean; reason: AutosaveReason; isCurrent: () => boolean };
export type Recovery<T> = { at: number; warn: string | null; data: T };

type Local<T> = { v: 1; at: number; id: Id | null; data: T; warn?: string | null };
type Session<T> = {
  id: Id | undefined; open: boolean; enabled: boolean;
  last: T | null; lastSer: string | null; baseline: string | null;
  takeBaseline: boolean; localKey: string | null; created: number; failed: boolean; notified: boolean;
};

const PREFIX = "crimson:autosave:";
const RETRY_MS = 20_000;

/* localStorage puede no existir o estar lleno (modo privado, cuota): nunca rompe el formulario. */
const ls = {
  get<T>(k: string): Local<T> | null { try { const s = localStorage.getItem(k); return s ? (JSON.parse(s) as Local<T>) : null; } catch { return null; } },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
  del(k: string) { try { localStorage.removeItem(k); } catch { /* sin almacenamiento */ } },
  keys(prefix: string): string[] {
    try { const out: string[] = []; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k && k.startsWith(prefix)) out.push(k); } return out; } catch { return []; }
  },
};

/** Sin red (o el servidor no responde): se reintenta solo. Un 4xx es una respuesta: no se insiste. */
export const isOffline = (e: unknown) =>
  (typeof navigator !== "undefined" && navigator.onLine === false) || e instanceof TypeError || (e instanceof ApiError && (e.status >= 500 || e.status === 0));

/** Copias locales pendientes de un prefijo (p. ej. levantamientos nuevos que no llegaron a crearse). */
export function pendingCopies<T>(storage: string): (Local<T> & { key: string })[] {
  return ls.keys(`${PREFIX}${storage}:`).map((key) => ({ key, ...(ls.get<T>(key) as Local<T>) })).filter((x) => x && x.data).sort((a, b) => b.at - a.at);
}
export const dropCopy = (key: string) => ls.del(key);

export function useAutosave<T>(opts: {
  value: T | null;
  idOf: (v: T) => Id | undefined;
  serialize: (v: T) => string;
  save: (v: T, ctx: SaveCtx) => Promise<SaveResult>;
  /** false: no se guarda nada (solo lectura, sin permiso). */
  enabled: boolean;
  /** Prefijo de la copia local (incluye empresa y usuario). Sin prefijo no hay copia local. */
  storage?: string | null;
  /** Espera despues del ultimo cambio. */
  delay?: number;
  /** false: un registro nuevo no se crea por tiempo, solo al cerrar (quien puede crear pero no editar despues). */
  createEarly?: boolean;
  /** Que paso con un formulario ya cerrado (su linea de estado ya no se ve): para avisar con un toast. */
  notify?: (state: "local" | "error" | "warn" | "saved", value: T, message: string | null) => void;
}) {
  const o = useRef(opts);
  o.current = opts;
  const delay = opts.delay ?? 1800;

  const blank = (): Session<T> => ({ id: undefined, open: false, enabled: false, last: null, lastSer: null, baseline: null, takeBaseline: false, localKey: null, created: Date.now(), failed: false, notified: false });
  const sess = useRef<Session<T>>(blank());
  const timer = useRef<number | undefined>(undefined);
  const localTimer = useRef<number | undefined>(undefined);
  const retryTimer = useRef<number | undefined>(undefined);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const pending = useRef(new Set<Session<T>>()); // sesiones con cambios que no llegaron (sin red)
  const [status, setStatusState] = useState<AutosaveStatus>({ state: "idle", at: null, message: null });
  const [recovery, setRecovery] = useState<(Recovery<T> & { key: string }) | null>(null);

  const setStatus = (s: Session<T>, st: AutosaveStatus) => { if (sess.current === s) setStatusState(st); };
  const dirty = (s: Session<T>) => !!s.last && s.lastSer !== s.baseline;
  const keyFor = (s: Session<T>) => {
    const st = o.current.storage;
    if (!st) return null;
    return `${PREFIX}${st}:${s.id !== undefined ? s.id : `nuevo-${s.created}`}`;
  };

  const writeLocal = (s: Session<T>, warn: string | null = null) => {
    if (!s.last || !s.enabled) return;
    const key = s.localKey || keyFor(s);
    if (!key) return;
    s.localKey = key;
    ls.set(key, { v: 1, at: Date.now(), id: s.id ?? null, data: s.last, warn } satisfies Local<T>);
  };
  const dropLocal = (s: Session<T>) => { if (s.localKey) ls.del(s.localKey); s.localKey = null; };
  /* El registro nuevo ya tiene id: su copia pasa de "nuevo-..." a la del id. */
  const migrateLocal = (s: Session<T>) => {
    const want = keyFor(s);
    if (s.localKey && want && s.localKey !== want) { ls.del(s.localKey); s.localKey = null; if (dirty(s)) writeLocal(s); }
  };

  const armRetry = () => {
    if (retryTimer.current) return;
    retryTimer.current = window.setTimeout(() => { retryTimer.current = undefined; retryAll("retry"); }, RETRY_MS);
  };

  const persist = (s: Session<T>, reason: AutosaveReason): Promise<boolean> => {
    const job = async (): Promise<boolean> => {
      if (!s.enabled || !dirty(s)) return true;
      const v = s.last as T, ser = s.lastSer as string;
      // un registro nuevo al salir no se crea a ciegas (si la respuesta no llega, recuperarlo lo duplicaria)
      // y quien no puede editar despues de crear lo crea una sola vez, al cerrar, con todo lo escrito
      const early = o.current.createEarly !== false;
      if (s.id === undefined && (reason === "unload" || (!early && s.open && reason !== "close"))) { writeLocal(s); return false; }
      setStatus(s, { state: "saving", at: null, message: null });
      try {
        const r = await o.current.save(v, { id: s.id, keepalive: reason === "unload" || reason === "hidden", reason, isCurrent: () => sess.current === s && s.open });
        if (r && r.id !== undefined && s.id === undefined) { s.id = r.id; migrateLocal(s); }
        s.baseline = ser;
        if (s.notified) { s.notified = false; o.current.notify?.("saved", v, null); } // subio lo que habia quedado en el dispositivo
        s.failed = false;
        pending.current.delete(s);
        if (r && r.warning) {
          writeLocal(s, r.warning); // lo que el servidor no acepto queda aqui, completo
          if (sess.current !== s || !s.open) o.current.notify?.("warn", v, r.warning);
          setStatus(s, { state: "warn", at: Date.now(), message: r.warning });
          return false;
        }
        if (s.lastSer === ser) dropLocal(s);
        setStatus(s, dirty(s) ? { state: "pending", at: null, message: null } : { state: "saved", at: Date.now(), message: null });
        return true;
      } catch (e) {
        const offline = isOffline(e);
        const msg = e instanceof Error ? e.message : "No se pudo guardar";
        writeLocal(s, offline ? null : msg);
        // el formulario ya se cerro: nadie ve la linea de estado, se avisa aparte (una vez, no en cada reintento)
        if (!s.notified && (sess.current !== s || !s.open)) { s.notified = true; o.current.notify?.(offline ? "local" : "error", s.last as T, offline ? null : msg); }
        s.failed = true;
        if (offline) {
          pending.current.add(s);
          armRetry();
          setStatus(s, { state: "local", at: Date.now(), message: null });
        } else {
          setStatus(s, { state: "error", at: Date.now(), message: msg });
        }
        return false;
      }
    };
    if (reason === "unload") return job(); // la pagina se va: no hay tiempo de esperar la fila
    const p = chain.current.then(job, job);
    chain.current = p.catch(() => false);
    return p;
  };

  const retryAll = (reason: AutosaveReason) => {
    const list = [...pending.current];
    const cur = sess.current;
    if (cur.open && cur.failed && dirty(cur) && !list.includes(cur)) list.push(cur);
    for (const s of list) void persist(s, reason);
  };

  const clearTimer = () => { window.clearTimeout(timer.current); timer.current = undefined; };

  /* ---------- cada cambio del valor ---------- */
  useEffect(() => {
    const { value, enabled } = o.current;
    const s = sess.current;
    if (value === null) {
      if (s.open) {
        clearTimer();
        s.open = false;
        if (dirty(s)) { writeLocal(s); void persist(s, "close"); } // cerrar nunca descarta
        setRecovery(null);
      }
      return;
    }
    const id = o.current.idOf(value);
    const ser = o.current.serialize(value);
    if (!s.open || (id !== undefined && s.id !== undefined && id !== s.id)) {
      // otro registro: si el anterior tenia cambios, se guardan antes de soltarlo
      if (s.open) { clearTimer(); s.open = false; if (dirty(s)) void persist(s, "close"); }
      const n: Session<T> = { ...blank(), id, open: true, enabled, last: value, lastSer: ser, baseline: ser };
      sess.current = n;
      // un cierre anterior de este mismo registro que espera red: su copia se ofrece abajo, no se reintenta aparte
      if (id !== undefined) for (const p of [...pending.current]) if (p.id === id) pending.current.delete(p);
      setStatusState({ state: "idle", at: null, message: null });
      offerRecovery(n, ser);
      return;
    }
    if (s.id === undefined && id !== undefined) { s.id = id; migrateLocal(s); }
    s.last = value;
    s.lastSer = ser;
    s.enabled = enabled;
    if (s.takeBaseline) { // recargado desde el servidor: es la nueva referencia, no un cambio del usuario
      s.takeBaseline = false;
      s.baseline = ser;
      clearTimer();
      dropLocal(s);
      return;
    }
    if (!enabled || ser === s.baseline) { clearTimer(); return; }
    setStatusState((st) => (st.state === "local" || st.state === "error" || st.state === "warn" ? st : { state: "pending", at: null, message: null }));
    // copia local casi inmediata (por si la pagina muere antes del guardado); el servidor espera la pausa
    window.clearTimeout(localTimer.current);
    localTimer.current = window.setTimeout(() => { if (sess.current === s && dirty(s)) writeLocal(s); }, 400);
    clearTimer();
    timer.current = window.setTimeout(() => { timer.current = undefined; void persist(s, "debounce"); }, delay);
  }, [opts.value, opts.enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- pagina oculta, salida, vuelta de la red, desmontaje ---------- */
  useEffect(() => {
    const hide = () => {
      const s = sess.current;
      if (document.visibilityState !== "hidden" || !s.open || !dirty(s)) return;
      clearTimer();
      writeLocal(s);
      void persist(s, "hidden");
    };
    const leave = () => {
      const s = sess.current;
      if (!s.open || !dirty(s)) return;
      clearTimer();
      writeLocal(s);
      void persist(s, "unload");
    };
    const online = () => retryAll("online");
    document.addEventListener("visibilitychange", hide);
    window.addEventListener("pagehide", leave);
    window.addEventListener("beforeunload", leave);
    window.addEventListener("online", online);
    return () => {
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("beforeunload", leave);
      window.removeEventListener("online", online);
      window.clearTimeout(retryTimer.current);
      window.clearTimeout(localTimer.current);
      // se navega a otra pantalla con el formulario abierto: se guarda igual
      const s = sess.current;
      clearTimer();
      if (s.open && dirty(s)) { writeLocal(s); void persist(s, "close"); }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /* ---------- recuperar una copia local al abrir ---------- */
  const offerRecovery = (s: Session<T>, ser: string) => {
    const st = o.current.storage;
    if (!st || !s.enabled) { setRecovery(null); return; }
    let found: (Local<T> & { key: string }) | null = null;
    if (s.id !== undefined) {
      const key = `${PREFIX}${st}:${s.id}`;
      const c = ls.get<T>(key);
      if (c && c.data) found = { ...c, key };
    } else {
      // la de un registro nuevo que este mismo pantallazo todavia intenta subir no se ofrece (se crearia dos veces)
      const busy = new Set([...pending.current].map((p) => p.localKey));
      found = pendingCopies<T>(st).find((c) => c.id === null && !busy.has(c.key)) || null;
    }
    if (!found) { setRecovery(null); return; }
    let same = false;
    try { same = o.current.serialize(found.data) === ser; } catch { same = false; }
    if (same) { ls.del(found.key); setRecovery(null); return; } // el servidor ya lo tiene todo
    setRecovery({ at: found.at, warn: found.warn ?? null, data: found.data, key: found.key });
  };

  /** Toma la copia local: el formulario la aplica (apply) y el hook la guarda como un cambio mas. */
  const takeRecovery = useCallback(() => {
    const r = recovery;
    if (!r) return null;
    sess.current.localKey = r.key; // la misma copia se reemplaza al guardar (no queda una vieja colgando)
    setRecovery(null);
    return r.data;
  }, [recovery]);
  const discardRecovery = useCallback(() => { if (recovery) ls.del(recovery.key); setRecovery(null); }, [recovery]);

  /** El proximo valor viene recargado del servidor (no es un cambio del usuario). */
  const fromServer = useCallback(() => { sess.current.takeBaseline = true; }, []);

  /** Guardado manual (boton Guardar, Enviar...): espera un autoguardado en curso, nunca corre a la par (crearia el
      registro dos veces) y deja lo enviado como referencia. Devuelve lo que devuelve fn. */
  const exclusive = useCallback(<R extends { id?: Id }>(value: T, fn: (id: Id | undefined) => Promise<R>): Promise<R> => {
    clearTimer();
    const s = sess.current;
    const run = async () => {
      const ser = o.current.serialize(value);
      const r = await fn(o.current.idOf(value) ?? s.id);
      if (r && r.id !== undefined && s.id === undefined) { s.id = r.id; migrateLocal(s); }
      s.baseline = ser;
      s.failed = false;
      pending.current.delete(s);
      if (s.lastSer === ser) { dropLocal(s); setStatus(s, { state: "saved", at: Date.now(), message: null }); }
      else if (s.open && s.enabled) { timer.current = window.setTimeout(() => { timer.current = undefined; void persist(s, "debounce"); }, delay); }
      return r;
    };
    const p = chain.current.then(run, run);
    chain.current = p.catch(() => false);
    return p;
  }, [delay]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Guarda ya lo pendiente del registro abierto (true = el servidor lo tiene todo). */
  const flush = useCallback(() => { clearTimer(); const s = sess.current; return s.open && dirty(s) ? persist(s, "close") : Promise.resolve(true); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return { status, recovery, takeRecovery, discardRecovery, fromServer, exclusive, flush };
}
export type Autosave<T> = ReturnType<typeof useAutosave<T>>;
