/* O2-01a: la tienda pinta sin dos llamadas en serie. En produccion nginx incrusta en el HTML (SSI) un
   <script type="application/json" id="sh-boot"> con el slug, los datos base de la tienda y la portada (api:
   /public/seo/boot). Aqui no hay nginx: la prueba arma ese mismo bloque con las respuestas del API simulado y lo pega en
   el HTML de la tienda de pruebas antes de cargarla. */
import type { Browser, Page } from "@playwright/test";
import type { AjustesE2e } from "./app/api";
import { enCalma, expect, test, tiendaLista } from "./soporte/tienda";

type Ventana = Window & { __e2eAjustes?: AjustesE2e; __titulares?: string[]; __lcp?: number };
const fijar = (page: Page, a: AjustesE2e) => page.addInitScript((x: AjustesE2e) => { (window as Ventana).__e2eAjustes = x; }, a);

/** El bloque de arranque tal como lo arma el API (store_seo.boot_data + boot_html), con los datos del API simulado. */
async function armarArranque(page: Page): Promise<string> {
  await page.goto("/");
  await tiendaLista(page);
  return page.evaluate(async () => {
    const pedir = (p: string) => fetch(`/api/public/store/crimson${p}`).then((r) => r.json());
    const home = await pedir("");
    const { seo: _seo, ...showcase } = await pedir("/showcase"); // el API no lo manda en el arranque
    const legal = home.store.legal || {};
    home.store = { ...home.store, legal: { privacy: "", terms: "" }, legal_on: { privacy: !!legal.privacy, terms: !!legal.terms } };
    // el mismo escape del API (services/store_seo.ld_json)
    const json = JSON.stringify({ v: 1, slug: "crimson", path: "/", store: home, showcase })
      .replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026").replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
    return `<script type="application/json" id="sh-boot">${json}</script>`;
  });
}

/** Sirve el HTML de la tienda con el bloque de arranque dentro del <body>, como lo deja el SSI de nginx. */
async function conArranque(page: Page, bloque: string) {
  await page.route((u) => u.pathname === "/", async (route) => {
    const r = await route.fetch();
    const html = (await r.text()).replace('<div id="root"></div>', `<div id="root"></div>\n    ${bloque}`);
    await route.fulfill({ response: r, body: html });
  });
}

test("[O2-01a] con /store-domain y /store caídos, la portada igual pinta cabecera y hero; el titular no cambia", async ({ page }, info) => {
  test.skip(!["w375", "w1280"].includes(info.project.name), "no depende del ancho: celular y escritorio");
  const bloque = await armarArranque(page);
  await conArranque(page, bloque);
  // /showcase tarda (y llega despues): lo que se ve antes es lo del arranque
  await fijar(page, { fallarDominio: 1000, fallarTienda: true, portadaTras: 1500 });
  // cada texto que tuvo el titular desde que aparecio
  await page.addInitScript(() => {
    const w = window as Ventana;
    w.__titulares = [];
    new MutationObserver(() => {
      const t = document.getElementById("vt-title")?.textContent ?? null;
      if (t !== null && w.__titulares!.at(-1) !== t) w.__titulares!.push(t);
    }).observe(document, { subtree: true, childList: true, characterData: true });
  });
  await page.reload();
  await expect(page.locator(".shop .sh-head")).toBeVisible();
  await expect(page.locator(".vt-hero #vt-title")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await tiendaLista(page);
  await enCalma(page);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".vt-kits .vt-kit:not(.vt-kit--skel)").first()).toBeVisible();
  const titulares = await page.evaluate(() => (window as Ventana).__titulares);
  expect(titulares, "el titular se pinto una sola vez, con su texto final").toHaveLength(1);
  // sin scroll horizontal
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});

test("[O2-01a] sin el bloque de arranque (o dañado) la tienda carga como antes", async ({ page }, info) => {
  test.skip(info.project.name !== "w375", "un ancho basta");
  await conArranque(page, '<script type="application/json" id="sh-boot">{roto</script>');
  await page.goto("/");
  await tiendaLista(page);
  await expect(page.locator(".vt-card").first()).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

/* LCP de laboratorio, antes (sin arranque) y despues (con arranque): celular, red 3G lenta (400 ms, 400 Kbps) y CPU x4,
   los valores de Lighthouse movil. El API simulado responde dentro del navegador (no pasa por la red emulada): cada
   llamada espera 400 ms, el viaje de ida y vuelta de esa red. Queda en las anotaciones del informe; no falla por el
   numero (la tienda de pruebas no es produccion), solo registra. */
test("[O2-01a] LCP de laboratorio con y sin arranque (3G lenta, CPU x4)", async ({ page, browser }, info) => {
  test.skip(info.project.name !== "w375" || !!process.env.CI, "medicion local, un ancho");
  test.setTimeout(180_000);
  const bloque = await armarArranque(page);
  const medir = async (b: Browser, con: boolean) => {
    const ctx = await b.newContext({ viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true, locale: "es-CR" });
    await ctx.route((u) => !/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//.test(u.href) && /^https?:/.test(u.href), (r) => r.abort());
    const p = await ctx.newPage();
    await fijar(p, { latenciaApi: 400 });
    if (con) await conArranque(p, bloque);
    await p.addInitScript(() => {
      new PerformanceObserver((l) => { for (const e of l.getEntries()) (window as Ventana).__lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
    });
    const cdp = await ctx.newCDPSession(p);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 400, downloadThroughput: (400 * 1024) / 8, uploadThroughput: (400 * 1024) / 8 });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await p.goto("/", { timeout: 120_000 });
    await p.locator(".vt-hero #vt-title").waitFor({ timeout: 120_000 });
    await p.waitForTimeout(1500);
    const lcp = await p.evaluate(() => (window as Ventana).__lcp ?? -1);
    await ctx.close();
    return Math.round(lcp);
  };
  info.annotations.push({ type: "bloque de arranque (bytes)", description: String(bloque.length) });
  const sin = await medir(browser, false);
  const con = await medir(browser, true);
  info.annotations.push({ type: "LCP sin arranque (ms)", description: String(sin) }, { type: "LCP con arranque (ms)", description: String(con) });
  console.log(`[O2-01a] LCP 3G lenta + CPU x4: sin arranque ${sin} ms · con arranque ${con} ms · bloque ${bloque.length} bytes`);
  expect(sin).toBeGreaterThan(0);
  expect(con).toBeGreaterThan(0);
});
