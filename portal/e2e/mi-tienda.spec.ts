/* Mi Tienda (panel) contra el API local de pruebas, a 768 px (y el scroll, tambien a 375): va con
   `npm run e2e:compra`, que levanta el API, el portal y crea un administrador de prueba con una contrasena al azar
   (playwright.compra.config.ts).
   - O1-11b: no se puede despublicar con un solo clic; escribir un titular y guardar un banner no pierde nada.
   - O4-01: cada seccion tiene su direccion (recargar deja en la misma), la bandera panel_v2 apagada vuelve al panel de
     una pagina y ninguna seccion tiene scroll de lado a 375 ni a 768 px. */
import { createHmac } from "node:crypto";
import type { APIRequestContext, Page, TestInfo } from "@playwright/test";
import { expect, test } from "./soporte/compra";

const ADMIN = "panel.e2e@example.com";
const CLAVE = process.env.E2E_PANEL_PASSWORD || "";
/* O2-01d: segundo administrador, con el 2FA activo (secreto TOTP al azar en cada corrida: playwright.compra.config.ts) */
const ADMIN_2FA = "panel.2fa.e2e@example.com";
const TOTP = process.env.E2E_PANEL_TOTP || "";

/** Codigo TOTP de 6 digitos (RFC 6238, 30 s, SHA-1), el mismo que muestra la app de autenticacion. */
function codigoTotp(secreto: string, paso = Math.floor(Date.now() / 30_000)): string {
  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secreto.replace(/=+$/, "").toUpperCase()) bits += B32.indexOf(c).toString(2).padStart(5, "0");
  const llave = Buffer.from((bits.match(/.{8}/g) || []).map((x) => parseInt(x, 2)));
  const contador = Buffer.alloc(8);
  contador.writeBigUInt64BE(BigInt(paso));
  const h = createHmac("sha1", llave).update(contador).digest();
  const o = h[h.length - 1] & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}
/* el panel no vive en tienda.localhost (ese nombre monta la tienda): mismo servidor, por 127.0.0.1 */
/* los rotulos del panel (Field) no van atados al campo: se busca el campo por el rotulo de su caja */
const campo = (page: Page, rotulo: string, dentro = "") =>
  page.locator(`${dentro} .field`.trim()).filter({ has: page.locator(`label:text-is("${rotulo}")`) }).locator("input, textarea");
const panel = (baseURL: string | undefined) => (baseURL || "").replace(/\/\/([a-z0-9-]+\.)*localhost/, "//127.0.0.1");
const solo768 = (info: TestInfo) => test.skip(info.project.name !== "compra-w768", "criterio del panel: 768 px");

/* dentro de la aplicacion (el token vive en memoria: un goto cargaria la pagina de nuevo) */
async function ir(page: Page, ruta: string) {
  await page.evaluate((r) => { history.pushState({}, "", r); dispatchEvent(new PopStateEvent("popstate")); }, ruta);
}

async function login(page: Page) {
  await page.locator('input[type="email"]').fill(ADMIN);
  await page.locator('input[autocomplete="current-password"]').fill(CLAVE);
  await page.locator("button.lg-go").click();
  // la bienvenida sale despues de cada ingreso (sessionStorage), a veces unos segundos despues de pintar el panel
  const hola = page.locator(".welcome");
  if (await hola.waitFor({ timeout: 15000 }).then(() => true, () => false)) {
    await hola.getByRole("button", { name: /Empezar/ }).click();
    await expect(hola).toBeHidden();
  }
}

async function entrar(page: Page, base: string, ruta = "/mi-tienda") {
  await page.goto(`${base}/login`);
  await login(page);
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  await ir(page, ruta);
  await expect(page.locator(".mt-hub")).toBeVisible({ timeout: 30_000 });
}

