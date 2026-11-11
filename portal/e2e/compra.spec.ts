/* Compra REAL de punta a punta en local (plan de la tienda, O0-03): el portal de verdad contra el API de verdad, con
   base desechable, datos semilla ficticios y sin cobrar nada. Corre con `npm run e2e:compra`
   (playwright.compra.config.ts) a 375 y 768 px; `npm run e2e` no la incluye (esa suite usa el API simulado).

   Recorrido: catalogo -> ficha -> agregar -> carrito -> los cuatro pasos del checkout (tus datos, entrega, pago,
   confirmar) -> pedido creado -> «Mi pedido» con las instrucciones de SINPE. Al final se lee el pedido en el servidor
   y se revisa que aparto existencia. La misma compra se repite solo con teclado.

   Todo criterio del plan que diga «en local» se verifica aqui. Nunca apunta a produccion: la configuracion solo
   acepta direcciones de esta maquina. */
import {
  abrirFicha, API, COMPRADORA, elegirDeLista, elegirRadio, ENVIO, escribir, existencia, expect, monto, PRODUCTO, productosSembrados, revisarPedido,
  SEMILLA, SINPE, tabHasta, test, tiendaLista, TOTAL, usoDePuntero, vigilarPuntero,
} from "./soporte/compra";

test("la tienda local está sembrada: seis productos, cada uno en su estado, SINPE y pago simulado", async ({ servidor }) => {
  const prods = await productosSembrados(servidor);
  expect(Object.fromEntries(Object.entries(prods).map(([c, p]) => [c, p.availability.state]))).toEqual(SEMILLA);
  for (const p of Object.values(prods)) expect(Number(p.display_price), `${p.code}: los datos de prueba cuestan ₡2 000 o menos`).toBeLessThanOrEqual(2000);
  expect(Number(prods[PRODUCTO.codigo].display_price)).toBe(PRODUCTO.precio);

  const cfg = await (await servidor.get(`${API}/checkout-config`)).json();
  // tarjeta sin llave de ONVO: el API la ofrece solo como pago simulado (y solo fuera de produccion)
  expect(cfg.methods.map((m: { code: string; simulated: boolean }) => [m.code, m.simulated])).toEqual([["sinpe", false], ["tarjeta", true]]);
  expect(cfg.shipping_rates.map((r: { name: string; display_amount: number }) => [r.name, Number(r.display_amount)])).toContainEqual([ENVIO.nombre, ENVIO.monto]);
  expect((await (await servidor.get(`${API}/site`)).json()).analytics).toBeUndefined(); // sin medicion (EX-NOGA)
});

