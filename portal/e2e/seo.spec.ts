/* SEO de la vitrina en el navegador (O1-06): un solo JSON-LD, el del servidor.
   En produccion nginx incrusta por SSI el <head> de la ruta pedida (etiquetas data-sh: titulo, descripcion, robots,
   canonica, Open Graph y JSON-LD). La tienda de pruebas no tiene nginx: aqui se simula esa respuesta reescribiendo el
   HTML de la primera carga, y se comprueba que la vitrina no lo toca en esa ruta y que, al navegar, pone el bloque
   seo del API (o nada de JSON-LD si no viene). Solo a 1280 px: no depende del ancho. */
import { REF } from "./app/datos";
import { CATEGORIA, rutaFicha } from "./soporte/pantallas";
import { expect, test, tiendaLista } from "./soporte/tienda";
import type { Page } from "@playwright/test";

test.beforeEach(({}, info) => test.skip(info.project.use.viewport!.width !== 1280, "no depende del ancho"));

const LD = JSON.stringify({ "@context": "https://schema.org", "@graph": [{ "@type": "Organization", name: "Hecho por el servidor" }] });

/** la primera carga llega con el <head> que pondria nginx (SSI) para esa ruta */
async function conSsi(page: Page) {
  await page.route((u) => !u.pathname.includes(".") && !u.pathname.startsWith("/api/"), async (route) => {
    if (route.request().resourceType() !== "document") return route.fallback();
    const res = await route.fetch();
    const html = (await res.text()).replace(/<meta name="robots"[^>]*>/, "").replace(/<title>[^<]*<\/title>/, [
      "<title>Titulo del servidor</title>",
      '<meta data-sh name="description" content="Descripcion del servidor">',
      '<meta data-sh name="robots" content="noindex,follow">',
      '<link data-sh rel="canonical" href="https://tienda.example.com/canonica">',
      `<script data-sh type="application/ld+json" id="sh-ld">${LD}</script>`,
    ].join(""));
    await route.fulfill({ response: res, body: html, headers: { ...res.headers(), "content-type": "text/html; charset=utf-8" } });
  });
}

async function head(page: Page) {
  return page.evaluate(() => ({
    title: document.title,
    description: document.querySelector<HTMLMetaElement>('meta[name="description"]')?.content ?? null,
    robots: document.querySelector<HTMLMetaElement>('meta[name="robots"]')?.content ?? null,
    canonical: document.querySelector<HTMLLinkElement>('link[rel="canonical"]')?.href ?? null,
    ld: [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => s.textContent),
  }));
}

for (const [nombre, ruta] of [["portada", "/"], ["categoría", CATEGORIA.ruta], ["ficha", rutaFicha(REF.comprable)], ["preguntas frecuentes", "/info/preguntas-frecuentes"]] as const) {
  test(`[O1-06] ${nombre}: en la primera carga el <head> y el JSON-LD son los del servidor`, async ({ page }) => {
    await conSsi(page);
    await page.goto(ruta);
    await tiendaLista(page);
    expect(await head(page)).toEqual({
      title: "Titulo del servidor", description: "Descripcion del servidor", robots: "noindex,follow",
      canonical: "https://tienda.example.com/canonica", ld: [LD],
    });
  });
}

test("[O1-06] al navegar, la ficha usa el bloque seo del API y la categoría no arma un JSON-LD propio", async ({ page }) => {
  await conSsi(page);
  await page.goto("/");
  await tiendaLista(page);
  await page.goto(rutaFicha(REF.comprable)); // carga nueva: otra vez la del servidor
  await tiendaLista(page);
  expect((await head(page)).ld).toEqual([LD]);
  // navegacion interna a otra ficha: manda el bloque seo de /item (un solo JSON-LD, ningun «index,follow» forzado)
  await page.locator(".vt-card__name").first().click();
  await tiendaLista(page);
  const ficha = await head(page);
  expect(ficha.ld).toHaveLength(1);
  expect(JSON.parse(ficha.ld[0]!)["@graph"][0]["@type"]).toBe("Product");
  expect(ficha.title).toBe(`${await page.getByRole("heading", { level: 1 }).textContent()} · Crimson`);
  expect(ficha.robots).toBe("index,follow");
  // a una categoria (el snapshot no trae bloque seo): sin JSON-LD inventado por el navegador y sin robots forzado
  await page.locator(".vt-crumbs a").last().click();
  await tiendaLista(page);
  const cat = await head(page);
  expect(cat.ld).toEqual([]);
  expect(cat.robots).toBeNull();
});