async function token(servidor: APIRequestContext): Promise<string> {
  const r = await servidor.post("/api/auth/login", { data: { email: ADMIN, password: CLAVE } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { access_token: string }).access_token;
}

const sinScrollDeLado = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
/* si hay scroll de lado, que elementos se salen (para el mensaje de la prueba) */
const seSalen = (page: Page) => page.evaluate(() => {
  const w = document.documentElement.clientWidth;
  return [...document.querySelectorAll("body *")].filter((e) => e.getBoundingClientRect().right > w + 1).slice(0, 8)
    .map((e) => `${e.tagName.toLowerCase()}${e.className && typeof e.className === "string" ? `.${e.className.trim().split(/\s+/).join(".")}` : ""}`).join(" | ");
});
async function sinScroll(page: Page, donde = "") {
  const extra = await sinScrollDeLado(page);
  expect(extra, `${donde} se sale: ${extra > 0 ? await seSalen(page) : ""}`).toBeLessThanOrEqual(0);
}

test.beforeEach(() => {
  test.skip(!CLAVE, "sin administrador de prueba (API ya levantado aparte): ver playwright.compra.config.ts");
});

test("[O1-11b] despublicar pide confirmación con el efecto escrito", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  await entrar(page, panel(baseURL), "/mi-tienda/publicacion");
  await page.getByRole("button", { name: "Despublicar" }).click();
  const ventana = page.locator(".modal, [role=dialog]").filter({ hasText: "¿Despublicar la tienda?" });
  await expect(ventana).toBeVisible();
  await expect(ventana).toContainText("La tienda dejará de verse para sus clientes");
  await ventana.getByRole("button", { name: "Cancelar" }).click();
  await expect(ventana).toBeHidden();
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  expect(((await (await servidor.get("/api/store", { headers: h })).json()) as { published: boolean }).published).toBe(true);
});

test("[O1-11b/O4-02] titular y banner en la misma sesión: un solo guardado, nada al perder el foco", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  await entrar(page, panel(baseURL), "/mi-tienda/portada");
  const puts: string[] = [];
  page.on("request", (r) => { if (r.method() === "PUT") puts.push(r.url()); });
  const titular = `Titular de prueba ${Date.now() % 100000}`;
  await campo(page, "Titular de la portada").fill(titular);
  await page.getByRole("button", { name: "Banner", exact: true }).click();  // antes, este clic guardaba el titular al perder el foco
  await campo(page, "Título", '[data-editor="banners"]').last().fill("Promo de prueba");
  await expect(page.getByText("Este banner no se muestra hasta que tenga título e imagen.").last()).toBeVisible();
  const barra = page.getByRole("region", { name: "Cambios sin guardar" });
  await expect(barra).toContainText("2 cambios sin guardar");
  expect(puts).toEqual([]);  // nada se guardo todavia
  const guardado = page.waitForResponse((r) => r.url().endsWith("/store/showcase") && r.request().method() === "PUT");
  await barra.getByRole("button", { name: "Guardar y publicar" }).click();
  expect((await guardado).ok()).toBeTruthy();
  await expect(barra).toBeHidden();
  await expect(page.getByRole("button", { name: "Deshacer" })).toBeVisible();
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const sc = (await (await servidor.get("/api/store/showcase", { headers: h })).json()) as { headline: string; banners: { title: string; active: boolean }[] };
  expect(sc.headline).toBe(titular);
  expect(sc.banners.map((b) => [b.title, b.active])).toContainEqual(["Promo de prueba", false]);  // nace inactivo (O1-04)
  await sinScroll(page);
});

test("[O4-02] deshacer, cambio de sección protegido, copia local y 409", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const frase = async () => ((await (await servidor.get("/api/store", { headers: h })).json()) as { tagline: string }).tagline;
  await entrar(page, panel(baseURL), "/mi-tienda/apariencia");
  const antes = await frase();
  const barra = page.getByRole("region", { name: "Cambios sin guardar" });
  // guardar y deshacer
  await campo(page, "Frase").fill("Frase de prueba uno");
  await barra.getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(frase).toBe("Frase de prueba uno");
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect.poll(frase).toBe(antes);
  // cambiar de seccion con cambios pide confirmacion; la copia local se ofrece al volver
  await campo(page, "Frase").fill("Frase sin guardar");
  await page.getByRole("navigation", { name: "Secciones de Mi Tienda" }).getByRole("link", { name: "Portada" }).click();
  const aviso = page.locator(".modal").filter({ hasText: "Hay cambios sin guardar" });
  await expect(aviso).toBeVisible();
  await aviso.getByRole("button", { name: "Salir sin guardar" }).click();
  await expect(page).toHaveURL(/\/mi-tienda\/portada$/);
  await expect(campo(page, "Titular de la portada")).toBeVisible();  // Apariencia ya se desmonto
  await ir(page, "/mi-tienda/apariencia");
  await expect(barra).toContainText("en este dispositivo");
  await barra.getByRole("button", { name: "Recuperarlos" }).click();
  await expect(campo(page, "Frase")).toHaveValue("Frase sin guardar");
  // otro guardo en el medio: 409 con el mensaje, nada se pisa
  expect((await servidor.put("/api/store", { headers: h, data: { tagline: "Escrita en otra pestaña" } })).ok()).toBeTruthy();
  await barra.getByRole("button", { name: "Guardar y publicar" }).click();
  await expect(barra).toContainText("Alguien más guardó cambios en esta sección. Recargue para verlos.");
  expect(await frase()).toBe("Escrita en otra pestaña");
  await barra.getByRole("button", { name: "Recargar" }).click();
  await expect(campo(page, "Frase")).toHaveValue("Escrita en otra pestaña");
  await servidor.put("/api/store", { headers: h, data: { tagline: antes } });
});

