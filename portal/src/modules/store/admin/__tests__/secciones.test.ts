/* O4-01: Mi Tienda por secciones. Cada seccion tiene su direccion (recargar no devuelve a «Portada») y abre solo
   con su permiso; la Tienda de pruebas, solo el administrador. Datos ficticios. */
import { describe, expect, it } from "vitest";
import { canAny, type Perms } from "../../../../app/roles";
import { SECTIONS, sectionFor, sectionUrl, visibleSections } from "../sections";

const allowsFor = (perms: Perms) => (need?: string) => canAny(perms, need);

describe("secciones de Mi Tienda", () => {
  it("cada direccion devuelve su propia seccion", () => {
    for (const s of SECTIONS) expect(sectionFor(sectionUrl(s))?.key).toBe(s.key);
    expect(sectionFor("/mi-tienda/catalogo/123")?.key).toBe("catalogo");
    expect(sectionFor("/mi-tienda/")?.key).toBe("resumen");
    expect(sectionFor("/mi-tienda/no-existe")).toBeUndefined();
    expect(sectionFor("/productos")).toBeUndefined();
  });

  it("las direcciones son unicas", () => {
    const paths = SECTIONS.map(sectionUrl);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it("el administrador ve todas, incluida la Tienda de pruebas", () => {
    expect(visibleSections(allowsFor({ "*": ["*"] }), true).map((s) => s.key)).toEqual(SECTIONS.map((s) => s.key));
  });

  it("quien solo edita el catalogo ve resumen y catalogo; nunca pagos, apariencia, categorias ni publicacion", () => {
    const keys = visibleSections(allowsFor({ catalog: ["ver", "editar"] }), false).map((s) => s.key);
    expect(keys).toEqual(["resumen", "catalogo"]);
  });

  it("un rol con settings.configurar que no es administrador no ve la Tienda de pruebas", () => {
    const keys = visibleSections(allowsFor({ settings: ["ver", "configurar"] }), false).map((s) => s.key);
    expect(keys).not.toContain("pruebas");
    expect(keys).toContain("publicacion");
    expect(keys).toContain("historial");
  });

  it("sin permisos de tienda no ve nada", () => {
    expect(visibleSections(allowsFor({ sales: ["ver"] }), false)).toEqual([]);
  });

  it("deja el lugar de «Contenido por aprobar» (Ola 3) en el grupo Catálogo", () => {
    const s = SECTIONS.find((x) => x.key === "contenido");
    expect(s?.group).toBe("Catálogo");
    expect(sectionUrl(s!)).toBe("/mi-tienda/contenido-por-aprobar");
  });
});
