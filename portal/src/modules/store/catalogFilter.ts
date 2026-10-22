/**
 * Filtrado, ordenamiento y facetas del catálogo completamente en el navegador.
 * Replica api/app/routers/store_showcase.py catalog() con los mismos criterios.
 * Módulo puro: sin efectos secundarios, sin imports de React. Fácil de probar en Vitest.
 */
import type { Avail, VProd } from "./storeKit";

// ── Tipos del snapshot (contrato JSON en api/app/services/store_snapshot.py) ─

export type SnapAvail = {
  state: string; label: string;
  accion: string | null; max_qty: number | null; plazo: string | null;
};

export type SnapProduct = {
  id: number; path: string; name: string;
  brand: string | null; model: string | null; code: string;
  category_id: number | null; display_price: number;
  web_featured: boolean; image: string | null;
  type: string | null; created_ts: number;
  availability: SnapAvail; sf: string;
};

export type SnapCategory = {
  id: number; name: string; slug: string;
  parent_id: number | null; count: number;
};

export type SnapShowcase = {
  headline: string | null; subline: string | null; featured: number[];
  banners: Array<{
    id?: string; image?: string; title?: string; text?: string;
    link?: string; cta?: string; active?: boolean;
  }>;
  trust: Array<{ icon: string; title: string; text: string }>;
};

export type Snapshot = {
  v: number; version: string; built_at: string;
  currency: string; n: number;
  products: SnapProduct[];
  categories: SnapCategory[];
  brands: Array<{ name: string; count: number }>;
  types: Array<{ name: string; count: number }>;
  showcase: SnapShowcase;
};

// ── Tipos de resultados ───────────────────────────────────────────────────────

export type CatalogParams = {
  q?: string; category?: string;
  brands?: string[]; tipo?: string;
  min?: number; max?: number;
  avail?: string; sort?: string;
  limit?: number; offset?: number;
};

export type CatalogFacets = {
  brands: { name: string; count: number }[];
  types: { name: string; count: number }[];
  availability: { disponible: number; bajo_pedido: number };
  price: { min: number; max: number };
};

export type CatalogResult = {
  items: SnapProduct[];
  total: number;
  offset: number;
  facets: CatalogFacets;
  currency: string;
  category: {
    id: number; name: string; slug: string;
    description: null;
    parent: { name: string; path: string } | null;
    children: { name: string; path: string }[];
  } | null;
  nav: { name: string; path: string; count: number }[];
};

// ── Utilidades internas ───────────────────────────────────────────────────────

/** Descendientes (inclusive) de una categoría en el árbol plano del snapshot. */
export function descendants(cats: SnapCategory[], cid: number): Set<number> {
  const out = new Set<number>([cid]);
  const todo = [cid];
  while (todo.length) {
    const cur = todo.pop()!;
    for (const c of cats) {
      if (c.parent_id === cur && !out.has(c.id)) {
        out.add(c.id);
        todo.push(c.id);
      }
    }
  }
  return out;
}