test("[O4-02] el historial restaura sin tocar la publicación", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  await servidor.put("/api/store/showcase", { headers: h, data: { subline: "Bajada original" } });
  await servidor.put("/api/store/showcase", { headers: h, data: { subline: "Bajada cambiada" } });
  await entrar(page, panel(baseURL), "/mi-tienda/historial");
  const fila = page.locator("tbody tr").filter({ hasText: "Bajada" }).first();
  await fila.getByRole("button", { name: "Restaurar" }).click();
  await page.locator(".modal").getByRole("button", { name: "Restaurar" }).click();
  await expect.poll(async () => ((await (await servidor.get("/api/store/showcase", { headers: h })).json()) as { subline: string }).subline).toBe("Bajada original");
  expect(((await (await servidor.get("/api/store", { headers: h })).json()) as { published: boolean }).published).toBe(true);
  await sinScroll(page);
});

test("[O4-01] cada sección tiene su dirección y recargar deja en la misma", async ({ page, baseURL }, info) => {
  solo768(info);
  await entrar(page, panel(baseURL));
  const menu = page.getByRole("navigation", { name: "Secciones de Mi Tienda" });
  await menu.getByRole("link", { name: "Pagos y envíos" }).click();
  await expect(page).toHaveURL(/\/mi-tienda\/pagos-envios$/);
  await expect(page.getByRole("heading", { level: 1, name: "Pagos y envíos" })).toBeVisible();
  await menu.getByRole("link", { name: "Páginas" }).click();
  await expect(page).toHaveURL(/\/mi-tienda\/paginas$/);
  await page.reload();
  // el token vive en memoria: si la recarga pide entrar de nuevo, la direccion no cambia y vuelve a la misma seccion
  await page.locator('.mt-hub, input[type="email"]').first().waitFor();
  if (await page.locator('input[type="email"]').isVisible()) await login(page);
  await expect(page).toHaveURL(/\/mi-tienda\/paginas$/);
  await expect(page.getByRole("heading", { level: 1, name: "Páginas" })).toBeVisible();
});

test("[O4-01] con la bandera apagada el panel es el de una sola página", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  expect((await servidor.put("/api/store", { headers: h, data: { panel_v2: false } })).ok()).toBeTruthy();
  try {
    await page.goto(`${panel(baseURL)}/login`);
    await login(page);
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    await ir(page, "/mi-tienda");
    await expect(page.getByText("Configura el sitio a tu gusto")).toBeVisible();
    await expect(page.locator(".mt-hub")).toHaveCount(0);
    await page.getByRole("button", { name: "Usar Mi Tienda por secciones" }).click();
    await expect(page.locator(".mt-hub")).toBeVisible();
  } finally {
    await servidor.put("/api/store", { headers: h, data: { panel_v2: true } });
  }
});

test("[O4-01] ninguna sección tiene scroll de lado", async ({ page, baseURL }) => {
  await entrar(page, panel(baseURL));
  for (const ruta of ["/mi-tienda", "/mi-tienda/apariencia", "/mi-tienda/portada", "/mi-tienda/kits", "/mi-tienda/paginas", "/mi-tienda/catalogo", "/mi-tienda/categorias", "/mi-tienda/pagos-envios", "/mi-tienda/seo", "/mi-tienda/publicacion", "/mi-tienda/historial"]) {
    await ir(page, ruta);
    await expect(page.locator(".mt-hub__body")).toBeVisible();
    await page.waitForLoadState("networkidle");
    await sinScroll(page, ruta);
  }
  // a 375 px el menu es un selector; a 768, la fila de fichas
  const w = page.viewportSize()?.width || 0;
  if (w <= 480) await expect(page.getByLabel("Sección de Mi Tienda")).toBeVisible();
  else await expect(page.getByRole("navigation", { name: "Secciones de Mi Tienda" })).toBeVisible();
});

