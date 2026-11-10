/* Base comun de las pruebas de pantalla: reloj fijo, cero pedidos a internet y ayudas para dejar la pagina quieta
   antes de medirla o fotografiarla. Todas las pruebas importan `test` y `expect` de aqui, no de @playwright/test. */
import { test as base, expect, type Page } from "@playwright/test";

/** La hora de todas las pruebas: las fechas de «Mi pedido» y el año del pie no cambian de una corrida a otra. */
export const AHORA = new Date("2026-11-10T15:00:00-06:00");

const SLUG = "crimson";
/* esta maquina: 127.0.0.1, localhost y sus subdominios (tienda.localhost es la tienda de la compra real, O0-03) */
const local = (url: string) => /^https?:\/\/(127\.0\.0\.1|([a-z0-9-]+\.)*localhost)(:\d+)?\//.test(url) || !/^https?:/.test(url);

/** Base de TODAS las pruebas de pantalla, con API simulado o real (soporte/compra.ts): nada sale de esta maquina. */
export const sinInternet = base.extend<{ externas: string[] }>({
  /* Nada sale de esta maquina: cualquier pedido a otro sitio (fotos de un proveedor, fuentes, medicion) se corta y la
     prueba falla. La tienda de pruebas no necesita nada de afuera; si algo lo pide, es un cambio que hay que mirar. */
  externas: [async ({ context }, use) => {
    const externas: string[] = [];
    await context.route((url) => !local(url.href), (route) => { externas.push(route.request().url()); return route.abort(); });
    await use(externas);
    expect.soft(externas, "la tienda de pruebas pidió algo fuera de esta máquina").toEqual([]);
  }, { auto: true }],
});

export const test = sinInternet.extend({
  page: async ({ page }, use) => {
    await page.clock.setFixedTime(AHORA); // fija Date.now(); los temporizadores siguen corriendo
    await use(page);
  },
});
export { expect };

// ---------- estado previo de la visita (se escribe antes de que cargue la pagina) ----------
export type Linea = { id: number; nombre: string; precio: number; cantidad: number; foto?: string | null; max?: number | null };

/** Carrito ya lleno, como lo deja PublicStore en localStorage. Se vuelve a escribir en cada carga de la pagina. */
export async function sembrarCarrito(page: Page, lineas: Linea[]) {
  const cart = lineas.map((l) => ({ key: `${l.id}:0`, id: l.id, variant_id: null, name: l.nombre, image: l.foto ?? null, price: l.precio, currency: "CRC", qty: l.cantidad, max: l.max ?? null }));
  await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [`crimson-shop:${SLUG}`, JSON.stringify(cart)]);
}

/** Datos de una compradora inventada: sirven para abrir el checkout en cualquier paso. */
export const COMPRADORA = {
  name: "Ana Prueba Ficticia", email: "ana.prueba@example.com", phone: "8888 0000", inv: "consumidor", id_number: "", company: "",
  dtype: "envio", province: "Heredia", canton: "Barva", district: "San Pedro", address: "Del parque 200 m norte, casa blanca de portón negro",
  rate: "Envío GAM", method: "sinpe", coupon: "", notes: "", terms: false, save: false, website: "",
};

/** Borrador del checkout (sessionStorage) en el paso 0 a 3, con los pasos anteriores ya llenos. */
export async function sembrarCheckout(page: Page, paso: 0 | 1 | 2 | 3) {
  const vacio = { ...COMPRADORA, name: "", email: "", phone: "", province: "", canton: "", district: "", address: "", rate: "", method: "" };
  const f = paso === 0 ? vacio : paso === 1 ? { ...vacio, name: COMPRADORA.name, email: COMPRADORA.email, phone: COMPRADORA.phone } : paso === 2 ? { ...COMPRADORA, method: "" } : COMPRADORA;
  await page.addInitScript(([k, v]) => sessionStorage.setItem(k, v), [`crimson-shop:checkout-borrador:${SLUG}`, JSON.stringify({ f, step: paso, coupon: null })]);
}

// ---------- pagina quieta ----------
const CARGANDO = ".spinner, .sh-skel, .vt-spin, .vt-kit--skel";
/** Sin llamadas al API en curso durante un rato (el API simulado las cuenta: e2e/app/api.ts). Es el «red en calma»
    de estas pruebas: la tienda encadena llamadas (snapshot, portada, disponibilidad en vivo) y hay que esperar a la ultima. */
