/* Un solo modelo de guardado para Mi Tienda (O4-02).

   Cada seccion edita un BORRADOR: nada llega a la tienda hasta «Guardar y publicar» de la barra de abajo (SaveBar).
   - Sin guardado al perder el foco ni botones «Guardar» por bloque.
   - Version optimista: cada fuente (/store, /store/showcase, /store/checkout) devuelve su `version`; el PUT la manda
     y si alguien guardo en el medio el API responde 409 «Alguien más guardó cambios en esta sección. Recargue…».
   - Copia local de lo escrito (localStorage, por fuente): si se cierra la pestana sin guardar, al volver se ofrece
     recuperarla (solo si la version no cambio: nunca se pisa lo que otro guardo).
   - Proteccion al cerrar la pestana (beforeunload) mientras haya cambios; el cambio de seccion lo cuida StoreHub.
   - Tras guardar, «Deshacer» vuelve a poner lo de antes (con la version nueva). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../../../lib/api";

export type DraftSource<T extends object> = {
  /** nombre de la fuente para la copia local y los avisos (p. ej. "apariencia:store") */
  key: string;
  url: string;
  /** PUT (ajustes de la tienda, por defecto) o PATCH (un producto) */
  method?: "PUT" | "PATCH";
  /** de la respuesta del GET/PUT, lo que edita esta seccion */
  read: (resp: Record<string, unknown>) => T;
  /** cuerpo del PUT; por defecto, solo los campos que cambiaron mas la version */
  body?: (draft: T, changed: (keyof T)[], version: number | undefined) => unknown;
  /** etiqueta legible de cada campo (para «3 cambios sin guardar» y el historial) */
  labels?: Partial<Record<keyof T, string>>;
};

export type Draft<T extends object> = {
  key: string;
  loading: boolean;
  error: string | null;
  server: T | null;
  draft: T | null;
  version: number | undefined;
  changed: (keyof T)[];
  set: (patch: Partial<T>) => void;
  discard: () => void;
  /** guarda; devuelve la funcion para deshacer (null si no habia nada o fallo) */
  save: () => Promise<{ ok: true; undo: (() => Promise<void>) | null } | { ok: false; error: string; conflict: boolean }>;
  recovery: { at: number } | null;
  recover: () => void;
  dropRecovery: () => void;
  reload: () => void;
};

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
export const CONFLICT = "Alguien más guardó cambios en esta sección. Recargue para verlos.";
const LS = (key: string) => `mi-tienda:borrador:${key}`;

function readLocal<T>(key: string): { at: number; version: number | undefined; draft: T } | null {
  try {
    const raw = localStorage.getItem(LS(key));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
function writeLocal(key: string, v: unknown) {
  try {
    if (v == null) localStorage.removeItem(LS(key));
    else localStorage.setItem(LS(key), JSON.stringify(v));
  } catch { /* sin almacenamiento local: el borrador vive solo en memoria */ }
}

/** campos que difieren entre dos objetos (solo los de primer nivel) */
export function changedKeys<T extends object>(base: T | null, draft: T | null): (keyof T)[] {
  if (!base || !draft) return [];
  const ks = new Set([...Object.keys(base), ...Object.keys(draft)]) as Set<keyof T>;
  return [...ks].filter((k) => !same((base as Record<keyof T, unknown>)[k], (draft as Record<keyof T, unknown>)[k]));
}

export function useDraft<T extends object>(src: DraftSource<T>): Draft<T> {
  const srcRef = useRef(src);
  srcRef.current = src;
  const [server, setServer] = useState<T | null>(null);
  const [draft, setDraft] = useState<T | null>(null);
  const [version, setVersion] = useState<number | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<{ at: number; draft: T } | null>(null);

  const adopt = useCallback((resp: Record<string, unknown>) => {
    const v = srcRef.current.read(resp);
    setServer(v);
    setDraft(v);
    setVersion(typeof resp.version === "number" ? resp.version : undefined);
    return { v, version: typeof resp.version === "number" ? resp.version : undefined };
  }, []);

  const reload = useCallback(() => {
    setLoading(true);
    api<Record<string, unknown>>(srcRef.current.url)
      .then((r) => {
        const { v, version: ver } = adopt(r);
        setError(null);
        const loc = readLocal<T>(srcRef.current.key);
        if (loc && loc.version === ver && changedKeys(v, loc.draft).length) setRecovery({ at: loc.at, draft: loc.draft });
        else { setRecovery(null); if (loc) writeLocal(srcRef.current.key, null); }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [adopt]);
  useEffect(reload, [reload]);

  const changed = useMemo(() => changedKeys(server, draft), [server, draft]);
  const dirty = changed.length > 0;

  // copia local de lo escrito y proteccion al cerrar la pestana
  useEffect(() => {
    if (!server || !draft) return;
    if (dirty) writeLocal(src.key, { at: Date.now(), version, draft });
    else if (!recovery) writeLocal(src.key, null);
  }, [dirty, draft, server, version, src.key, recovery]);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const set = useCallback((patch: Partial<T>) => setDraft((d) => (d ? { ...d, ...patch } : d)), []);
  const discard = useCallback(() => { setDraft(server); writeLocal(srcRef.current.key, null); setRecovery(null); }, [server]);

  const put = useCallback(async (body: unknown) => api<Record<string, unknown>>(srcRef.current.url, { method: srcRef.current.method || "PUT", json: body }), []);

  const save = useCallback(async () => {
    if (!server || !draft || !changed.length) return { ok: true as const, undo: null };
    const s = srcRef.current;
    const body = s.body ? s.body(draft, changed, version) : { ...Object.fromEntries(changed.map((k) => [k, (draft as Record<keyof T, unknown>)[k]])), version };
    const before = server;
    const fields = [...changed];
    try {
      const resp = await put(body);
      const { version: nv } = adopt(resp);
      writeLocal(s.key, null);
      setRecovery(null);
      const undo = async () => {
        const back = { ...draft, ...Object.fromEntries(fields.map((k) => [k, (before as Record<keyof T, unknown>)[k]])) } as T;
        const b = s.body ? s.body(back, fields, nv) : { ...Object.fromEntries(fields.map((k) => [k, (before as Record<keyof T, unknown>)[k]])), version: nv };
        adopt(await put(b));
      };
      return { ok: true as const, undo };
    } catch (e) {
      const conflict = e instanceof ApiError && e.status === 409;
      return { ok: false as const, error: conflict ? CONFLICT : e instanceof Error ? e.message : String(e), conflict };
    }
  }, [server, draft, changed, version, put, adopt]);

  return {
    key: src.key, loading, error, server, draft, version, changed, set, discard, save,
    recovery: recovery ? { at: recovery.at } : null,
    recover: () => { if (recovery) setDraft(recovery.draft); setRecovery(null); },
    dropRecovery: () => { writeLocal(srcRef.current.key, null); setRecovery(null); },
    reload,
  };
}
