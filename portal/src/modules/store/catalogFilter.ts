/**
 * Filtrado, ordenamiento y facetas del catálogo completamente en el navegador.
 * Replica api/app/routers/store_showcase.py catalog() con los mismos criterios.
 * Módulo puro: sin efectos secundarios, sin imports de React. Fácil de probar en Vitest.
 */
import type { Avail, VProd } from "./storeKit";

// ── Tipos del snapshot (contrato JSON en api/app/services/store_snapshot.py) ─

export type SnapAvail = {
  state: string; label: string; label_short?: string | null;
  accion: string | null; max_qty: number | null; plazo: string | null;
  /** se puede comprar en linea (services/availability); viaja en el snapshot v2 */
  disponible?: boolean;
};

export type SnapProduct = {
  id: number; path: string; name: string;
  /** O3-01: nombre comercial aprobado (solo viaja si existe); O3-02: codigo publico */
  display_name?: string | null; public_code?: string | null;
  brand: string | null; model: string | null; code: string;
  category_id: number | null; display_price: number;
  web_featured: boolean; image: string | null;
  type: string | null; created_ts: number;
  availability: SnapAvail; sf: string;
  /** precio anterior tachado (O1-13): solo viaja con historial propio que lo respalde; casi siempre falta */
  compare_display_price?: number | null;
  /** O2-06 (aditivo): datos clave de la tarjeta; un snapshot viejo no los trae */
  ks?: string[];
  /** O3-04: familia (camaras, parlantes, switches, ups) y el valor de cada filtro propio, calculados por el API */
  fam?: string; fa?: Record<string, string>;
  /** O3-08: calidad de ficha (0 a 3) para el orden «Recomendados»; solo viaja con el interruptor encendido */
  q?: number;
};

export type SnapCategory = {
  id: number; name: string; slug: string;
  parent_id: number | null; count: number;
  /** O3-07a: descripcion de la categoria y direcciones viejas (renombrada); solo viajan si hay */
  description?: string | null; aliases?: string[];
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
  /** O1-14: tabla de sinonimos del buscador (raiz -> raices del grupo). Falta en un snapshot viejo: sin sinonimos. */
  syn?: Record<string, string[]>;
  /** O3-08: «recomendados» cuando el dueno encendio el orden nuevo; pins = fijados por categoria (8 como maximo) */
  rank?: string; pins?: Record<string, number[]>;
};

// ── Tipos de resultados ───────────────────────────────────────────────────────

export type CatalogParams = {
  q?: string; category?: string;
  brands?: string[]; tipo?: string;
  min?: number; max?: number;
  avail?: string; sort?: string;
  limit?: number; offset?: number;
  /** O3-04: filtros propios de la familia, «grupo:valor» (?f=) */
  f?: string[];
  /** O1-13: estado en vivo (/availability) de los productos que la pagina ya consulto; manda sobre el del snapshot
      para «Entrega inmediata» y el orden. Estados del API (en_bodega, bajo_pedido...) o de la vitrina (disponible, ultimas). */
  live?: Record<number, string>;
};

/** estado en vivo de la vitrina (normAvail: disponible/ultimas) -> estado del API */
const LIVE: Record<string, string> = { disponible: "en_bodega", ultimas: "en_bodega" };

export type CatalogFacets = {
  brands: { name: string; count: number }[];
  types: { name: string; count: number }[];
  availability: { disponible: number; bajo_pedido: number };
  price: { min: number; max: number };
  /** O3-04: filtros propios de la familia de la pagina (4 como maximo); falta en un API viejo */
  groups?: FamilyGroup[];
};
export type FamilyGroup = { key: string; label: string; values: { name: string; count: number }[] };

/* O3-04: los mismos grupos que api/app/services/family_filters.py (GROUPS y ORDER). Los valores ya vienen calculados
   en el snapshot («fa»); aqui solo se cuentan y se filtran. */
