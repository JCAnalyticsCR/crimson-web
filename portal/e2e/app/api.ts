/* API de las pruebas de Playwright: el mock de la demo (src/demo/mockApi.ts) con el catalogo ficticio de datos.ts y
   dos agregados que la demo no tiene y la tienda real si:

   1. GET /public/store/<slug>/snapshot — el catalogo pre-armado que la tienda real pide al entrar (useSnapshot.ts).
      Sin el, listado, busqueda y portada caerian al camino de respaldo (/catalog) y las capturas no mostrarian lo
      que ve el cliente. Misma forma que api/app/services/store_snapshot.py (contrato v1).
   2. La disponibilidad con la forma completa del API (state, label, cantidad, max_qty, disponible, accion, plazo,
      ultimas), incluido el estado «consultar». El mock solo conoce tres estados simplificados; aqui se reemplaza
      `availability` en cada respuesta por la de datos.ts.

   Todo ocurre dentro del navegador: nada sale a un servidor. */
import { Catalog, productPath, productType, DEFAULT_TRUST, type RawProduct } from "../../src/demo/catalog";
import { installMockApi } from "../../src/demo/mockApi";
import type { SnapProduct } from "../../src/modules/store/catalogFilter";
import { CATALOGO, DISPONIBILIDAD } from "./datos";

const SLUG = "crimson";
const ETAG = '"e2e-1"';

/** nombre + marca + modelo en minusculas y sin tildes (store_snapshot._sf) */
const sf = (p: RawProduct) => [p.name, p.brand].filter(Boolean).join(" ").normalize("NFD").replace(/[^\x00-\x7f]/g, "").toLowerCase();

function snapshot() {
  const c = new Catalog(CATALOGO);
  const prods = c.prods;
  const products: SnapProduct[] = prods.map((p, i) => ({
    id: p.id, path: productPath(p), name: p.name, brand: p.brand, model: null, code: p.code, category_id: p.cat,
    display_price: c.gross(p), web_featured: false, image: p.images[0] || null, type: productType(p) || null,
    created_ts: 1_760_000_000 + Number(p.mod) * 86_400 + i, // mas nuevo = `mod` mas alto, como el orden «Novedades» del mock
    availability: { ...DISPONIBILIDAD[p.id] },
    sf: sf(p),
  }));
  const cuenta = (key: (p: RawProduct) => string | null) => {
    const m = new Map<string, number>();
    for (const p of prods) { const k = key(p); if (k) m.set(k, (m.get(k) || 0) + 1); }
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  };
  return {
    v: 1, version: "1762800000000", built_at: "2026-11-10T18:00:00+00:00", currency: "CRC", n: products.length,
    products,
    categories: c.tree(c.counts()), // arbol con hijas en `children`, como lo manda el API (useSnapshot lo aplana)
    brands: cuenta((p) => p.brand), types: cuenta((p) => productType(p) || null),
    showcase: { headline: null, subline: null, featured: [], banners: [], trust: DEFAULT_TRUST },
  };
}

/** Pone la disponibilidad de datos.ts en todo objeto que parezca una tarjeta de producto ({ id, availability }). */
function conDisponibilidad(x: unknown): unknown {
  if (Array.isArray(x)) return x.map(conDisponibilidad);
  if (!x || typeof x !== "object") return x;
  const o = x as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(o)) out[k] = conDisponibilidad(v);
  const d = typeof o.id === "number" ? DISPONIBILIDAD[o.id] : undefined;
  if (d && o.availability && typeof o.availability === "object") out.availability = { ...d };
  return out;
}

/** Instala el mock de la demo con el catalogo ficticio y, encima, el snapshot y la disponibilidad completa. */
export function instalarApiE2e() {
  installMockApi({ snapshot: CATALOGO });
  const mock = window.fetch.bind(window);
  let snap: string | null = null;
  // Las pruebas preguntan cuantas llamadas al API siguen en curso: como todo se responde dentro del navegador,
  // Playwright no las ve pasar por la red y no tiene otra forma de saber que la pagina ya termino de pedir datos.
  let enCurso = 0;
  (window as Window & { __e2e?: { enCurso: () => number } }).__e2e = { enCurso: () => enCurso };
  const pausa = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
  let snapshotServido = () => {};
  const trasSnapshot = new Promise<void>((r) => { snapshotServido = r; });
  const atender = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, location.href);
    const base = `/api/public/store/${SLUG}`;
    if (url.origin !== location.origin || !url.pathname.startsWith(base)) return mock(input, init);
    const sub = url.pathname.slice(base.length);
    const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();

    if (method === "GET" && sub === "/snapshot") {
      snapshotServido();
      const pide = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined)).get("If-None-Match");
      if (pide === ETAG) return new Response(null, { status: 304, headers: { ETag: ETAG } });
      snap ??= JSON.stringify(snapshot());
      return new Response(snap, { status: 200, headers: { "Content-Type": "application/json", ETag: ETAG, "Cache-Control": "public, max-age=0, must-revalidate" } });
    }
    /* ORDEN FIJO EN LA PORTADA: /showcase siempre responde despues del snapshot, que es lo normal en produccion (el
       snapshot suele estar en cache). Sin esto la portada cambia segun cual llega antes: si /showcase gana por poco,
       StoreHome (efecto «if (snapD && !d) setD(snapD)») lo pisa con los datos del snapshot y la portada se queda sin
       kits y con otra fila de «Lo más pedido». Es una carrera real de la tienda, anotada para quien toque
       StoreHome.tsx; aqui solo se evita que haga cambiar las capturas de una corrida a otra. */
    if (method === "GET" && sub === "/showcase") {
      await Promise.race([trasSnapshot, pausa(3000)]);
      await pausa(200);
    }

    const res = await mock(input, init);
    if (!res.ok || !(res.headers.get("Content-Type") || "").includes("json")) return res;
    const body: unknown = await res.json();
    // /availability?ids= devuelve la disponibilidad plana por id: { items: [{ id, state, label, ... }] }
    const fixed = method === "GET" && sub === "/availability" && body && typeof body === "object" && Array.isArray((body as { items?: unknown }).items)
      ? { items: (body as { items: { id: number }[] }).items.map((it) => (DISPONIBILIDAD[it.id] ? { id: it.id, ...DISPONIBILIDAD[it.id] } : it)) }
      : conDisponibilidad(body);
    return new Response(JSON.stringify(fixed), { status: res.status, headers: res.headers });
  };
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    enCurso++;
    try { return await atender(input, init); } finally { enCurso--; }
  };
}
