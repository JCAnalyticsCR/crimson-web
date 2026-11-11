/* O1-13: snapshot al dia en el navegador. Un snapshot viejo (sin los campos nuevos) no rompe; el tachado solo sale si
   viaja; el estado en vivo manda sobre el del snapshot para «Entrega inmediata»; un snapshot guardado con formato
   menor no se usa. Datos 100 % ficticios. */
import { describe, expect, it } from "vitest";
import { filterCatalog, snapToVProd, type SnapProduct, type Snapshot } from "../catalogFilter";
import { SNAP_FORMAT, snapUsable } from "../useSnapshot";

const P = (id: number, state: string, extra: Partial<SnapProduct> = {}): SnapProduct => ({
  id, path: `/p/equipo-${id}`, name: `Equipo ${id}`, brand: null, model: null, code: `C-${id}`, category_id: null, display_price: 1000,
  web_featured: false, image: null, type: null, created_ts: id, availability: { state, label: state, accion: null, max_qty: null, plazo: null },
  sf: `equipo ${id}`, ...extra,
});

const snap = (products: SnapProduct[], v = 2): Snapshot => ({
  v, version: "1", built_at: "2026-11-10T00:00:00+00:00", currency: "CRC", n: products.length, products, categories: [], brands: [], types: [],
  showcase: { headline: null, subline: null, featured: [], banners: [], trust: [] },
});

describe("snapshot al día (O1-13)", () => {
  it("un snapshot sin los campos nuevos (formato 1) filtra y convierte igual", () => {
    const viejo = snap([P(1, "en_bodega"), P(2, "consultar")], 1);
    const r = filterCatalog(viejo, { q: "equipo" });
    expect(r.total).toBe(2);
    expect(snapToVProd(r.items[0], "CRC", []).compare_display_price).toBeNull();
  });

  it("el tachado solo aparece cuando el API lo manda (con historial propio)", () => {
    expect(snapToVProd(P(1, "en_bodega", { compare_display_price: 1200 }), "CRC", []).compare_display_price).toBe(1200);
    expect(snapToVProd(P(2, "en_bodega"), "CRC", []).compare_display_price).toBeNull();
  });

  it("el estado en vivo manda sobre el del snapshot en «Entrega inmediata» y en el orden", () => {
    const s = snap([P(1, "consultar"), P(2, "en_bodega"), P(3, "en_bodega")]);
    expect(filterCatalog(s, { avail: "inmediata" }).items.map((p) => p.id)).toEqual([2, 3]);
    // la pagina ya pidio /availability: el 1 si esta en bodega y el 3 ya no
    const live = { 1: "disponible", 3: "agotado" };
    expect(filterCatalog(s, { avail: "inmediata", live }).items.map((p) => p.id)).toEqual([1, 2]);
    expect(filterCatalog(s, { live }).facets.availability.disponible).toBe(2);
    expect(filterCatalog(s, { live }).items.map((p) => p.id).slice(-1)).toEqual([3]);
  });

  it("un snapshot guardado con un formato menor no se usa", () => {
    expect(SNAP_FORMAT).toBe(2);
    expect(snapUsable({ v: 1 })).toBe(false);
    expect(snapUsable({})).toBe(false);
    expect(snapUsable(null)).toBe(false);
    expect(snapUsable({ v: 2 })).toBe(true);
  });
});
