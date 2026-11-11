/* O2-01b: contenido real en el HTML (cuerpo por SSI, apagado por defecto). En produccion nginx incrusta, antes de
   #root, la hoja de la tienda, la regla que oculta #root y el contenido del servidor en #sh-ssr (api: /public/seo/body).
   Aqui no hay nginx: la prueba pega ese mismo bloque en el HTML de la tienda de pruebas y mira el relevo
   (src/modules/store/ssrBody.ts): sin salto (CLS), sin perder la posicion de lectura, y sin JavaScript se lee igual. */
import { readdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Browser, Page } from "@playwright/test";
import type { AjustesE2e } from "./app/api";
import { expect, test, tiendaLista } from "./soporte/tienda";

type Ventana = Window & { __e2eAjustes?: AjustesE2e; __cls?: number };
const fijar = (page: Page, a: AjustesE2e) => page.addInitScript((x: AjustesE2e) => { (window as Ventana).__e2eAjustes = x; }, a);

/** la hoja de la vitrina del build de pruebas (la que el plugin cssDeLaTienda enlaza en el build real) */
const hoja = () => readdirSync(resolve(process.cwd(), "e2e/.dist/assets")).find((f) => /^PublicStore-.*\.css$/.test(f));

/** El bloque tal como queda tras el SSI de index.html, con un contenido como el de services/store_ssr.py. */
function bloque(css: string, filas = 40): string {
  const prods = Array.from({ length: filas }, (_, i) => `<li data-sh-id="${i + 1}"><a href="/p/producto-de-prueba-${i + 1}">Producto de prueba ${i + 1}</a> · <span class="sh-ssr__precio">₡1&nbsp;000</span> · <span class="sh-ssr__disp">En bodega</span></li>`).join("");
  const cuerpo = '<div class="shop sh-ssr"><header class="sh-ssr__head"><div class="sh-wrap"><a href="/"><b>Crimson</b></a> <nav aria-label="Principal"><a href="/catalogo">Catálogo</a></nav></div></header>'
    + `<main class="sh-wrap"><h1>Contenido del servidor</h1><p>Texto de prueba.</p><ul>${prods}</ul><p class="sh-ssr__contacto"><a href="https://wa.me/50600000000">Escribir por WhatsApp</a></p></main></div>`;
  return `<link rel="stylesheet" crossorigin href="/assets/${css}"><style>#sh-ssr:not(:empty)~#root{position:absolute;top:0;left:0;right:0;visibility:hidden}</style><div id="sh-ssr">${cuerpo}</div>`;
}

async function conCuerpo(page: Page, html: string) {
  await page.route((u) => u.pathname === "/", async (route) => {
    const r = await route.fetch();
    const doc = (await r.text()).replace('<div id="root"></div>', `${html}\n    <div id="root"></div>`);
    await route.fulfill({ response: r, body: doc });
  });
}

/* CLS de la portada con y sin el contenido del servidor, cada una en un contexto nuevo (sin cache). El relevo no puede
   agregar salto: con cuerpo, a lo sumo lo mismo que sin el (+0,01). Ademas, en celular el total queda bajo 0,05.
   Los dos numeros quedan en las anotaciones del informe. */
async function clsDeLaPortada(browser: Browser, ancho: { width: number; height: number }, cuerpo: string | null) {
  const ctx = await browser.newContext({ viewport: ancho, isMobile: ancho.width < 1024, hasTouch: ancho.width < 1024, locale: "es-CR" });
  await ctx.route((u) => !/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//.test(u.href) && /^https?:/.test(u.href), (r) => r.abort());
  const page = await ctx.newPage();
  if (cuerpo) await conCuerpo(page, cuerpo);
  await page.addInitScript(() => {
    (window as Ventana).__cls = 0;
    new PerformanceObserver((l) => {
      for (const e of l.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) if (!e.hadRecentInput) (window as Ventana).__cls! += e.value;
    }).observe({ type: "layout-shift", buffered: true });
  });
  await page.goto("/");
  await tiendaLista(page);
  await page.waitForTimeout(500);
  await expect(page.locator("#sh-ssr")).toHaveCount(0);
  await expect(page.locator(".vt-hero #vt-title")).toBeVisible();
  const cls = await page.evaluate(() => (window as Ventana).__cls ?? 0);
  const lado = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  await ctx.close();
  return { cls, lado };
}

test("[O2-01b] el relevo no agrega salto (CLS) ni scroll de lado", async ({ browser }, info) => {
  test.skip(!["w320", "w375", "w768"].includes(info.project.name), "celular y tableta");
  const css = hoja();
  expect(css, "hoja de la vitrina en e2e/.dist/assets").toBeTruthy();
  const ancho = info.project.use.viewport!;
  const sin = await clsDeLaPortada(browser, ancho, null);
  const con = await clsDeLaPortada(browser, ancho, bloque(css!));
  info.annotations.push({ type: "CLS portada sin cuerpo", description: sin.cls.toFixed(4) }, { type: "CLS portada con cuerpo", description: con.cls.toFixed(4) });
  console.log(`[O2-01b] ${info.project.name}: CLS sin cuerpo ${sin.cls.toFixed(4)} · con cuerpo ${con.cls.toFixed(4)}`);
  expect(con.cls).toBeLessThanOrEqual(sin.cls + 0.01);
  if (ancho.width < 768) expect(con.cls).toBeLessThan(0.05);
  expect(con.lado).toBeLessThanOrEqual(0);
});

test("[O2-01b] antes del relevo se lee el contenido del servidor y la posición de lectura se conserva", async ({ page }, info) => {
  test.skip(info.project.name !== "w375", "un ancho basta");
  await conCuerpo(page, bloque(hoja()!, 80));
  await fijar(page, { latenciaApi: 2500 }); // la vitrina tarda: da tiempo a leer y bajar
  await page.goto("/");
  await expect(page.locator("#sh-ssr h1")).toHaveText("Contenido del servidor");
  await expect(page.locator("#root")).toBeHidden();
  await page.evaluate(() => window.scrollTo(0, 400));
  await expect(page.locator("#sh-ssr")).toHaveCount(0, { timeout: 15_000 });
  await tiendaLista(page);
  const y = await page.evaluate(() => window.scrollY);
  expect(Math.abs(y - 400)).toBeLessThanOrEqual(2);
});

test("[O2-01b] sin JavaScript el contenido del servidor se ve con H1, precio y enlaces reales", async ({ browser }, info) => {
  test.skip(info.project.name !== "w375", "un ancho basta");
  const ctx = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 375, height: 667 } });
  await ctx.route((u) => !/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//.test(u.href) && /^https?:/.test(u.href), (r) => r.abort());
  const page = await ctx.newPage();
  await conCuerpo(page, bloque(hoja()!));
  await page.goto("/");
  await expect(page.locator("#sh-ssr h1")).toBeVisible();
  await expect(page.locator("#sh-ssr .sh-ssr__precio").first()).toHaveText("₡1 000");
  await expect(page.locator('#sh-ssr a[href="/p/producto-de-prueba-1"]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  await ctx.close();
});
