/* O2-14: lo que la automatizacion si puede revisar antes de cerrar la ola.
   1. Texto al 200 % (la tienda usa rem: se agranda la letra base) sin scroll de lado ni contenido cortado.
   2. Celular lento (3G lenta y CPU x4) en portada, categoria y ficha: LCP, CLS y tareas largas. Es un INFORME: solo
      corre con E2E_LENTO=1 y deja los numeros como anotaciones (la tienda de pruebas sirve todo desde el navegador,
      sin red real: los numeros de produccion se miden aparte con Lighthouse; ver e2e/ACCESIBILIDAD.md). */
import { CARRITO, CATEGORIA, rutaFicha } from "./soporte/pantallas";
import { expect, medirAncho, sembrarCarrito, test, tiendaLista } from "./soporte/tienda";

const PANTALLAS = [
  { nombre: "portada", ruta: "/" },
  { nombre: "categoría", ruta: CATEGORIA.ruta },
  { nombre: "ficha", ruta: rutaFicha(70) },
];

for (const p of [...PANTALLAS, { nombre: "carrito", ruta: `${CATEGORIA.ruta}#carrito` }]) {
  test(`[O2-14] texto al 200 %: ${p.nombre} sin scroll de lado ni contenido cortado`, async ({ page }, info) => {
    test.skip(!["w320", "w375"].includes(info.project.name), "celular");
    await page.addInitScript(() => document.addEventListener("DOMContentLoaded", () => { document.documentElement.style.fontSize = "200%"; }));
    if (p.nombre === "carrito") await sembrarCarrito(page, CARRITO);
    await page.goto(p.ruta.replace("#carrito", ""));
    await tiendaLista(page);
    if (p.nombre === "carrito") {
      await page.getByRole("button", { name: /^Carrito:/ }).click();
      await expect(page.locator(".sh-drawer")).toHaveAttribute("data-open", "true");
      await page.waitForFunction(() => document.querySelector(".sh-drawer")!.getAnimations().every((a) => a.playState !== "running"));
    }
    const m = await medirAncho(page);
    expect(m.documento, "scroll de lado").toBeLessThanOrEqual(m.ventana);
    expect(m.fuera, "contenido cortado por el borde").toEqual([]);
  });
}

for (const p of PANTALLAS) {
  test(`[O2-14] celular lento (informe): ${p.nombre}`, async ({ page }, info) => {
    test.skip(info.project.name !== "w375" || process.env.E2E_LENTO !== "1", "solo con E2E_LENTO=1, a 375 px");
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 400, downloadThroughput: (400 * 1024) / 8, uploadThroughput: (400 * 1024) / 8 });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await page.addInitScript(() => {
      const w = window as Window & { __perf?: { lcp: number; cls: number; largas: number[] } };
      w.__perf = { lcp: 0, cls: 0, largas: [] };
      new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__perf!.lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) if (!e.hadRecentInput) w.__perf!.cls += e.value; }).observe({ type: "layout-shift", buffered: true });
      new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__perf!.largas.push(Math.round(e.duration)); }).observe({ type: "longtask", buffered: true });
    });
    await page.goto(p.ruta);
    await tiendaLista(page);
    await page.waitForTimeout(3000);
    const r = await page.evaluate(() => (window as Window & { __perf?: { lcp: number; cls: number; largas: number[] } }).__perf!);
    const txt = `LCP ${Math.round(r.lcp)} ms · CLS ${r.cls.toFixed(3)} · tareas largas ${r.largas.length} (máx ${Math.max(0, ...r.largas)} ms)`;
    info.annotations.push({ type: "lento", description: `${p.nombre}: ${txt}` });
    console.log(`[O2-14] ${p.nombre}: ${txt}`);
  });
}
