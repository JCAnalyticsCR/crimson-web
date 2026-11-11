/* EX-OPT: en el checkout, la casilla de ofertas es opcional, va aparte de los términos y arranca desmarcada. */
import { CARRITO } from "./soporte/pantallas";
import { expect, sembrarCarrito, sembrarCheckout, test, tiendaLista } from "./soporte/tienda";

test("[EX-OPT] la casilla de ofertas está desmarcada, es opcional y dice cómo darse de baja", async ({ page }, info) => {
  test.skip(![375, 1280].includes(info.project.use.viewport!.width), "no depende del ancho");
  await sembrarCarrito(page, CARRITO);
  await sembrarCheckout(page, 3);
  await page.goto("/?v=pagar");
  await tiendaLista(page);
  const casilla = page.getByRole("checkbox", { name: /Quiero recibir ofertas y novedades de .+ por correo o WhatsApp\. Podés darte de baja cuando quieras\./ });
  await expect(casilla).toBeVisible();
  await expect(casilla).not.toBeChecked();
  // se puede confirmar sin marcarla: solo los términos son obligatorios
  await page.locator("input[data-f=terms]").check();
  let enviado: Record<string, unknown> | null = null;
  page.on("console", () => undefined);
  await page.exposeFunction("__vistoCheckout", (b: string) => { enviado = JSON.parse(b); });
  await page.evaluate(() => {
    const f = window.fetch;
    window.fetch = (input, init) => {
      if (String(input).endsWith("/checkout") && typeof init?.body === "string") (window as unknown as { __vistoCheckout: (b: string) => void }).__vistoCheckout(init.body);
      return f(input, init);
    };
  });
  await page.getByRole("button", { name: /^Confirmar pedido/ }).click();
  await expect.poll(() => enviado).not.toBeNull();
  expect(enviado!.marketing_opt_in).toBe(false);
});