export async function enCalma(page: Page) {
  await page.evaluate(async () => {
    const e2e = (window as Window & { __e2e?: { enCurso: () => number } }).__e2e;
    if (!e2e) return;
    for (let quieto = 0; quieto < 250;) { await new Promise((r) => setTimeout(r, 50)); quieto = e2e.enCurso() === 0 ? quieto + 50 : 0; }
  });
}

/** La tienda termino de cargar: hay cabecera, no quedan esqueletos ni ruedas de carga y las fuentes estan listas. */
export async function tiendaLista(page: Page) {
  await page.locator(".shop .sh-head").waitFor();
  await expect(page.locator(CARGANDO)).toHaveCount(0);
  await enCalma(page);
  await expect(page.locator(CARGANDO)).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
}

/** Deja la pagina completa y quieta: baja hasta el final y vuelve arriba (lo que aparece al desplazarse), y pide ya
    las fotos diferidas (loading="lazy"): las de un carrusel que quedan fuera de la ventana no cargarian nunca solas. */
export async function cargarTodo(page: Page) {
  await page.evaluate(async () => {
    const pausa = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const paso = Math.max(200, Math.floor(window.innerHeight * 0.8));
    for (let y = 0; y < document.documentElement.scrollHeight; y += paso) { window.scrollTo(0, y); await pausa(40); }
    window.scrollTo(0, 0);
    for (const i of document.querySelectorAll<HTMLImageElement>('img[loading="lazy"]')) i.loading = "eager";
    await Promise.race([Promise.all([...document.images].map((i) => (i.complete ? null : i.decode().catch(() => null)))), pausa(5000)]);
  });
  await enCalma(page);
  await expect(page.locator(CARGANDO)).toHaveCount(0);
  /* Los carruseles vuelven al inicio. En la portada, «Lo más pedido» puede quedar corrido de lado: la fila se pinta
     primero con el snapshot y luego con /showcase, y el navegador sigue a la tarjeta que tenia ajustada. Depende de
     cual de las dos respuestas llega antes, asi que sin esto la captura cambiaria de una corrida a otra. */
  await page.evaluate(async () => {
    for (const el of document.querySelectorAll<HTMLElement>("body *")) if (el.scrollLeft > 0) el.scrollTo({ left: 0, behavior: "instant" });
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  });
}

/** Ancho del documento contra el de la ventana, y los elementos que quedan cortados por el borde (para que el
    fallo diga donde mirar). `documento > ventana` = hay scroll horizontal. `fuera` = algo visible se pasa del borde
    aunque la pagina no se deslice (un contenedor con overflow oculto lo recorta: el cliente lo ve cortado). */
export async function medirAncho(page: Page) {
  const ventana = page.viewportSize()!.width; // el ancho pedido: en movil innerWidth crece cuando la pagina se desborda
  return page.evaluate((w) => {
    const documento = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
    const nombre = (el: Element) => {
      const cls = typeof el.className === "string" && el.className.trim() ? `.${el.className.split(" ").filter(Boolean).slice(0, 2).join(".")}` : "";
      return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${cls}`;
    };
    /* No cuenta lo que se desliza de lado a proposito (carruseles y filas de chips: un ancestro con overflow-x
       auto o scroll), lo oculto para lectores de pantalla o fuera de escena (aria-hidden, enlace «Saltar al
       contenido») ni lo que no se ve. */
    const aProposito = (el: Element) => {
      for (let p: Element | null = el; p && p !== document.documentElement; p = p.parentElement) {
        const st = getComputedStyle(p);
        if (p !== el && (st.overflowX === "auto" || st.overflowX === "scroll")) return true;
        if (p.getAttribute("aria-hidden") === "true" || p.classList.contains("sh-skip") || p.classList.contains("sh-sr")) return true;
        if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) return true;
      }
      return false;
    };
    const fuera: string[] = [];
    for (const el of document.querySelectorAll("body *")) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || (r.right <= w + 0.5 && r.left >= -0.5)) continue;
      if (aProposito(el)) continue;
      if ([...el.children].some((c) => { const k = c.getBoundingClientRect(); return k.width > 0 && (k.right > w + 0.5 || k.left < -0.5); })) continue; // se nombra el mas interno
      fuera.push(`${nombre(el)} [${Math.round(r.left)}..${Math.round(r.right)}]`);
      if (fuera.length >= 12) break;
    }
    return { ventana: w, documento, fuera };
  }, ventana);
}
