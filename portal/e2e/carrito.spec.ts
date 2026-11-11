/* Carrito sobrio y enlace de carrito para ventas (O2-09). */
import { REF } from "./app/datos";
import { CARRITO, CATEGORIA, prod } from "./soporte/pantallas";
import { expect, medirAncho, sembrarCarrito, test, tiendaLista } from "./soporte/tienda";

test("[O2-09] el cajón tiene un único botón rojo con el total y sin scroll de lado", async ({ page }) => {
  await sembrarCarrito(page, CARRITO);
  await page.goto(CATEGORIA.ruta);
  await tiendaLista(page);
  await page.getByRole("button", { name: /^Carrito:/ }).click();
  const cajon = page.getByRole("dialog", { name: "Carrito" });
  await expect(cajon).toHaveAttribute("data-open", "true");
  await expect(cajon.getByRole("heading", { level: 2 })).toHaveText(`Tu carrito (${CARRITO.reduce((n, l) => n + l.cantidad, 0)})`);
  await expect(cajon.locator(".sh-btn--crimson")).toHaveCount(1);
  await expect(cajon.getByRole("button", { name: /^Finalizar pedido · ₡/ })).toBeVisible();
  await expect(cajon.getByRole("link", { name: CARRITO[0].nombre })).toBeVisible(); // la linea lleva a su ficha
  await expect(cajon.locator(".sh-btn--wa")).toHaveCount(0); // WhatsApp como enlace de texto
  const m = await medirAncho(page);
  expect(m.documento).toBeLessThanOrEqual(m.ventana);
});

test("[O2-09] vacío: «Tu carrito está vacío» con dos atajos", async ({ page }) => {
  await page.goto("/");
  await tiendaLista(page);
  await page.getByRole("button", { name: /^Carrito:/ }).click();
  const cajon = page.getByRole("dialog", { name: "Carrito" });
  await expect(cajon).toContainText("Tu carrito está vacío");
  await expect(cajon.getByRole("link", { name: "Ver kits" })).toBeVisible();
  await expect(cajon.getByRole("link", { name: "Ver cámaras" })).toBeVisible();
});

test("[O2-09] /?carrito= pregunta, fusiona, avisa lo que no se pudo y limpia la dirección", async ({ page }) => {
  await sembrarCarrito(page, [CARRITO[0]]);
  await page.goto(`/?carrito=240x2,${REF.porConfirmar}x1`);
  await tiendaLista(page);
  const pregunta = page.getByRole("dialog", { name: /Te compartieron un carrito con 2 productos/ });
  await expect(pregunta).toBeVisible();
  await expect(page).not.toHaveURL(/carrito=/);
  await pregunta.getByRole("button", { name: "Agregar" }).click();
  const fin = page.getByRole("dialog", { name: /Agregamos 1 producto/ });
  await expect(fin).toContainText(`No se pudo agregar`);
  await expect(fin).toContainText(prod(REF.porConfirmar).name);
  await fin.getByRole("button", { name: "Ver carrito" }).click();
  const cajon = page.getByRole("dialog", { name: "Carrito" });
  await expect(cajon.locator(".sh-line")).toHaveCount(2); // lo que ya habia sigue: fusiona, no reemplaza
});

test("[O2-09] «No, gracias» no agrega nada y las direcciones viejas siguen funcionando", async ({ page }) => {
  await page.goto("/?carrito=240x1");
  await tiendaLista(page);
  await page.getByRole("button", { name: "No, gracias" }).click();
  await expect(page.getByRole("button", { name: /^Carrito: 0 / })).toBeVisible();
  await page.goto(`/?p=${REF.comprable}`);
  await tiendaLista(page);
  await expect(page).toHaveURL(/\/p\//);
  await page.goto("/?q=camara");
  await tiendaLista(page);
  await expect(page).toHaveURL(/\/buscar\?q=camara/);
  await page.goto("/?v=mi-pedido");
  await tiendaLista(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Mi pedido");
});
