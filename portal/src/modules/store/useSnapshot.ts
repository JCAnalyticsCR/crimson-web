/**
 * Snapshot del catálogo: se pide una vez por sesión (ETag + If-None-Match),
 * se guarda en IndexedDB (fallback: localStorage, fallback: solo memoria)
 * y se comparte entre todos los componentes de la tienda sin refetch al navegar.
 */
import { useEffect, useState } from "react";
import type { SnapCategory, Snapshot } from "./catalogFilter";

// ── Caché con triple fallback ─────────────────────────────────────────────────

const IDB_DB = "crimson-snap";
const IDB_VER = 1;
const IDB_STORE = "snaps";

type CacheEntry = { snap: Snapshot; etag: string };

async function idbGet(slug: string): Promise<CacheEntry | null> {
  return new Promise((ok) => {
    try {
      const rq = indexedDB.open(IDB_DB, IDB_VER);
      rq.onupgradeneeded = () => rq.result.createObjectStore(IDB_STORE);
      rq.onsuccess = () => {
        try {
          const tx = rq.result.transaction(IDB_STORE, "readonly");
          const g = tx.objectStore(IDB_STORE).get(slug);
          g.onsuccess = () => ok((g.result as CacheEntry | undefined) ?? null);
          g.onerror = () => ok(null);
        } catch { ok(null); }
      };
      rq.onerror = () => ok(null);
    } catch { ok(null); }
  });
}

async function idbSet(slug: string, entry: CacheEntry): Promise<void> {
  return new Promise((ok) => {
    try {
      const rq = indexedDB.open(IDB_DB, IDB_VER);
      rq.onupgradeneeded = () => rq.result.createObjectStore(IDB_STORE);
      rq.onsuccess = () => {
        try {
          const tx = rq.result.transaction(IDB_STORE, "readwrite");
          tx.objectStore(IDB_STORE).put(entry, slug);
          tx.oncomplete = () => ok();
          tx.onerror = () => ok();
        } catch { ok(); }
      };
      rq.onerror = () => ok();
    } catch { ok(); }
  });
}

async function cacheGet(slug: string): Promise<CacheEntry | null> {
  // Intentar IDB primero
  const idb = await idbGet(slug);
  if (idb) return idb;
  // Fallback a localStorage
  try {
    const raw = localStorage.getItem(`crimson-snap:${slug}`);
    if (raw) return JSON.parse(raw) as CacheEntry;
  } catch { /* bloqueado (Brave Shields, Safari privado, Firefox strict) */ }
  return null;
}

async function cachePut(slug: string, entry: CacheEntry): Promise<void> {
  await idbSet(slug, entry);
  // También persiste en localStorage como respaldo secundario (tamaño permisivo)
  try { localStorage.setItem(`crimson-snap:${slug}`, JSON.stringify(entry)); } catch { /* sin espacio */ }
}

// ── Singleton por slug (compartido en la sesión) ──────────────────────────────

export type SnapState = { snap: Snapshot | null; loading: boolean };

const _states: Record<string, SnapState> = {};
const _listeners: Record<string, Set<(s: SnapState) => void>> = {};
const _started: Record<string, boolean> = {};

/* El servidor manda las categorias como arbol (cada una con sus hijas en children); el filtro trabaja con una lista
   plana con parent_id. Sin esto, una categoria padre (p. ej. Redes y conectividad) solo mostraba sus productos
   directos y no los de sus subcategorias. Acepta tambien el formato plano (cache vieja o servidor futuro). */
type TreeCat = SnapCategory & { children?: TreeCat[] };
function flatCats(cats: TreeCat[], parent: number | null = null, out: SnapCategory[] = []): SnapCategory[] {
  for (const c of cats || []) {
    const { children, ...rest } = c;
    out.push({ ...rest, parent_id: rest.parent_id ?? parent });
    if (children?.length) flatCats(children, c.id, out);
  }
  return out;
}
function norm(snap: Snapshot | null): Snapshot | null {
  return snap ? { ...snap, categories: flatCats(snap.categories as TreeCat[]) } : snap;
}

function emit(slug: string, s: SnapState) {
  s = { ...s, snap: norm(s.snap) };
  _states[slug] = s;
  _listeners[slug]?.forEach((fn) => fn(s));
}

async function bootstrap(slug: string): Promise<void> {
  const url = `/api/public/store/${slug}/snapshot`;

  // Paso 1: mostrar lo que hay en caché inmediatamente (cero latencia)
  const cached = await cacheGet(slug);
  if (cached) emit(slug, { snap: cached.snap, loading: true });

  // Paso 2: revalidar con If-None-Match
  let res: Response | null = null;
  try {
    const headers: HeadersInit = cached?.etag ? { "If-None-Match": cached.etag } : {};
    res = await fetch(url, { headers });
  } catch { /* sin red: usar caché */ }

  if (!res || res.status === 304) {
    // 304 o sin red: lo que hay en caché es la respuesta definitiva
    emit(slug, { snap: cached?.snap ?? null, loading: false });
    return;
  }
  if (!res.ok) {
    emit(slug, { snap: cached?.snap ?? null, loading: false });
    return;
  }

  try {
    const snap = (await res.json()) as Snapshot;
    const etag = res.headers.get("ETag") ?? "";
    await cachePut(slug, { snap, etag });
    emit(slug, { snap, loading: false });
  } catch {
    emit(slug, { snap: cached?.snap ?? null, loading: false });
  }
}

/**
 * Snapshot del catálogo, compartido entre páginas de la misma tienda.
 * Muestra inmediatamente lo que haya en caché y revalida con If-None-Match.
 * Si el snapshot no está disponible (primera visita sin red), retorna snap: null.
 */
export function useSnapshot(slug: string): SnapState {
  const [state, setState] = useState<SnapState>(
    () => _states[slug] ?? { snap: null, loading: true }
  );

  useEffect(() => {
    if (!_listeners[slug]) _listeners[slug] = new Set();
    _listeners[slug].add(setState);

    // Sincronizar con el estado actual si ya hubo una emisión previa
    if (_states[slug]) setState(_states[slug]);

    if (!_started[slug]) {
      _started[slug] = true;
      bootstrap(slug);
    }

    return () => { _listeners[slug]?.delete(setState); };
  }, [slug]);

  return state;
}
