/* Fotos y adjuntos privados: la URL que se guarda es estable (/api/media/f/<llave>), pero un archivo privado
   solo se entrega con firma que vence. Antes de mostrar una foto se pide su URL "para ver" a POST /media/sign
   (un solo pedido por tanda, con cache). Las publicas vuelven igual; las que el usuario no puede ver no vuelven. */
import { useEffect, useState } from "react";
import { api } from "./api";

type Entry = { url: string; until: number };
const cache = new Map<string, Entry>();
let pending = new Set<string>();
let timer: ReturnType<typeof setTimeout> | null = null;
let waiters: (() => void)[] = [];

const MARGIN = 120; // segundos: se renueva antes de que venza para no mostrar una foto rota

/** Solo las de nuestra multimedia y que todavia no traen firma (las del portal del cliente ya vienen firmadas). */
export function needsSign(u: string | null | undefined): u is string {
  return !!u && u.includes("/media/f/") && !/[?&]s=/.test(u);
}

function fresh(u: string): boolean {
  const e = cache.get(u);
  return !!e && e.until - MARGIN > Date.now() / 1000;
}

async function flush() {
  timer = null;
  const urls = [...pending];
  pending = new Set();
  const done = waiters;
  waiters = [];
  for (let i = 0; i < urls.length; i += 200) {
    const part = urls.slice(i, i + 200);
    try {
      const r = await api<{ urls: Record<string, string>; expires_at: number | null }>("/media/sign", { method: "POST", json: { urls: part } });
      for (const u of part) {
        const s = r.urls[u];
        // publica: misma URL, sin vencimiento. Privada: hasta la expiracion de su firma. Sin acceso: vacia un rato
        if (s === undefined) cache.set(u, { url: "", until: Date.now() / 1000 + 300 });
        else if (s === u) cache.set(u, { url: s, until: Number.POSITIVE_INFINITY });
        else cache.set(u, { url: s, until: Number(new URL(s, location.origin).searchParams.get("e")) || Date.now() / 1000 + 600 });
      }
    } catch {
      // sin sesion interna (p. ej. portal del cliente) o API caida: se intenta con la URL tal cual
      for (const u of part) cache.set(u, { url: u, until: Date.now() / 1000 + 300 });
    }
  }
  done.forEach((f) => f());
}

function request(urls: string[]): Promise<void> {
  urls.forEach((u) => pending.add(u));
  return new Promise((res) => {
    waiters.push(res);
    if (!timer) timer = setTimeout(flush, 15); // junta las fotos de toda la pantalla en un solo pedido
  });
}

/** URLs listas para <img> o background. "" mientras se firma (o si el usuario no puede ver esa foto). */
export function useMediaUrls(urls: string[], enabled = true): string[] {
  const [, setTick] = useState(0);
  const key = urls.join("\n");
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let renew: ReturnType<typeof setTimeout> | null = null;
    const run = () => {
      const missing = urls.filter((u) => needsSign(u) && !fresh(u));
      if (!missing.length) return schedule();
      request([...new Set(missing)]).then(() => { if (alive) { setTick((n) => n + 1); schedule(); } });
    };
    // cuando la firma mas proxima este por vencer, se pide de nuevo (pantallas que quedan abiertas)
    const schedule = () => {
      const until = Math.min(...urls.filter(needsSign).map((u) => cache.get(u)?.until ?? Number.POSITIVE_INFINITY));
      if (Number.isFinite(until)) renew = setTimeout(run, Math.max(5, until - MARGIN - Date.now() / 1000) * 1000);
    };
    run();
    return () => { alive = false; if (renew) clearTimeout(renew); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
  if (!enabled) return urls;
  // mientras se renueva se sigue usando la firma anterior (todavia vigente por MARGIN)
  return urls.map((u) => (needsSign(u) ? cache.get(u)?.url ?? "" : u));
}

export function useMediaUrl(url: string | null | undefined, enabled = true): string {
  return useMediaUrls(url ? [url] : [], enabled)[0] ?? "";
}