test("[O4-03] el resumen dice qué falta y lleva a corregirlo", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const salud = (await (await servidor.get("/api/store/health", { headers: h })).json()) as { indicadores: { publicados: number; visibles: number }; avance: { listos: number; total: number }; items: { ok: boolean; to: string; texto: string }[] };
  const inv = (await (await servidor.get("/api/store/inventory", { headers: h })).json()) as { summary: { publicados: number } };
  expect(salud.indicadores.publicados).toBe(inv.summary.publicados);
  await entrar(page, panel(baseURL));
  await expect(page.getByRole("list", { name: "Indicadores de la tienda" })).toContainText("Visibles en la tienda");
  await expect(page.getByText(`${salud.avance.listos} de ${salud.avance.total} listos`)).toBeVisible();
  const falta = salud.items.find((x) => !x.ok);
  if (falta) {
    const fila = page.locator(".rs-item").filter({ hasText: falta.texto });
    await fila.getByRole("link", { name: "Corregir" }).click();
    await expect(page).toHaveURL((u) => u.pathname + u.search === falta.to);
  }
  await sinScroll(page);
});

test("[O4-04] catálogo en cuadrícula con filtros y la ficha web en un cajón", async ({ page, baseURL, servidor }) => {
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const lista = (await (await servidor.get("/api/store/catalog", { headers: h })).json()) as { total: number; items: { id: number; name: string }[]; faltas: { key: string; n: number }[] };
  expect(lista.total).toBeGreaterThan(0);
  await entrar(page, panel(baseURL), "/mi-tienda/catalogo");
  const sinFoto = lista.faltas.find((f) => f.key === "sin_descripcion")!;
  const chip = page.getByRole("group", { name: "Filtros de calidad" }).getByRole("button", { name: new RegExp(`^Sin descripción ${sinFoto.n}`) });
  await chip.click();
  await expect(page).toHaveURL(/falta=sin_descripcion/);
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  await chip.click();  // quitar el filtro
  const primero = lista.items[0];
  await page.locator(".cw-card").filter({ hasText: primero.name }).first().getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`/mi-tienda/catalogo/${primero.id}`));
  const cajon = page.getByRole("dialog", { name: `Ficha web: ${primero.name}` });
  await expect(cajon).toBeVisible();
  await expect(cajon.locator(".vt-card")).toBeVisible();  // la tarjeta real de la tienda
  await cajon.getByRole("tab", { name: "Google" }).click();
  await expect(cajon.getByLabel("Vista previa en Google")).toBeVisible();
  await expect(cajon.getByText(/de 60 caracteres/)).toBeVisible();
  await cajon.getByRole("tab", { name: "Nombre y textos" }).click();
  const texto = `Descripción de prueba ${Date.now() % 100000}`;
  await cajon.locator(".field").filter({ has: page.locator('label:text-is("Descripción para la tienda")') }).locator("textarea").fill(texto);
  const barra = cajon.getByRole("region", { name: "Cambios sin guardar" });
  await expect(barra).toContainText("1 cambio sin guardar");
  await barra.getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(async () => ((await (await servidor.get(`/api/store/catalog/${primero.id}`, { headers: h })).json()) as { description_store: string }).description_store).toBe(texto);
  await sinScroll(page);
  await page.keyboard.press("Escape");
  await expect(cajon).toBeHidden();
  await expect(page).toHaveURL(/\/mi-tienda\/catalogo$/);
  // la tabla apilada en tablet y celular, sin scroll de lado
  await page.getByRole("button", { name: "Tabla" }).click();
  await expect(page.locator("table.cw-table")).toBeVisible();
  await sinScroll(page);
});

test("[O4-05] edición en lote con vista previa, confirmación y deshacer; precio escrito con IVA", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const fijados = async () => ((await (await servidor.get("/api/store/catalog", { headers: h })).json()) as { items: { web_featured: boolean }[] }).items.filter((x) => x.web_featured).length;
  const antes = await fijados();
  await entrar(page, panel(baseURL), "/mi-tienda/catalogo");
  await page.getByLabel("Seleccionar esta página").check();
  const barra = page.getByRole("region", { name: "Edición en lote" });
  await expect(barra).toContainText("seleccionados");
  await barra.getByRole("button", { name: "Fijar arriba" }).click();
  const vista = page.locator(".modal").filter({ hasText: "Fijar arriba" });
  await expect(vista.getByRole("table", { name: "Antes y después" })).toBeVisible();
  const filas = await vista.locator("tbody tr").count();
  expect(await fijados()).toBe(antes);  // la vista previa no cambia nada
  await vista.getByRole("button", { name: /^Aplicar a/ }).click();
  await expect.poll(fijados).toBe(antes + filas);
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect.poll(fijados).toBe(antes);
  await sinScroll(page, "lote");
  // precio web: se escribe final con IVA y se ve el neto que se guarda (no se guarda: se descarta)
  await page.locator(".cw-card").first().getByRole("link").click();
  const cajon = page.getByRole("dialog", { name: /Ficha web/ });
  await cajon.getByRole("tab", { name: "Precio" }).click();
  await cajon.locator(".field").filter({ has: page.locator('label:text-is("Precio web final con IVA")') }).locator("input").fill("1.950");
  await expect(cajon.getByText("1725.66372")).toBeVisible();
  await cajon.getByRole("region", { name: "Cambios sin guardar" }).getByRole("button", { name: "Descartar" }).click();
});

