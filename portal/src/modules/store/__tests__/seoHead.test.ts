/* O1-06: en la ruta con la que se abrio la pagina, si el servidor (nginx SSI) ya puso sus metas, el navegador no las
   toca; desde la primera navegacion interna manda el navegador (con el bloque seo del API). */
import { afterEach, describe, expect, it } from "vitest";
import { _setSsrPath, serverOwnsHead } from "../storeKit";

afterEach(() => _setSsrPath(null));

describe("useHead y las metas del servidor", () => {
  it("sin metas del servidor (panel, vite dev) el navegador siempre manda", () => {
    _setSsrPath(null);
    expect(serverOwnsHead("/")).toBe(false);
    expect(serverOwnsHead("/p/camara-1")).toBe(false);
  });

  it("en la ruta de la carga inicial no se toca nada, aunque el componente se pinte varias veces", () => {
    _setSsrPath("/p/camara-68");
    expect(serverOwnsHead("/p/camara-68")).toBe(true);
    expect(serverOwnsHead("/p/camara-68")).toBe(true); // snapshot primero, /item despues: sigue siendo del servidor
  });

  it("al navegar a otra ruta manda el navegador, y ya no vuelve a ceder aunque se regrese", () => {
    _setSsrPath("/");
    expect(serverOwnsHead("/c/camaras")).toBe(false);
    expect(serverOwnsHead("/")).toBe(false);
  });
});
