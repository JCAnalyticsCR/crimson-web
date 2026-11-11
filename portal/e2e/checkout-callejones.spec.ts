/* O1-10c: checkout sin callejones. Un producto que ya no se puede comprar en linea se avisa desde el paso 1, sin abrir
   el resumen, y «Quitar y continuar» deja terminar la compra; la cabecera del checkout es corta; el comparador no
   ofrece agregar lo que no se compra en linea. Catalogo y montos ficticios. */
import { REF } from "./app/datos";
import { linea, rutaFicha } from "./soporte/pantallas";
import { COMPRADORA, expect, sembrarCarrito, test, tiendaLista } from "./soporte/tienda";

test("[O1-10c] por confirmar en el carrito: aviso visible desde el paso 1 y «Quitar y continuar» deja comprar", async ({ page }, info) => {
  test.skip(![375, 1280].includes(info.project.use.viewport!.width), "criterio a 375 px (y escritorio)");
  await sembrarCarrito(page, [linea(REF.comprable, 1), linea(REF.porConfirmar, 1)]);
  await page.goto("/?v=pagar");
  await tiendaLista(page);
  const aviso = page.getByRole("alert").filter({ hasText: "ya no se puede comprar en línea" });
  await expect(aviso).toBeVisible();
  await expect(page.locator(".ck-mini")).not.toHaveAttribute("open", "");
  await expect(aviso.getByRole("link", { name: /Consultar .* por WhatsApp/ })).toBeVisible();
  // cabecera corta: logo, «Pago seguro» y «Volver a la tienda»; sin buscador ni carrito
  await expect(page.locator(".ck-head")).toContainText("Pago seguro");
  await expect(page.getByRole("button", { name: "Volver a la tienda" })).toBeVisible();
  await expect(page.locator("header .sh-search")).toHaveCount(0);

  await aviso.getByRole("button", { name: "Quitar y continuar" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "ya no se puede comprar en línea" })).toHaveCount(0);

  const titulo = page.getByRole("heading", { level: 1 });
  const seguir = page.getByRole("button", { name: "Continuar" });
  await page.getByLabel("Nombre completo").fill(COMPRADORA.name);
  await page.getByLabel("Correo").fill(COMPRADORA.email);
  await page.getByLabel("Teléfono / WhatsApp").fill(COMPRADORA.phone);
  await seguir.click();
  await expect(titulo).toContainText("Entrega");
  await page.getByLabel("Provincia").selectOption(COMPRADORA.province);
  await page.getByLabel("Cantón").selectOption(COMPRADORA.canton);
  await page.getByLabel("Dirección exacta (otras señas)").fill(COMPRADORA.address);
  await page.locator("label.sh-opt", { hasText: COMPRADORA.rate }).click();
  await seguir.click();
  await expect(titulo).toContainText("Pago");
  await page.locator("label.sh-opt", { hasText: "SINPE Móvil" }).click();
  await seguir.click();
  await expect(titulo).toContainText("Confirmar");
  await page.locator("label.ck-check input[type=checkbox]").check();
  await page.getByRole("button", { name: /^Confirmar pedido/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(/^(ORD|DEMO)-[0-9]+$/); // el numero del pedido (simulado)
});

test("[O1-10c] el comparador no ofrece agregar lo que no se compra en línea", async ({ page }) => {
  await page.addInitScript((ids) => localStorage.setItem("crimson-shop:cmp:crimson", JSON.stringify(ids)), [REF.comprable, REF.porConfirmar]);
  await page.goto("/comparar");
  await tiendaLista(page);
  const acciones = page.locator("tr", { has: page.locator("th", { hasText: "Acciones" }) });
  await expect(acciones.getByRole("button", { name: "Agregar" })).toHaveCount(1);
  await expect(acciones.getByRole("link", { name: /^Consultar/ })).toHaveCount(1);
});

test("[O1-10c] la ficha por confirmar sigue con su venta asistida (sin cambios)", async ({ page }) => {
  await page.goto(rutaFicha(REF.porConfirmar));
  await tiendaLista(page);
  await expect(page.locator(".vt-buybox button:disabled")).toHaveCount(0);
});
