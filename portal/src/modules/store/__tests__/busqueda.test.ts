/* O1-14: buscador tolerante con la MISMA regla del API (services/store_search.py).
   1) Paridad: los casos de busqueda_casos.json los arma el API; aqui se comprueba que el navegador da lo mismo.
   2) Sobre el catalogo real de la demo (src/demo/snapshot.json): plural, guiones, codigos y sinonimos. */
import { describe, expect, it } from "vitest";
import real from "../../../demo/snapshot.json";
import { productType, type Snapshot as Demo } from "../../../demo/catalog";
import { filterCatalog, searchField, searchScore, type SnapProduct, type Snapshot } from "../catalogFilter";
import casos from "./busqueda_casos.json";

type Caso = { q: string; ids: number[]; scores: Record<string, number> };
const syn = casos.syn as Record<string, string[]>;

describe("paridad con el API (30 consultas)", () => {
  it("el campo de búsqueda se arma igual", () => {
    for (const p of casos.products) expect(searchField(p.name, p.brand, p.model, p.code, p.sku, p.category, p.type)).toBe(p.sf);
  });
  it("mismos resultados, en el mismo orden y con los mismos puntos", () => {
    expect(casos.cases.length).toBeGreaterThanOrEqual(30);
    for (const c of casos.cases as unknown as Caso[]) {
      const sc = casos.products.map((p) => [p.id, searchScore(c.q, p, p.category, syn)] as const).filter(([, s]) => s > 0);
      const got = [...sc].sort((a, b) => b[1] - a[1] || a[0] - b[0]).map(([id]) => id);
      expect(got, c.q).toEqual(c.ids);
      expect(Object.fromEntries(sc.map(([id, s]) => [String(id), s])), c.q).toEqual(c.scores);
    }
  });
});

/** el catalogo real de la demo con la forma del snapshot nuevo (sf con codigo, categoria y tipo; tabla syn) */
function snapReal(): Snapshot {
  const d = real as unknown as Demo;
  const cat = new Map(d.categories.map((c) => [c.id, c.name]));
  const products: SnapProduct[] = d.products.map((p) => ({
    id: p.id, path: `/p/x-${p.id}`, name: p.name, brand: p.brand, model: null, code: p.code, category_id: p.cat, display_price: p.crc,
    web_featured: false, image: null, type: productType(p) || null, created_ts: 0,
    availability: { state: "en_bodega", label: "", accion: null, max_qty: null, plazo: null },
    sf: searchField(p.name, p.brand, p.code, p.cat != null ? cat.get(p.cat) : null, productType(p)),
  }));
  return {
    v: 2, version: "1", built_at: "", currency: "CRC", n: products.length, products, syn,
    categories: d.categories.map((c) => ({ id: c.id, name: c.name, slug: String(c.id), parent_id: c.parent, count: 0 })),
    brands: [], types: [], showcase: { headline: null, subline: null, featured: [], banners: [], trust: [] },
  };
}

describe("sobre el catálogo real (O1-14)", () => {
  const s = snapReal();
  const total = (q: string) => filterCatalog(s, { q, limit: 999 }).total;
  const nombres = (q: string) => filterCatalog(s, { q, limit: 999 }).items.map((p) => p.name);
  const codigos = (q: string) => filterCatalog(s, { q, limit: 999 }).items.map((p) => p.code);

  it("«camaras» y «camara» difieren menos de 10 %", () => {
    const a = total("camaras"), b = total("camara");
    expect(b).toBeGreaterThan(0);
    expect(Math.abs(a - b) / Math.max(a, b)).toBeLessThan(0.1);
  });
  it("«tp-link», «tp link» y «tplink» dan lo mismo", () => {
    expect(nombres("tp link")).toEqual(nombres("tp-link"));
    expect(nombres("tplink")).toEqual(nombres("tp-link"));
  });
  it("el código que imprime la ficha y el modelo sin guiones encuentran su producto, primero", () => {
    expect(codigos("SG5071")[0]).toBe("SG5071");
    expect(codigos("sg-5071")[0]).toBe("SG5071");
    expect(nombres("ds2cd1047g3")[0]).toMatch(/DS-2CD1047G3/i);
  });
  it("«bocina» devuelve parlantes (el catálogo de la demo no trae parlantes: se mira en los casos de paridad)", () => {
    const bocina = casos.cases.find((c) => c.q === "bocina")!.ids;
    const parlantes = casos.products.filter((p) => p.type === "Parlante").map((p) => p.id);
    expect([...bocina].sort()).toEqual([...parlantes].sort());
  });
  it("un snapshot viejo (sf sin código, sin tabla de sinónimos) no rompe", () => {
    const viejo: Snapshot = { ...s, v: 1, syn: undefined, products: s.products.map((p) => ({ ...p, sf: `${p.name} ${p.brand ?? ""}`.toLowerCase() })) };
    expect(filterCatalog(viejo, { q: "camaras" }).total).toBeGreaterThan(0);
  });
});
