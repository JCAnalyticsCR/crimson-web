/* Base de la compra REAL de punta a punta (e2e/compra.spec.ts, plan de la tienda O0-03): el portal de verdad contra
   el API local de pruebas. A diferencia de soporte/tienda.ts, aqui el reloj NO se fija (el servidor usa la hora de
   verdad y el pedido lleva fechas suyas) y no hay API simulado: lo que se lee viene del API local.

   Los datos son los de la semilla (api/scripts/seed_tienda_e2e.py). Si alla cambia un nombre, un precio o una
   tarifa, se cambia aqui: los montos estan escritos a mano a proposito, para que un cambio de dinero no pase solo. */
import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { sinInternet } from "./tienda";

export { expect };
/* `servidor`: el API local visto desde Node (lecturas de apoyo: productos, existencia, el pedido guardado). Va por
   127.0.0.1 y no por tienda.localhost: los nombres *.localhost los resuelve el navegador, no siempre el sistema
   (en Windows no existen). Es el mismo servidor y el mismo /api que usa la pagina. */
export const test = sinInternet.extend<{ servidor: APIRequestContext }>({
  servidor: async ({ playwright, baseURL }, use) => {
    const ctx = await playwright.request.newContext({ baseURL: baseURL!.replace(/\/\/([a-z0-9-]+\.)*localhost/, "//127.0.0.1") });
    await use(ctx);
    await ctx.dispose();
  },
});

const SLUG = "crimson";
export const API = `/api/public/store/${SLUG}`;

/** Lo que deja la semilla: codigo -> estado de disponibilidad. */
export const SEMILLA: Record<string, string> = {
  "E2E-BOD-1": "en_bodega", "E2E-BOD-2": "en_bodega", "E2E-PED-1": "bajo_pedido", "E2E-PED-2": "bajo_pedido", "E2E-CON-1": "consultar", "E2E-AGO-1": "agotado",
};
/** El producto que se compra: en bodega, 1 500 + 13 % de IVA. */
export const PRODUCTO = { codigo: "E2E-BOD-1", nombre: "Cable de red de prueba 1 m", precio: 1695 };
/** Tarifa de la GAM: 4 700 + 13 % de IVA. */
export const ENVIO = { nombre: "Fast Crimson", monto: 5311 };
export const TOTAL = PRODUCTO.precio + ENVIO.monto; // 7 006
export const SINPE = { telefono: "0000-0000", titular: "Tienda Ficticia E2E" };
/** Compradora inventada. El correo es de un dominio reservado para ejemplos: ahi no llega nada (y el API de pruebas no envia). */
export const COMPRADORA = {
  nombre: "Ana Prueba Ficticia", correo: "ana.prueba@example.com", telefono: "8888 0000",
  provincia: "Heredia", canton: "Barva", distrito: "San Pedro", direccion: "Del parque 200 m norte, casa blanca",
};

/** «₡7 006» como lo escribe la tienda: el separador de miles puede ser un espacio normal, fino o de no separacion. */
export const monto = (n: number) => new RegExp(`₡\\s?${String(n).replace(/\B(?=(\d{3})+$)/g, "\\s")}(?!\\d)`);

type Publico = { id: number; code: string; name: string; path?: string; display_price: number; availability: { state: string; cantidad: number | null } };

/** Los productos de la semilla tal como los publica el API (id, ficha y disponibilidad de este momento). */
export async function productosSembrados(request: APIRequestContext): Promise<Record<string, Publico>> {
  const r = await request.get(`${API}/products?q=E2E`);
  expect(r.ok(), `El API local no responde (${r.status()}): ¿está levantada y sembrada la tienda de pruebas? Ver e2e/README.md`).toBeTruthy();
  return Object.fromEntries(((await r.json()) as Publico[]).map((p) => [p.code, p]));
}

/** Unidades libres del producto en este momento (lo que la tienda puede vender ya). */
export async function existencia(request: APIRequestContext, id: number): Promise<number> {
  const r = await request.get(`${API}/availability?ids=${id}`);
  expect(r.ok()).toBeTruthy();
  return Number(((await r.json()) as Record<string, { cantidad: number | null }>)[String(id)].cantidad);
}

type Pedido = {
  number: string; status: string; status_label: string; payment_kind: string | null; shipping_method: string | null; total: number; needs_proof: boolean;
  lines: { name: string; quantity: number; shipping?: boolean }[];
};
/** El pedido como lo guardo el servidor, leido con su enlace de seguimiento. */
export async function pedidoGuardado(request: APIRequestContext, token: string): Promise<Pedido> {
  const r = await request.get(`${API}/orders/${encodeURIComponent(token)}`);
  expect(r.ok(), "el enlace de seguimiento no abre el pedido").toBeTruthy();
  return (await r.json()) as Pedido;
}

/** La tienda termino de cargar: hay cabecera y no quedan esqueletos ni ruedas de carga. */
export async function tiendaLista(page: Page) {
  await page.locator(".shop .sh-head").waitFor();
  await expect(page.locator(".spinner, .sh-skel, .vt-spin, .vt-kit--skel")).toHaveCount(0);
}