/** Normaliza igual que el backend: NFD → quita diacríticos → minúsculas. */
function norm(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Divide la consulta en términos normalizados (igual que sv.split_terms). */
function splitTerms(q: string): string[] {
  return norm(q).split(/\s+/).filter(Boolean);
}

/** Score de búsqueda: 0 = no coincide, >0 = número de términos en sf.
 *  AND estricto: todos los términos deben aparecer en sf. */
function scoreSearch(sf: string, terms: string[]): number {
  if (!terms.length) return 0;
  let n = 0;
  for (const t of terms) {
    if (!sf.includes(t)) return 0;
    n++;
  }
  return n;
}

/** Prioridad de disponibilidad (aproximación de sort_key del backend). */
function availOrd(state: string): number {
  return state === "en_bodega" ? 0 : state === "bajo_pedido" ? 1 : 2;
}

/** Orden por defecto (replica _rank_default del backend). */
function cmpDefault(a: SnapProduct, b: SnapProduct): number {
  if (a.web_featured !== b.web_featured) return a.web_featured ? -1 : 1;
  const ah = (a.brand ?? "").toLowerCase() === "hikvision";
  const bh = (b.brand ?? "").toLowerCase() === "hikvision";
  if (ah !== bh) return ah ? -1 : 1;
  if (!!a.image !== !!b.image) return a.image ? -1 : 1;
  if ((a.display_price > 0) !== (b.display_price > 0)) return a.display_price > 0 ? -1 : 1;
  return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
}

// ── Función principal ─────────────────────────────────────────────────────────

/**
 * Filtra, ordena y calcula facetas del catálogo desde el snapshot.
 * Sin llamadas al API: todo es synchronous y en memoria.
 */
export function filterCatalog(snap: Snapshot, params: CatalogParams): CatalogResult {
  const {
    q = "", category, brands: brandsParam = [], tipo,
    min, max, avail, sort = "relevance", limit = 24, offset = 0,
  } = params;

  const catById = new Map(snap.categories.map((c) => [c.id, c]));
  const catBySlug = new Map(snap.categories.map((c) => [c.slug, c]));

  // Mapa hijo → padre para el árbol
  const childrenOf = new Map<number | null, SnapCategory[]>();
  for (const c of snap.categories) {
    const pid = c.parent_id != null && catById.has(c.parent_id) ? c.parent_id : null;
    if (!childrenOf.has(pid)) childrenOf.set(pid, []);
    childrenOf.get(pid)!.push(c);
  }

  // Conteos directos por categoría (desde los productos del snapshot)
  const directCount = new Map<number, number>();
  for (const p of snap.products) {
    if (p.category_id != null) {
      directCount.set(p.category_id, (directCount.get(p.category_id) ?? 0) + 1);
    }
  }
  const totMemo = new Map<number, number>();
  function totalCount(cid: number): number {
    if (totMemo.has(cid)) return totMemo.get(cid)!;
    let n = directCount.get(cid) ?? 0;
    for (const ch of childrenOf.get(cid) ?? []) n += totalCount(ch.id);
    totMemo.set(cid, n);
    return n;
  }

  // Navegación de categorías raíz (igual que nav[] del backend)
  const nav = (childrenOf.get(null) ?? [])
    .map((c) => ({ name: c.name, path: `/c/${c.slug}`, count: totalCount(c.id) }))
    .filter((c) => c.count > 0)
    .sort((a, b) => b.count - a.count);

  // ── Pool base ───────────────────────────────────────────────────────────────
  let pool: SnapProduct[] = [...snap.products];
  let catObj: SnapCategory | undefined;

  if (category) {
    catObj =
      catBySlug.get(category) ??
      (/^\d+$/.test(category) ? catById.get(Number(category)) : undefined);
    if (catObj) {
      const allowed = descendants(snap.categories, catObj.id);
      pool = pool.filter((p) => p.category_id != null && allowed.has(p.category_id));
    }
  }

  // Búsqueda por términos en el campo sf (normalizado por el backend)
  const terms = splitTerms(q);
  const scores = new Map<number, number>();
  if (terms.length) {
    pool = pool.filter((p) => {
      const s = scoreSearch(p.sf, terms);
      if (s > 0) { scores.set(p.id, s); return true; }
      return false;
    });
  }

  const brandsLow = new Set(brandsParam.filter(Boolean).map((b) => b.toLowerCase()));

  // Replica keep(p, skip) del backend
  function keep(p: SnapProduct, skip = ""): boolean {
    if (
      skip !== "brand" && brandsLow.size > 0 &&
      !brandsLow.has((p.brand ?? "").toLowerCase())
    ) return false;
    if (skip !== "type" && tipo && p.type !== tipo) return false;
    if (skip !== "price") {
      if (min != null && p.display_price < min) return false;
      if (max != null && (p.display_price > max || p.display_price <= 0)) return false;
    }
    if (skip !== "avail" && avail) {
      const st = p.availability.state;
      if ((avail === "disponible" || avail === "inmediata") && st !== "en_bodega") return false;
      if (avail === "bajo_pedido" && st !== "en_bodega" && st !== "bajo_pedido") return false;
    }
    return true;
  }

  const rows = pool.filter((p) => keep(p));

  // ── Facetas (sin el filtro de la propia faceta) ─────────────────────────────
  const fb: Record<string, number> = {};
  for (const p of pool) {
    if (p.brand && keep(p, "brand")) fb[p.brand] = (fb[p.brand] ?? 0) + 1;
  }
  const ft: Record<string, number> = {};
  for (const p of pool) {
    if (p.type && keep(p, "type")) ft[p.type] = (ft[p.type] ?? 0) + 1;
  }
  const fa = { disponible: 0, bajo_pedido: 0 };
  for (const p of pool) {
    if (keep(p, "avail")) {
      const st = p.availability.state;
      if (st === "en_bodega") fa.disponible++;
      if (st === "en_bodega" || st === "bajo_pedido") fa.bajo_pedido++;
    }
  }
  const priced = pool
    .filter((p) => keep(p, "price") && p.display_price > 0)
    .map((p) => p.display_price);

  const facets: CatalogFacets = {
    brands: Object.entries(fb)
      .sort(([a, na], [b, nb]) =>
        a.toLowerCase() === "hikvision" ? -1 : b.toLowerCase() === "hikvision" ? 1 : nb - na
      )
      .slice(0, 30)
      .map(([name, count]) => ({ name, count })),
    types: Object.entries(ft)
      .sort(([, na], [, nb]) => nb - na)
      .map(([name, count]) => ({ name, count })),
    availability: fa,
    price: {
      min: priced.length ? Math.min(...priced) : 0,
      max: priced.length ? Math.max(...priced) : 0,
    },
  };

  // ── Ordenamiento ────────────────────────────────────────────────────────────
  const sorted = [...rows];
  if (sort === "relevance") {
    sorted.sort((a, b) => {
      const sd = (scores.get(b.id) ?? 0) - (scores.get(a.id) ?? 0);
      if (sd !== 0) return sd;
      const od = availOrd(a.availability.state) - availOrd(b.availability.state);
      if (od !== 0) return od;
      return cmpDefault(a, b);
    });
  } else if (sort === "name") {
    sorted.sort((a, b) => {
      if (a.web_featured !== b.web_featured) return a.web_featured ? -1 : 1;
      const ah = (a.brand ?? "").toLowerCase() === "hikvision";
      const bh = (b.brand ?? "").toLowerCase() === "hikvision";
      if (ah !== bh) return ah ? -1 : 1;
      return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    });
  } else if (sort === "price_asc" || sort === "price_desc") {
    const desc = sort === "price_desc";
    const withP = sorted
      .filter((p) => p.display_price > 0)
      .sort((a, b) => desc ? b.display_price - a.display_price : a.display_price - b.display_price);
    const noP = sorted.filter((p) => p.display_price <= 0);
    sorted.splice(0, sorted.length, ...withP, ...noP);
  } else {
    // "recent"
    sorted.sort((a, b) => b.created_ts - a.created_ts || b.id - a.id);
  }

  const total = sorted.length;
  const items = sorted.slice(offset, offset + limit);

  // ── Info de categoría ────────────────────────────────────────────────────────
  let categoryInfo: CatalogResult["category"] = null;
  if (catObj) {
    const parentCat = catObj.parent_id != null ? catById.get(catObj.parent_id) : null;
    categoryInfo = {
      id: catObj.id, name: catObj.name, slug: catObj.slug,
      description: null,
      parent: parentCat ? { name: parentCat.name, path: `/c/${parentCat.slug}` } : null,
      children: (childrenOf.get(catObj.id) ?? []).map((c) => ({
        name: c.name, path: `/c/${c.slug}`,
      })),
    };
  }

  return { items, total, offset, facets, currency: snap.currency, category: categoryInfo, nav };
}

// ── Conversión snapshot → VProd ───────────────────────────────────────────────

/**
 * Convierte un producto del snapshot al tipo VProd que usa la vitrina.
 * Los campos no disponibles en el snapshot (description, specs, etc.) quedan en null/[].
 * La ficha completa los carga via /item/:ref.
 */
export function snapToVProd(p: SnapProduct, currency: string, cats: SnapCategory[]): VProd {
  const cat = cats.find((c) => c.id === p.category_id);
  const avail: Avail = {
    state: p.availability.state,
    label: p.availability.label,
    max_qty: p.availability.max_qty,
    blocked: p.availability.state === "agotado" || p.availability.state === "consultar",
    disponible: p.availability.state === "en_bodega" || p.availability.state === "bajo_pedido",
    cantidad: null,
    ultimas: false,
  };
  return {
    id: p.id,
    code: p.code,
    name: p.name,
    brand: p.brand,
    model: p.model ?? undefined,
    image: p.image,
    images: p.image ? [p.image] : [],
    description: null,
    category: cat?.name ?? null,
    category_id: p.category_id,
    category_path: cat ? `/c/${cat.slug}` : null,
    path: p.path,
    type: p.type ?? undefined,
    tax_rate: 13,
    item_type: "product",
    display_price: p.display_price,
    display_currency: currency,
    compare_display_price: null,
    price_on_request: p.display_price <= 0,
    availability: avail,
    variants: [],
    price: p.display_price,
    currency,
  };
}

/**
 * Extrae el ID numérico del ref de la URL de la ficha (p. ej. "hikvision-ds-2cd-42" → 42).
 * Mismo algoritmo que sv.product_id_from_ref del backend.
 */
export function pidFromRef(ref: string): number | null {
  const m = ref.match(/-(\d+)$/);
  return m ? Number(m[1]) : null;
}
