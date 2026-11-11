/* O3-04: filtros propios de cada familia con la MISMA regla del API (services/family_filters.py y /catalog).
   familias_casos.json: los valores «fam» y «fa» los calcula el API (la prueba api/tests/test_tienda_filtros_familia.py
   comprueba que el snapshot los manda iguales); los grupos y los productos esperados de cada caso son los mismos que
   la prueba del API exige a /catalog. Aqui se comprueba que el navegador da lo mismo. */
import { describe, expect, it } from "vitest";
import { filterCatalog, parseFamilyFilters, type SnapProduct, type Snapshot } from "../catalogFilter";
import casos from "./familias_casos.json";

type Caso = { f: string[]; ids: number[]; groups: { key: string; label: string; values: { name: string; count: number }[] }[] };

function snap(): Snapshot {
  const products: SnapProduct[] = casos.productos.map((p) => ({
    id: p.id, path: `/p/x-${p.id}`, name: p.name, brand: null, model: null, code: `C-${p.id}`, category_id: 1, display_price: p.price,
    web_featured: false, image: null, type: p.type, created_ts: 0, fam: p.fam ?? undefined, fa: p.fa as Record<string, string>,
    availability: { state: "en_bodega", label: "", accion: null, max_qty: null, plazo: null }, sf: p.name.toLowerCase(),
  }));
  return {
    v: 2, version: "1", built_at: "", currency: "CRC", n: products.length, products,
    categories: [{ id: 1, name: casos.categoria, slug: "parlantes-de-prueba", parent_id: null, count: products.length }],
    brands: [], types: [], showcase: { headline: null, subline: null, featured: [], banners: [], trust: [] },
  } as unknown as Snapshot;
}

describe("filtros propios de la familia (paridad con el API)", () => {
  it("mismos grupos, valores, conteos y productos", () => {
    for (const c of casos.casos as Caso[]) {
      const r = filterCatalog(snap(), { category: "parlantes-de-prueba", f: c.f, limit: 99 });
      expect(r.items.map((p) => p.id).sort((a, b) => a - b), c.f.join("|")).toEqual(c.ids);
      expect(r.facets.groups, c.f.join("|")).toEqual(c.groups);
    }
  });
  it("sin categoria no hay grupos de familia y el ultimo valor de un grupo gana", () => {
    expect(filterCatalog(snap(), { limit: 99 }).facets.groups).toEqual([]);
    expect(parseFamilyFilters(["potencia:A", "potencia:B", "malo", "x:"])).toEqual({ potencia: "B" });
  });
});
