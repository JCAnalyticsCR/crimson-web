/* O3-08: el orden «Recomendados» y «Nombre» del navegador son los del API (services/store_ranking.py). orden_casos.json
   lo arma el API (tests/test_tienda_orden_recomendados.py exige el mismo resultado alla). Sin el interruptor del
   dueno el snapshot no trae «rank» y el orden es el de siempre. */
import { describe, expect, it } from "vitest";
import { filterCatalog, type SnapProduct, type Snapshot } from "../catalogFilter";
import casos from "./orden_casos.json";

function snap(rank: boolean): Snapshot {
  const products: SnapProduct[] = casos.productos.map((p) => ({
    id: p.id, path: `/p/x-${p.id}`, name: p.name, brand: null, model: null, code: `C-${p.id}`, category_id: 1, display_price: p.price,
    web_featured: p.id === 7, image: null, type: null, created_ts: 0, q: p.q || undefined,
    availability: { state: p.state, label: "", accion: null, max_qty: null, plazo: null }, sf: p.name.toLowerCase(),
  }));
  const pins = { "1": casos.productos.filter((p) => p.pinned).map((p) => p.id) };
  return {
    v: 2, version: "1", built_at: "", currency: "CRC", n: products.length, products,
    categories: [{ id: 1, name: "Prueba", slug: "prueba", parent_id: null, count: products.length }],
    brands: [], types: [], showcase: { headline: null, subline: null, featured: [], banners: [], trust: [] },
    ...(rank ? { rank: "recomendados", pins } : {}),
  } as unknown as Snapshot;
}

describe("orden «Recomendados» (paridad con el API)", () => {
  it("comprable hoy, fijado, calidad de ficha, precio y nombre", () => {
    expect(filterCatalog(snap(true), { category: "prueba", limit: 99 }).items.map((p) => p.id)).toEqual(casos.recomendados);
  });
  it("«Nombre» de la A a la Z, sin nada arriba", () => {
    expect(filterCatalog(snap(true), { category: "prueba", sort: "name", limit: 99 }).items.map((p) => p.id)).toEqual(casos.nombre);
  });
  it("sin el interruptor, el destacado sigue arriba como siempre", () => {
    expect(filterCatalog(snap(false), { category: "prueba", sort: "name", limit: 99 }).items[0].id).toBe(7);
  });
});
