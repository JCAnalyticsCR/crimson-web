/* O1-02: «Consultar» pasa a venta asistida. La ficha «por confirmar» ofrece un único botón activo de WhatsApp con el
   precio de referencia y todo lo que hace falta para cotizar sin preguntar de vuelta; con accion = ninguna no hay
   botón. Corre en los cinco anchos (el criterio pide 320, 360, 375 y 768). */
import { REF } from "./app/datos";
import type { AjustesE2e } from "./app/api";
import { PANTALLAS, prod } from "./soporte/pantallas";
import { expect, medirAncho, test, tiendaLista } from "./soporte/tienda";

const abrir = (id: string) => PANTALLAS.find((p) => p.id === id)!.abrir;

test("[O1-02] ficha por confirmar: «Pedir por WhatsApp» activo con producto, cantidad, precio y referencia", async ({ page }) => {
  await abrir("ficha-por-confirmar")(page);
  const p = prod(REF.porConfirmar);
  await expect(page.locator(".vt-info__price .vt-avail")).toHaveText("Disponibilidad por confirmar");
  const zona = page.locator(".vt-buybox");
  const wa = zona.getByRole("link", { name: "Pedir por WhatsApp" });
  await expect(wa).toHaveCount(1);
  await expect(zona.locator("button:disabled")).toHaveCount(0);
  await expect(zona).toContainText("Se abre WhatsApp");
  await expect(zona).toContainText("Precio de referencia: te confirmamos precio final, existencia y fecha de entrega antes de que pagués.");
  await expect(zona.getByRole("link", { name: "Prefiero que me contacten" })).toHaveAttribute("href", `/asesoria?producto=${p.id}&cantidad=1`);
  // sube la cantidad y el mensaje la lleva
  await zona.getByRole("button", { name: "Agregar uno" }).click();
  const href = decodeURIComponent((await wa.getAttribute("href"))!).replace(/[\u00a0\u202f]/g, " ");
  expect(href).toMatch(/^https:\/\/wa\.me\/\d+\?text=/);
  expect(href).toContain(`quiero pedir ${p.name} (código ${p.code}), cantidad 2.`);
  expect(href).toMatch(/Precio que vi en la web: ₡[\d ]+ con IVA\./);
  expect(href).toContain(`· ref. web P${p.id}`);
  expect(href).toContain(`/p/`);
  // nada de los textos viejos
  await expect(page.locator("main")).not.toContainText("No lo podemos vender en línea");
  await expect(page.locator("main")).not.toContainText("Sin existencias");
  // la barra fija (celular y tableta) tambien ofrece el pedido, activo
  const ancho = page.viewportSize()!.width;
  if (ancho < 900) await expect(page.locator(".vt-stickybuy").getByRole("link", { name: "Pedir por WhatsApp" })).toBeVisible();
  const m = await medirAncho(page);
  expect(m.documento).toBeLessThanOrEqual(m.ventana);
});

test("[O1-02] con accion = ninguna no aparece ningún botón: solo la nota y «Prefiero que me contacten»", async ({ page }) => {
  await page.addInitScript((a: AjustesE2e) => { (window as Window & { __e2eAjustes?: AjustesE2e }).__e2eAjustes = a; }, { accion: "ninguna" });
  await abrir("ficha-por-confirmar")(page);
  const zona = page.locator(".vt-buybox");
  await expect(zona.getByRole("link", { name: "Prefiero que me contacten" })).toBeVisible();
  await expect(zona).toContainText("Lo traemos del distribuidor.");
  await expect(zona.locator(".sh-btn")).toHaveCount(0);
  await expect(page.locator(".vt-stickybuy .sh-btn")).toHaveCount(0);
  await expect(page.locator(".vt-pdp a[href*='wa.me'], .vt-stickybuy a[href*='wa.me']")).toHaveCount(0); // el boton flotante general de la tienda sigue
});

test("[O1-02] agotado: sin botón de compra y un enlace de texto «Consultar por WhatsApp»", async ({ page }) => {
  await abrir("ficha-agotada")(page);
  const zona = page.locator(".vt-buybox");
  await expect(zona.getByRole("link", { name: "Consultar por WhatsApp" })).toBeVisible();
  await expect(zona.getByRole("button", { name: /Agregar/ })).toHaveCount(0);
  await expect(page.locator(".vt-stickybuy")).not.toContainText("Sin existencias");
});

test("[O1-02] la tarjeta por confirmar dice «Consultar» y su nombre accesible nombra el producto", async ({ page }) => {
  const p = prod(REF.porConfirmar);
  await page.goto("/c/audio");
  await tiendaLista(page);
  const tarjeta = page.locator(".vt-card", { hasText: p.name });
  await expect(tarjeta.locator(".vt-avail")).toHaveText("Por confirmar");
  const pill = tarjeta.getByRole("link", { name: `Consultar ${p.name} por WhatsApp` });
  await expect(pill).toHaveText("Consultar");
});