const FAMILY_GROUPS: Record<string, [string, string][]> = {
  camaras: [["resolucion", "Resolución"], ["ambiente", "Interior o exterior"], ["conexion", "Conexión"]],
  parlantes: [["potencia", "Potencia"], ["agua", "Resistencia al agua"]],
  switches: [["puertos", "Puertos"], ["poe", "PoE"]],
  ups: [["capacidad", "Capacidad"]],
};
const FAMILY_ORDER: Record<string, string[]> = {
  resolucion: ["1 MP", "2 MP", "3 MP", "4 MP", "5 MP", "6 MP", "8 MP (4K)", "12 MP"],
  ambiente: ["Interior", "Exterior"],
  conexion: ["WiFi", "PoE", "Analógica"],
  potencia: ["Menos de 10 W", "10 a 30 W", "30 a 100 W", "100 W o más"],
  puertos: ["4 puertos", "5 puertos", "8 puertos", "16 puertos", "24 puertos", "48 puertos"],
  poe: ["Con PoE", "Sin PoE"],
  capacidad: ["Hasta 750 VA", "751 a 1500 VA", "Más de 1500 VA"],
};
/** «grupo:valor» -> {grupo: valor} (el ultimo gana), como family_filters.parse */
export function parseFamilyFilters(values: string[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const v of values || []) { const i = v.indexOf(":"); if (i > 0 && i < v.length - 1) out[v.slice(0, i).trim()] = v.slice(i + 1).trim(); }
  return out;
}
/** la familia de la pagina: la de 60 % o mas de lo que se ve (family_filters.dominant) */
function dominantFamily(fams: (string | undefined)[]): string | null {
  const seen = fams.filter(Boolean) as string[];
  if (!fams.length || !seen.length) return null;
  const n: Record<string, number> = {};
  for (const f of seen) n[f] = (n[f] || 0) + 1;
  const best = Object.entries(n).sort((a, b) => b[1] - a[1])[0];
  return best[1] / fams.length >= 0.6 ? best[0] : null;
}