/** Abre la ficha del producto y espera a que este completa (la zona de compra ya llego del API). */
export async function abrirFicha(page: Page, p: { id: number; name: string; path?: string }) {
  await page.goto(p.path || `/p/producto-${p.id}`);
  await tiendaLista(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(p.name);
  await expect(page.locator(".vt-buybox")).toBeVisible();
}

/** Lo que se revisa al final de toda compra: la pantalla «Mi pedido», el pedido en el servidor y la existencia. */
export async function revisarPedido(page: Page, request: APIRequestContext, antes: { id: number; libres: number }) {
  // «Mi pedido» con el numero, el estado y como pagar
  await expect(page).toHaveURL(/[?&]pedido=[^&]+/);
  const titulo = page.getByRole("heading", { level: 1 });
  await expect(titulo).toHaveText(/^ORD-\d{6}$/);
  const numero = (await titulo.textContent())!.trim();
  await expect(page.locator(".ck-thanks")).toContainText("Recibimos tu pedido");
  // sin servicio de correo el API no dice «te enviamos un correo»: la pantalla lo cuenta tal cual
  await expect(page.locator(".ck-thanks")).toContainText("No pudimos enviarte el correo de confirmación");
  await expect(page.locator(".ck-badge")).toHaveText("Pendiente de pago");
  const pago = page.locator(".ck-pay");
  await expect(pago.getByRole("heading", { level: 2 })).toHaveText("Cómo pagar · SINPE Móvil");
  const instrucciones = pago.locator(".ck-instr");
  await expect(instrucciones).toContainText(`por SINPE Móvil al ${SINPE.telefono} a nombre de ${SINPE.titular}`);
  await expect(instrucciones).toContainText(`número de pedido ${numero}`);
  await expect(instrucciones).toContainText(monto(TOTAL));
  await expect(pago.getByText("Subir comprobante", { exact: true })).toBeVisible();
  const totales = page.getByRole("complementary", { name: "Productos y totales" });
  await expect(totales).toContainText(`1 × ${PRODUCTO.nombre}`);
  await expect(totales.locator(".sh-totrow--big")).toContainText(monto(TOTAL));
  await expect(page.getByRole("button", { name: /^Carrito: 0 / })).toBeVisible(); // el carrito quedo vacio

  // el pedido existe en el servidor, con lo que se eligio y el total que se vio
  const token = new URL(page.url()).searchParams.get("pedido")!;
  const o = await pedidoGuardado(request, token);
  expect(o).toMatchObject({ number: numero, status: "nuevo", status_label: "Pendiente de pago", payment_kind: "sinpe", shipping_method: ENVIO.nombre, needs_proof: true });
  expect(Number(o.total)).toBe(TOTAL);
  expect(o.lines.filter((l) => !l.shipping).map((l) => [l.name, Number(l.quantity)])).toEqual([[PRODUCTO.nombre, 1]]);
  // y aparto existencia de verdad: queda una unidad libre menos
  expect(await existencia(request, antes.id)).toBe(antes.libres - 1);
  // la pantalla final queda en el informe (playwright-report/compra): evidencia de cada corrida
  await test.info().attach(`mi-pedido-${numero}`, { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  return { numero, token };
}

// ---------- solo con teclado ----------
/** Registra cualquier uso de raton, dedo o lapiz en la pagina: la compra con teclado tiene que terminar en cero. */
export async function vigilarPuntero(page: Page) {
  await page.addInitScript(() => {
    const w = window as Window & { __puntero?: string[] };
    w.__puntero = [];
    for (const tipo of ["pointerdown", "mousedown", "touchstart"]) addEventListener(tipo, (e) => w.__puntero!.push(`${tipo} en ${(e.target as Element | null)?.tagName ?? "?"}`), true);
  });
}
export const usoDePuntero = (page: Page) => page.evaluate(() => (window as Window & { __puntero?: string[] }).__puntero ?? []);

/** Tab (o Mayus+Tab) hasta que el foco quede en `destino`. Si no se llega, ese control no se alcanza con teclado. */
export async function tabHasta(page: Page, destino: Locator, { atras = false, max = 150 } = {}) {
  await expect(destino, "el control al que hay que llegar con Tab no está en la página").toHaveCount(1);
  const enFoco = () => destino.evaluate((el) => el === document.activeElement);
  for (let i = 0; i < max && !(await enFoco()); i++) await page.keyboard.press(atras ? "Shift+Tab" : "Tab");
  await expect(destino, `con ${max} pulsaciones de Tab no se llega al control`).toBeFocused();
}

/** Escribe en el campo que tiene el foco y revisa que quedo escrito. */
export async function escribir(page: Page, campo: Locator, texto: string) {
  await tabHasta(page, campo);
  await page.keyboard.type(texto);
  await expect(campo).toHaveValue(texto);
}

/** Elige una opcion de una lista desplegable con el teclado: llega con Tab y escribe el nombre de la opcion (la
    lista salta a ella). Si ese navegador no salta al escribir, baja con la flecha desde la primera: tambien es teclado. */
export async function elegirDeLista(page: Page, lista: Locator, opcion: string) {
  await tabHasta(page, lista);
  await page.keyboard.type(opcion);
  if ((await lista.inputValue()) !== opcion) {
    await page.keyboard.press("Home");
    const total = await lista.locator("option").count();
    for (let i = 0; i < total && (await lista.inputValue()) !== opcion; i++) await page.keyboard.press("ArrowDown");
  }
  await expect(lista).toHaveValue(opcion);
  await expect(lista).toBeFocused();
}

/** Elige una opcion de un grupo de botones de radio con el teclado: Tab hasta el grupo y flechas hasta la opcion. */
export async function elegirRadio(page: Page, grupo: Locator, opcion: Locator) {
  const marcada = grupo.locator("input[type=radio]:checked");
  // Tab entra a un grupo de radios por la opcion marcada; si no hay ninguna, por la primera
  await tabHasta(page, (await marcada.count()) ? marcada : grupo.locator("input[type=radio]").first());
  const total = await grupo.locator("input[type=radio]").count();
  for (let i = 0; i < total && !(await opcion.isChecked()); i++) await page.keyboard.press("ArrowDown");
  await expect(opcion).toBeChecked();
  await expect(opcion).toBeFocused();
}