test("compra SINPE completa: del catálogo a «Mi pedido» con las instrucciones de pago", async ({ page, servidor }) => {
  const p = (await productosSembrados(servidor))[PRODUCTO.codigo];
  const libres = await existencia(servidor, p.id);

  // catalogo -> ficha
  await page.goto("/catalogo");
  await tiendaLista(page);
  await page.locator(".vt-card", { hasText: PRODUCTO.nombre }).getByRole("link").first().click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(PRODUCTO.nombre);
  const compra = page.locator(".vt-buybox");
  await expect(compra).toBeVisible();
  await expect(page.locator(".vt-pdp")).toContainText(monto(PRODUCTO.precio));

  // agregar y usar el aviso de 5 segundos con «Ir al carrito» (pedido del dueño del 21/10: se conserva)
  await compra.getByRole("button", { name: "Agregar al carrito" }).click();
  const aviso = page.locator(".sh-toast");
  await expect(aviso).toContainText("Agregado:");
  await aviso.getByRole("button", { name: "Ir al carrito" }).click();

  // carrito
  const cajon = page.getByRole("dialog", { name: "Carrito" });
  await expect(cajon).toHaveAttribute("data-open", "true");
  await expect(cajon.locator(".sh-line")).toHaveCount(1);
  await expect(cajon.locator(".sh-totrow--big")).toContainText(monto(PRODUCTO.precio));
  await expect(cajon).toContainText("IVA incluido");
  await cajon.getByRole("button", { name: "Finalizar pedido" }).click();
  await expect(page).toHaveURL(/[?&]v=pagar/);

  const titulo = page.getByRole("heading", { level: 1 });
  const seguir = page.getByRole("button", { name: "Continuar" });

  // paso 1 · tus datos
  await expect(titulo).toContainText("Tus datos");
  await page.getByLabel("Nombre completo").fill(COMPRADORA.nombre);
  await page.getByLabel("Correo").fill(COMPRADORA.correo);
  await page.getByLabel("Teléfono / WhatsApp").fill(COMPRADORA.telefono);
  await seguir.click();

  // paso 2 · entrega: Heredia es GAM, se elige la tarifa de la GAM
  await expect(titulo).toContainText("Entrega");
  await page.getByLabel("Provincia").selectOption(COMPRADORA.provincia);
  await page.getByLabel("Cantón").selectOption(COMPRADORA.canton);
  await page.getByLabel("Distrito").fill(COMPRADORA.distrito);
  await page.getByLabel("Dirección exacta (otras señas)").fill(COMPRADORA.direccion);
  const tarifa = page.locator("label.sh-opt", { hasText: ENVIO.nombre });
  await expect(tarifa).toContainText(monto(ENVIO.monto));
  await tarifa.click();
  await seguir.click();

  // paso 3 · pago: SINPE (la tarjeta se ofrece marcada como modo de prueba)
  await expect(titulo).toContainText("Pago");
  await expect(page.locator("label.sh-opt", { hasText: "Tarjeta" })).toContainText("Modo de prueba");
  await page.locator("label.sh-opt", { hasText: "SINPE Móvil" }).click();
  await seguir.click();

  // paso 4 · confirmar: el total ya trae el envio y el IVA
  await expect(titulo).toContainText("Confirmar");
  const confirmar = page.getByRole("button", { name: /^Confirmar pedido/ });
  await expect(confirmar).toContainText(monto(TOTAL));
  await page.locator("input[data-f=terms]").check();
  await confirmar.click();

  // pedido creado: «Mi pedido», el pedido en el servidor y la existencia
  const { numero } = await revisarPedido(page, servidor, { id: p.id, libres });

  // y se vuelve a encontrar por numero y correo, como quien abre «Mi pedido» otro dia
  await page.goto("/?v=mi-pedido");
  await tiendaLista(page);
  await page.getByLabel("Número de pedido").fill(numero);
  await page.getByLabel("Correo").fill(COMPRADORA.correo);
  await page.getByRole("button", { name: "Ver mi pedido" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(numero);
  await expect(page.locator(".ck-instr")).toContainText(SINPE.telefono);
});

test("la misma compra solo con teclado: ni ratón ni dedo", async ({ page, servidor }) => {
  const p = (await productosSembrados(servidor))[PRODUCTO.codigo];
  const libres = await existencia(servidor, p.id);
  await vigilarPuntero(page);

  // ficha: Tab hasta «Agregar al carrito», Enter; la linea que queda debajo (O2-07: «Agregado. En tu carrito: 1 ·
  // Ver carrito») lleva al carrito
  await abrirFicha(page, p);
  const compra = page.locator(".vt-buybox");
  await tabHasta(page, compra.getByRole("button", { name: "Agregar al carrito" }));
  await page.keyboard.press("Enter");
  await expect(compra.getByRole("status")).toContainText("Agregado. En tu carrito: 1");
  await tabHasta(page, compra.getByRole("button", { name: "Ver carrito" }));
  await page.keyboard.press("Enter");

  // carrito
  const cajon = page.getByRole("dialog", { name: "Carrito" });
  await expect(cajon).toHaveAttribute("data-open", "true");
  await expect(cajon.locator(".sh-line")).toHaveCount(1);
  await tabHasta(page, cajon.getByRole("button", { name: "Finalizar pedido" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/[?&]v=pagar/);

  const titulo = page.getByRole("heading", { level: 1 });
  const seguir = page.getByRole("button", { name: "Continuar" });

  // paso 1 · tus datos: se escribe campo por campo y Enter dentro del formulario lo envia
  await expect(titulo).toContainText("Tus datos");
  await escribir(page, page.getByLabel("Nombre completo"), COMPRADORA.nombre);
  await escribir(page, page.getByLabel("Correo"), COMPRADORA.correo);
  await escribir(page, page.getByLabel("Teléfono / WhatsApp"), COMPRADORA.telefono);
  await page.keyboard.press("Enter");

  // paso 2 · entrega: listas con el teclado, tarifa con las flechas
  await expect(titulo).toContainText("Entrega");
  await elegirDeLista(page, page.getByLabel("Provincia"), COMPRADORA.provincia);
  await elegirDeLista(page, page.getByLabel("Cantón"), COMPRADORA.canton);
  await escribir(page, page.getByLabel("Distrito"), COMPRADORA.distrito);
  await escribir(page, page.getByLabel("Dirección exacta (otras señas)"), COMPRADORA.direccion);
  const tarifas = page.locator(".sh-field", { has: page.locator("input[name=rate]") });
  await elegirRadio(page, tarifas, page.locator("label.sh-opt", { hasText: ENVIO.nombre }).locator("input[type=radio]"));
  await tabHasta(page, seguir);
  await page.keyboard.press("Enter");

  // paso 3 · pago
  await expect(titulo).toContainText("Pago");
  const pagos = page.locator("fieldset", { has: page.locator("input[name=pay]") });
  await elegirRadio(page, pagos, page.locator("label.sh-opt", { hasText: "SINPE Móvil" }).locator("input[type=radio]"));
  await tabHasta(page, seguir);
  await page.keyboard.press("Enter");

  // paso 4 · confirmar: la casilla de los terminos se marca con la barra espaciadora
  await expect(titulo).toContainText("Confirmar");
  const terminos = page.locator("input[data-f=terms]");
  await tabHasta(page, terminos);
  await page.keyboard.press("Space");
  await expect(terminos).toBeChecked();
  const confirmar = page.getByRole("button", { name: /^Confirmar pedido/ });
  await expect(confirmar).toContainText(monto(TOTAL));
  await tabHasta(page, confirmar);
  await page.keyboard.press("Enter");

  await revisarPedido(page, servidor, { id: p.id, libres });
  expect(await usoDePuntero(page), "la compra con teclado no puede usar ratón, dedo ni lápiz").toEqual([]);
});