test("[O4-08] pagos y envíos en una sección, con lo que ve el cliente; SEO con vista previa de Google", async ({ page, baseURL, servidor }) => {
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  await entrar(page, panel(baseURL), "/mi-tienda/pagos-envios");
  await expect(page.getByText("Así lo ve su cliente en el checkout")).toBeVisible();
  await expect(page.getByRole("table", { name: "Tarifas de envío" })).toBeVisible();
  await sinScroll(page, "pagos y envíos");
  const dir = `Oficina de prueba ${Date.now() % 1000}`;
  await page.locator(".field").filter({ has: page.locator('label:text-is("Dirección de retiro")') }).locator("input").fill(dir);
  const barra = page.getByRole("region", { name: "Cambios sin guardar" });
  await barra.getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(async () => ((await (await servidor.get("/api/store/checkout", { headers: h })).json()) as { checkout: { pickup: { address: string } } }).checkout.pickup.address).toBe(dir);
  // el resumen deja de marcar «retiro sin dirección»
  const salud = (await (await servidor.get("/api/store/health", { headers: h })).json()) as { items: { key: string; ok: boolean }[] };
  expect(salud.items.find((x) => x.key === "retiro")?.ok ?? true).toBe(true);

  await ir(page, "/mi-tienda/seo");
  const titulo = `Tienda de prueba ${Date.now() % 100000}`;
  await page.locator(".field").filter({ has: page.locator('label:text-is("Título de la portada")') }).locator("input").fill(titulo);
  await expect(page.getByLabel("Vista previa en Google")).toContainText(titulo);
  await expect(page.getByText(new RegExp(`${titulo.length} de 60 caracteres`))).toBeVisible();
  await sinScroll(page, "seo");
  await page.getByRole("region", { name: "Cambios sin guardar" }).getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(async () => ((await (await servidor.get("/api/store/showcase", { headers: h })).json()) as { seo_title: string }).seo_title).toBe(titulo);
  await expect(page.getByText(/analytics|píxel/i)).toHaveCount(0);  // sin medición de terceros
});

test("[O4-07] portada ordenable con teclado, categorías y colecciones por regla", async ({ page, baseURL, servidor }, info) => {
  solo768(info);
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  const slug = ((await (await servidor.get("/api/store", { headers: h })).json()) as { slug: string }).slug;
  const orden = async () => ((await (await servidor.get(`/api/public/store/${slug}/showcase`)).json()) as { sections: { key: string }[] }).sections.map((s) => s.key);
  const antes = await orden();
  await entrar(page, panel(baseURL), "/mi-tienda/portada");
  // con el teclado: foco en «Bajar Portada principal» y Enter
  await page.getByRole("button", { name: "Bajar Portada principal" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".pd-row").first()).toContainText("Sellos de confianza");
  await page.getByRole("region", { name: "Cambios sin guardar" }).getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(orden).toEqual([antes[1], antes[0], ...antes.slice(2)]);
  await sinScroll(page, "portada");
  // la tienda publica aplica el orden: los sellos antes del hero
  const tienda = await page.context().newPage();
  await tienda.goto(`${baseURL}/`);
  await expect(tienda.locator(".vt-hero")).toBeVisible();
  // el orden llega con /showcase (el snapshot del navegador se rearma despues): se espera a que se aplique
  await expect.poll(() => tienda.evaluate(() => { const s = document.querySelector(".vt-trust"); const h = document.querySelector(".vt-hero"); return !!s && !!h && !!(s.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_FOLLOWING); })).toBe(true);
  await tienda.close();
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect.poll(orden).toEqual(antes);

  await ir(page, "/mi-tienda/categorias");
  await page.getByRole("button", { name: "Listo para entregar" }).click();
  await page.getByRole("region", { name: "Cambios sin guardar" }).getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(async () => (await servidor.get(`/api/public/store/${slug}/collections/listo-para-entregar`)).status()).toBe(200);
  expect((await servidor.get(`/api/public/store/${slug}/collections/no-existe`)).status()).toBe(404);
  await sinScroll(page, "categorías");
});

