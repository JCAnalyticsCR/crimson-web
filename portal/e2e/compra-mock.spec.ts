/* Recorrido de compra sobre el API simulado: ficha -> agregar -> aviso «Ir al carrito» -> carrito -> los cuatro pasos
   del checkout -> «Mi pedido». Protege que el recorrido no se rompa al tocar la vitrina, el carrito o el checkout.

   NO es la prueba de compra real: aqui no hay API, inventario ni pago (el pedido lo inventa src/demo/mockApi.ts).
   La compra de punta a punta contra el API local, a 375 y 768 px y solo con teclado, es e2e/compra.spec.ts
   (`npm run e2e:compra`, tarea O0-03). */
import { REF } from "./app/datos";
import { rutaFicha } from "./soporte/pantallas";
import { COMPRADORA, expect, test, tiendaLista } from "./soporte/tienda";

test("compra simulada con SINPE: de la ficha a «Mi pedido»", async ({ page }) => {
  await page.goto(rutaFicha(REF.comprable));
  await tiendaLista(page);

  // ficha: agregar y usar el aviso de 5 segundos con «Ir al carrito» (pedido del dueño del 21/10: se conserva)
  await page.locator(".vt-buybox").getByRole("button", { name: "Agregar al carrito" }).click();
  const aviso = page.locator(".sh-toast");
  await expect(aviso).toContainText("Agregado:");
  await aviso.getByRole("button", { name: "Ir al carrito" }).click();

  // carrito
  const cajon = page.getByRole("dialog", { name: "Carrito" });
  await expect(cajon).toHaveAttribute("data-open", "true");
  await expect(cajon.locator(".sh-line")).toHaveCount(1);
  await expect(cajon).toContainText("IVA incluido");
  await cajon.getByRole("button", { name: "Finalizar pedido" }).click();
  await expect(page).toHaveURL(/[?&]v=pagar/);

  const titulo = page.getByRole("heading", { level: 1 });
  const seguir = page.getByRole("button", { name: "Continuar" });

  // paso 1
  await expect(titulo).toContainText("Tus datos");
  await page.getByLabel("Nombre completo").fill(COMPRADORA.name);
  await page.getByLabel("Correo").fill(COMPRADORA.email);
  await page.getByLabel("Teléfono / WhatsApp").fill(COMPRADORA.phone);
  await seguir.click();

  // paso 2
  await expect(titulo).toContainText("Entrega");
  await page.getByLabel("Provincia").selectOption(COMPRADORA.province);
  await page.getByLabel("Cantón").selectOption(COMPRADORA.canton);
  await page.getByLabel("Distrito").fill(COMPRADORA.district);
  await page.getByLabel("Dirección exacta (otras señas)").fill(COMPRADORA.address);
  await page.locator("label.sh-opt", { hasText: COMPRADORA.rate }).click();
  await seguir.click();

  // paso 3
  await expect(titulo).toContainText("Pago");
  await page.locator("label.sh-opt", { hasText: "SINPE Móvil" }).click();
  await seguir.click();

  // paso 4: el total ya trae el envio y el IVA (1 999 del producto + 4 520 del envio a la GAM)
  await expect(titulo).toContainText("Confirmar");
  const confirmar = page.getByRole("button", { name: /^Confirmar pedido/ });
  await expect(confirmar).toContainText(/6\s519/);
  await page.locator("input[data-f=terms]").check();
  await confirmar.click();

  // «Mi pedido»: numero de prueba, como pagar y carrito vacio
  await expect(page).toHaveURL(/[?&]pedido=demo-/);
  await expect(titulo).toHaveText(/^DEMO-\d{4}$/);
  await expect(page.locator("main")).toContainText("SINPE Móvil");
  await expect(page.getByRole("button", { name: /^Carrito: 0 / })).toBeVisible();
});