export type CatalogResult = {
  items: SnapProduct[];
  total: number;
  offset: number;
  facets: CatalogFacets;
  currency: string;
  category: {
    id: number; name: string; slug: string;
    description: string | null;
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

// ── Buscador (O1-14): la MISMA regla que api/app/services/store_search.py ─────
// Si cambias algo aqui, cambia lo mismo alla: la prueba de paridad (busqueda_casos.json) lo exige.

/** minusculas y sin tildes (NFKD y fuera todo lo que no es ASCII, como el `encode("ascii", "ignore")` del API) */
export function fold(s: string): string {
  return (s || "").normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase();
}
export const words = (s: string | null | undefined): string[] => fold(s || "").match(/[a-z0-9]+/g) || [];
export const compact = (s: string | null | undefined): string => words(s).join("");
/** raiz: sin plural (es / s) y sin la vocal final; los numeros y las palabras cortas quedan igual */
export function stem(w: string): string {
  if (w.length <= 3 || /^\d+$/.test(w)) return w;
  if (w.endsWith("es") && w.length > 4 && !"aeiou".includes(w[w.length - 3])) w = w.slice(0, -2);
  else if (w.endsWith("s")) w = w.slice(0, -1);
  if (w.length > 3 && "aeo".includes(w[w.length - 1])) w = w.slice(0, -1);
  return w;
}
/** campo de busqueda (el sf del snapshot): las palabras normalizadas separadas por un espacio */
export const searchField = (...parts: (string | null | undefined)[]) => parts.flatMap((p) => words(p)).join(" ");
const splitQuery = (q: string) => words(q).slice(0, 8);

function termMatches(term: string, stems: string[], comp: string, syn: Record<string, string[]>): boolean {
  const t = stem(term);
  const alts = syn[t] || syn[term] || [t];
  if (stems.some((s) => alts.some((a) => s.startsWith(a)))) return true;
  const c = compact(term);
  return c.length >= 3 && [c, ...(syn[c] || [])].some((a) => a.length >= 3 && comp.includes(a));
}

/** 0 = no coincide. Si coincide: 100 por modelo o codigo, 20 si todas las palabras estan en el nombre, 5 por marca o
    categoria que coincida, y 1 de base (services/store_search.score). */
export function searchScore(q: string, p: { name: string; display_name?: string | null; brand?: string | null; model?: string | null; code?: string | null; sf: string }, category: string | null, syn: Record<string, string[]>): number {
  // O3-01: los dos nombres (el comercial y el de siempre), como store_showcase._names del API
  if (p.display_name && p.display_name !== p.name) p = { ...p, name: `${p.name} ${p.display_name}` };
  const terms = splitQuery(q);
  if (!terms.length) return 0;
  const field = words(p.sf); // un sf viejo (con guiones y puntos) se normaliza igual
  const stems = field.map(stem);
  const comp = field.join("");
  const whole = compact(q);
  const every = terms.every((t) => termMatches(t, stems, comp, syn));
  if (!every && !(whole.length >= 4 && comp.includes(whole))) return 0;
  let pts = 1;
  const codes = [p.model, p.code].filter(Boolean).map((x) => compact(x));
  if (whole.length >= 3 && codes.some((c) => c.startsWith(whole) || c.includes(whole))) pts += 100;
  const nameStems = words(p.name).map(stem);
  if (terms.every((t) => termMatches(t, nameStems, compact(p.name), syn))) pts += 20;
  const side = words([p.brand, category].filter(Boolean).join(" ")).map(stem);
  pts += 5 * terms.filter((t) => termMatches(t, side, "", syn)).length;
  return pts;
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

/* O3-08: «Recomendados» (services/store_ranking.recommended_key): comprable hoy, fijado en su categoria, calidad de
   ficha, precio (los «a consultar» al final), nombre e id. «Nombre»: de la A a la Z por el nombre que se ve. */
const foldKey = (p: SnapProduct) => fold(p.display_name || p.name);
const cmpStr = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
function cmpName(a: SnapProduct, b: SnapProduct): number {
  return cmpStr(foldKey(a), foldKey(b)) || a.id - b.id;
}

// ── Función principal ─────────────────────────────────────────────────────────

/**
 * Filtra, ordena y calcula facetas del catálogo desde el snapshot.
 * Sin llamadas al API: todo es synchronous y en memoria.
 */
export function filterCatalog(snap: Snapshot, params: CatalogParams): CatalogResult {
  const {
    q = "", category, brands: brandsParam = [], tipo,
    min, max, avail, sort = "relevance", limit = 24, offset = 0, live, f,
  } = params;
  const ffil = parseFamilyFilters(f);
  /** el estado que manda: el vivo si la pagina ya lo pidio, si no el del snapshot */
  const stateOf = (p: SnapProduct): string => {
    const v = live?.[p.id];
    return v ? LIVE[v] || v : p.availability?.state || "consultar";
  };

  const catById = new Map(snap.categories.map((c) => [c.id, c]));
  const catBySlug = new Map(snap.categories.map((c) => [c.slug, c]));
  for (const c of snap.categories) for (const a of c.aliases || []) if (!catBySlug.has(a)) catBySlug.set(a, c); // O3-07a

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

  // Búsqueda (O1-14): la misma regla del API sobre el campo sf y la tabla de sinonimos del snapshot
  const terms = splitQuery(q);
  const scores = new Map<number, number>();
  const syn = snap.syn || {};
  if (terms.length) {
    pool = pool.filter((p) => {
      const s = searchScore(q, p, p.category_id != null ? catById.get(p.category_id)?.name ?? null : null, syn);
      if (s > 0) { scores.set(p.id, s); return true; }
      return false;
    });
  }

  // O3-02: la marca se compara en version compacta, como el API (una direccion vieja con «Tp Link» sigue sirviendo)
  const brandsLow = new Set(brandsParam.filter(Boolean).map((b) => compact(b.replace(/&/g, "and"))));

  // Replica keep(p, skip) del backend
  function keep(p: SnapProduct, skip = ""): boolean {
    if (
      skip !== "brand" && brandsLow.size > 0 &&
      !brandsLow.has(compact((p.brand ?? "").replace(/&/g, "and")))
    ) return false;
    if (skip !== "type" && tipo && p.type !== tipo) return false;
    if (skip !== "price") {
      if (min != null && p.display_price < min) return false;
      if (max != null && (p.display_price > max || p.display_price <= 0)) return false;
    }
    if (skip !== "avail" && avail) {
      const st = stateOf(p);
      if ((avail === "disponible" || avail === "inmediata") && st !== "en_bodega") return false;
      if (avail === "bajo_pedido" && st !== "en_bodega" && st !== "bajo_pedido") return false;
    }
    for (const [g, v] of Object.entries(ffil)) if (skip !== g && (p.fa?.[g] ?? null) !== v) return false;
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
      const st = stateOf(p);
      if (st === "en_bodega") fa.disponible++;
      if (st === "en_bodega" || st === "bajo_pedido") fa.bajo_pedido++;
    }
  }
  const priced = pool
    .filter((p) => keep(p, "price") && p.display_price > 0)
    .map((p) => p.display_price);

  // O3-04: los filtros propios de la familia (solo en una categoria; el mismo calculo del API)
  const pageFamily = catObj ? dominantFamily(pool.map((p) => p.fam)) : null;
  const groups: FamilyGroup[] = [];
  for (const [g, label] of (pageFamily ? FAMILY_GROUPS[pageFamily] || [] : []).slice(0, 4)) {
    const cnt: Record<string, number> = {};
    let n = 0;
    for (const p of pool) { if (!keep(p, g)) continue; n++; const v = p.fa?.[g]; if (v) cnt[v] = (cnt[v] || 0) + 1; }
    const sum = Object.values(cnt).reduce((a, b) => a + b, 0);
    if (Object.keys(cnt).length > 1 || (sum > 0 && sum < n) || g in ffil) { // un grupo que no acota nada no se ofrece
      const order = FAMILY_ORDER[g] || [];
      const rank = (k: string) => (order.indexOf(k) >= 0 ? order.indexOf(k) : order.length);
      groups.push({ key: g, label, values: Object.entries(cnt).sort(([a], [b]) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0)).map(([name, count]) => ({ name, count })) });
    }
  }

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
    groups,
  };

  // ── Ordenamiento ────────────────────────────────────────────────────────────
  const sorted = [...rows];
  const recomendados = snap.rank === "recomendados";
  const pins = snap.pins || {};
  const pinned = (p: SnapProduct) => {
    const cats = catObj ? [catObj.id] : p.category_id != null ? [p.category_id] : [];
    return cats.some((c) => (pins[String(c)] || []).includes(p.id));
  };
  if (sort === "relevance" && recomendados) {
    const buy = (p: SnapProduct) => (stateOf(p) === "en_bodega" || stateOf(p) === "bajo_pedido" ? 0 : 1);
    sorted.sort((a, b) => (scores.get(b.id) ?? 0) - (scores.get(a.id) ?? 0) || buy(a) - buy(b) || Number(pinned(b)) - Number(pinned(a))
      || (b.q ?? 0) - (a.q ?? 0) || Number(a.display_price <= 0) - Number(b.display_price <= 0)
      || (a.display_price > 0 ? a.display_price : 0) - (b.display_price > 0 ? b.display_price : 0) || cmpName(a, b));
  } else if (sort === "name" && recomendados) {
    sorted.sort(cmpName);
  } else if (sort === "relevance") {
    sorted.sort((a, b) => {
      const sd = (scores.get(b.id) ?? 0) - (scores.get(a.id) ?? 0);
      if (sd !== 0) return sd;
      const od = availOrd(stateOf(a)) - availOrd(stateOf(b));
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
      description: catObj.description ?? null,
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
    label_short: p.availability.label_short ?? null,
    accion: p.availability.accion ?? null,
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
    display_name: p.display_name ?? null,
    public_code: p.public_code ?? null,
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
    compare_display_price: p.compare_display_price ?? null,
    price_on_request: p.display_price <= 0,
    availability: avail,
    variants: [],
    price: p.display_price,
    currency,
    ks: p.ks ?? null,
    created_ts: p.created_ts || null,
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
