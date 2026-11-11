/* O1-10a: el checkout ayuda en vez de trabar. Errores bajo cada campo (aria-invalid y aria-describedby) y el foco en
   el primero al enviar; ayuda por WhatsApp con el carrito; tarjeta de credito o debito; cupon plegado; todo en vos.
   Corre en los cinco anchos. Sin dinero de por medio: el API es el simulado (e2e/app/api.ts). */
import { expect, sembrarCarrito, sembrarCheckout, test, tiendaLista } from "./soporte/tienda";

const CARRITO = [{ id: 68, nombre: "Producto de prueba", precio: 1500, cantidad: 2 }];

async function abrirPaso(page: import("@playwright/test").Page, paso: 0 | 1 | 2 | 3) {
  await sembrarCarrito(page, CARRITO);
  await sembrarCheckout(page, paso);
  await page.goto("/?v=pagar");
  await tiendaLista(page);
  await expect(page.locator(".ck-mini summary b")).not.toHaveText("…");
}

test("[O1-10a] paso 1 vacío: el foco va al primer campo y cada error queda ligado a su campo", async ({ page }) => {
  await abrirPaso(page, 0);
  await page.getByRole("button", { name: "Continuar" }).click();
  const nombre = page.getByLabel("Nombre completo");
  await expect(nombre).toBeFocused();
  for (const [campo, texto] of [["Nombre completo", "Escribí tu nombre completo"], ["Correo", "Escribí un correo válido"], ["Teléfono / WhatsApp", "Escribí un teléfono de 8 dígitos"]] as const) {
    const input = page.getByLabel(campo, { exact: true });
    await expect(input).toHaveAttribute("aria-invalid", "true");
    const ids = ((await input.getAttribute("aria-describedby")) || "").split(/\s+/).filter(Boolean);
    const textos = await Promise.all(ids.map((id) => page.locator(`[id="${id}"]`).innerText()));
    expect(textos.join(" | "), `el error de «${campo}» está ligado al campo`).toContain(texto);
  }
  // el resumen de arriba sigue
  await expect(page.locator(".ck-errs")).toContainText("Escribí tu nombre completo");
  // al corregir el nombre y reenviar, el foco salta al siguiente con error
  await nombre.fill("Ana Prueba");
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByLabel("Correo", { exact: true })).toBeFocused();
  await expect(nombre).not.toHaveAttribute("aria-invalid", "true");
});

test("[O1-10a] ayuda por WhatsApp bajo el botón, con las líneas del carrito y el total", async ({ page }) => {
  await abrirPaso(page, 0);
  const ayuda = page.getByRole("link", { name: "¿Dudas? Escribinos por WhatsApp" });
  await expect(ayuda).toBeVisible();
  await expect(page.locator(".ck-help")).toContainText("Se abre WhatsApp");
  const texto = decodeURIComponent((await ayuda.getAttribute("href"))!).replace(/[  ]/g, " ");
  expect(texto).toContain("• 2 × Producto de prueba");
  expect(texto).toMatch(/Total: ₡[\d ]+/);
});

test("[O1-10a] pago: «Tarjeta de crédito o débito» y el cupón plegado tras «¿Tenés un cupón?»", async ({ page }) => {
  await abrirPaso(page, 2);
  await expect(page.locator("label.sh-opt", { hasText: "Tarjeta de crédito o débito" })).toHaveCount(1);
  const abrir = page.getByRole("button", { name: "¿Tenés un cupón?" });
  await expect(abrir).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByLabel("Código de cupón")).toHaveCount(0);
  await abrir.click();
  await expect(abrir).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByLabel("Código de cupón")).toBeVisible();
  const fs = await page.getByLabel("Código de cupón").evaluate((el) => getComputedStyle(el).fontSize);
  expect(Number.parseFloat(fs)).toBeGreaterThanOrEqual(16); // iOS no hace zoom
});

test("[O1-10a] confirmar: «Factura electrónica» junto al botón y ningún «usted» en el checkout", async ({ page }) => {
  await abrirPaso(page, 3);
  await expect(page.locator(".ck-trust")).toHaveText("Factura electrónica");
  const todo = await page.locator(".ck").innerText();
  expect(todo).not.toMatch(/\busted\b|\ble ayudó\b|\belíjalo\b|\blo atendió\b/i);
  for (const p of [2, 1, 0] as const) {
    await page.getByRole("button", { name: new RegExp(`^Paso ${p + 1}:`) }).click();
    expect(await page.locator(".ck").innerText()).not.toMatch(/\busted\b|\ble ayudó\b|\belíjalo\b|\blo atendió\b/i);
  }
});