test("[O4-06] apariencia con vista previa en vivo y aviso de contraste", async ({ page, baseURL, servidor }) => {
  const h = { Authorization: `Bearer ${await token(servidor)}` };
  await entrar(page, panel(baseURL), "/mi-tienda/apariencia");
  const vista = page.locator(".ap-shop");
  await expect(vista.locator(".vt-card").first()).toBeVisible();  // la tarjeta real de la tienda
  const acento = page.getByLabel("Color de acento: código");
  await acento.fill("#f5d90a");
  await expect(page.getByRole("alert").filter({ hasText: "menos de 4,5" })).toBeVisible();
  await acento.fill("#1d4ed8");
  await expect(vista).toHaveAttribute("style", /--crimson: #1d4ed8/);  // se ve al instante, sin guardar
  for (const ancho of ["Teléfono", "Tablet", "Escritorio"]) {
    await page.getByRole("group", { name: "Ancho de la vista previa" }).getByRole("button", { name: ancho }).click();
    await sinScroll(page, `apariencia ${ancho}`);
  }
  await page.getByRole("button", { name: "Pantalla completa" }).click();
  await expect(page.getByRole("dialog", { name: "Vista previa de la tienda" })).toBeVisible();
  await page.getByRole("dialog", { name: "Vista previa de la tienda" }).getByRole("button", { name: "Cerrar", exact: true }).click();
  await page.getByRole("region", { name: "Cambios sin guardar" }).getByRole("button", { name: "Guardar y publicar" }).click();
  await expect.poll(async () => ((await (await servidor.get("/api/store", { headers: h })).json()) as { theme?: { accent?: string } }).theme?.accent).toBe("#1d4ed8");
  const slug = ((await (await servidor.get("/api/store", { headers: h })).json()) as { slug: string }).slug;
  expect(((await (await servidor.get(`/api/public/store/${slug}`)).json()) as { store: { theme: { accent: string } } }).store.theme.accent).toBe("#1d4ed8");
  // la tienda publica lo aplica como variable de .shop
  const tienda = await page.context().newPage();
  await tienda.goto(`${baseURL}/`);
  await expect(tienda.locator("div.shop").first()).toHaveAttribute("style", /--crimson: #1d4ed8/);
  await tienda.close();
  await servidor.put("/api/store", { headers: h, data: { theme: { accent: "#e2233a" }, primary: "#e2233a" } });
});

/* O2-01d: el panel ya no viene en la entrada (src/app/PanelApp.tsx, con lazy()). Su arranque completo, con segundo
   factor: correo y contrasena, el paso «Verificación en dos pasos», el codigo de la app y adentro del panel. */
test("[O2-01d] inicio de sesión del panel con segundo factor (2FA)", async ({ page, baseURL }) => {
  test.skip(!TOTP, "sin administrador con 2FA (API ya levantado aparte)");
  const base = panel(baseURL);
  await page.goto(`${base}/login`);
  await page.locator('input[type="email"]').fill(ADMIN_2FA);
  await page.locator('input[autocomplete="current-password"]').fill(CLAVE);
  await page.locator("button.lg-go").click();
  await expect(page.getByRole("heading", { name: "Verificación en dos pasos" })).toBeVisible();
  // un codigo de una ventana de 30 s que no sea la que esta por vencer (el API acepta un paso a cada lado)
  if (30 - (Math.floor(Date.now() / 1000) % 30) < 3) await page.waitForTimeout(3500);
  await page.locator('input[autocomplete="one-time-code"]').fill(codigoTotp(TOTP));
  await page.locator("button.lg-go").click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  const hola = page.locator(".welcome");
  if (await hola.waitFor({ timeout: 3000 }).then(() => true, () => false)) await hola.getByRole("button", { name: /Empezar/ }).click();
  // el cascaron del panel (menu lateral con sus secciones) esta montado
  await expect(page.getByRole("complementary").getByRole("link", { name: "Inicio" })).toBeAttached();
  await expect(page.getByRole("heading", { name: "Verificación en dos pasos" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
});
