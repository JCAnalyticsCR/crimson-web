/* Ficha en orden de decision (O2-07). */
import { PANTALLAS, prod, rutaFicha } from "./soporte/pantallas";
import { expect, test, tiendaLista } from "./soporte/tienda";

/** una camara en bodega con nombre corto (el criterio pide 60 caracteres o menos) */
const CORTA = 70;

const abrir = (id: string) => PANTALLAS.find((p) => p.id === id)!.abrir;

test("[O2-07] a 375x812 precio, disponibilidad y botón caben en pantalla y media", async ({ page }) => {
  test.skip(page.viewportSize()!.width !== 375, "criterio a 375 px");
  expect(prod(CORTA).name.length).toBeLessThanOrEqual(60);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(rutaFicha(CORTA));
  await tiendaLista(page);
  await expect(page.locator(".vt-buy__add")).toBeVisible();
  for (const sel of [".vt-info__price .vt-price", ".vt-info__price .vt-avail", ".vt-buy__add"]) {
    const box = (await page.locator(sel).boundingBox())!;
    expect(box.y + box.height, sel).toBeLessThanOrEqual(812 * 1.5);
  }
  await expect(page.locator(".vt-gallery")).not.toContainText("CAM");
});

test("[O2-07] al agregar: el botón sigue igual, la línea anuncia el carrito, el foco no se mueve y un doble toque no abre el carrito", async ({ page }) => {
  await abrir("ficha-comprable")(page);
  const boton = page.locator(".vt-buybox").getByRole("button", { name: "Agregar al carrito" });
  await boton.dblclick();
  await expect(boton).toBeFocused();
  await expect(boton).toHaveText(/Agregar al carrito/);
  await expect(page.locator(".vt-buybox").getByRole("status")).toContainText("Agregado. En tu carrito: 2 · Ver carrito");
  await expect(page.locator(".sh-toast")).toContainText("Ir al carrito"); // el aviso de 5 s del 21/10 sigue
  await expect(page.locator(".sh-drawer")).not.toHaveAttribute("data-open", "true");
});

test("[O2-07] plegados y galería se manejan con el teclado", async ({ page }) => {
  await abrir("ficha-comprable")(page);
  const desc = page.getByRole("button", { name: "Descripción" });
  await expect(desc).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#vt-desc")).toHaveCount(1); // el contenido esta en la pagina aunque este plegado
  await desc.focus();
  await page.keyboard.press("Enter");
  await expect(desc).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#vt-desc")).toBeVisible();
  const sig = page.getByRole("button", { name: "Foto siguiente" });
  await sig.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".vt-thumb").nth(1)).toHaveAttribute("aria-current", "true");
  await expect(page.locator(".vt-specs__src")).toContainText("Datos según el distribuidor");
});

test("[O2-07] la barra fija no aparece mientras el botón principal está a la vista", async ({ page }) => {
  test.skip(page.viewportSize()!.width >= 900, "barra de celular y tableta");
  await abrir("ficha-comprable")(page);
  const barra = page.locator(".vt-stickybuy");
  await expect(barra).toBeHidden();
  await page.locator(".sh-foot").scrollIntoViewIfNeeded();
  await expect(barra).toBeVisible();
});

test("[O2-07] sin el candado legal no aparece «garantía» junto al botón", async ({ page }) => {
  await abrir("ficha-comprable")(page);
  await expect(page.locator(".vt-buybox")).not.toContainText(/garant/i);
  await expect(page.locator(".vt-info")).not.toContainText(/garant/i);
});
