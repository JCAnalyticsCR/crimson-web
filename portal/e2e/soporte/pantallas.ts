/* Las pantallas de la tienda que cubren las pruebas genericas (scroll, capturas, axe). Cada una sabe abrirse sola y
   esperar a estar completa. Para cubrir una pantalla nueva basta agregarla aqui: entra a las tres pruebas. */
import { productPath } from "../../src/demo/catalog";
import { CATALOGO, PRODUCTOS, REF } from "../app/datos";
import { expect, sembrarCarrito, sembrarCheckout, tiendaLista, type Linea } from "./tienda";
import type { Page } from "@playwright/test";

const prod = (id: number) => PRODUCTOS.find((p) => p.id === id)!;
export const rutaFicha = (id: number) => productPath(prod(id));
/** linea de carrito con el precio que muestra la tienda (colones con IVA) */
export const linea = (id: number, cantidad: number): Linea => {
  const p = prod(id);
  return { id, nombre: p.name, precio: Math.round(p.crc * (1 + p.rate / 100) * 100) / 100, cantidad, foto: p.images[0] ?? null };
};
export const CARRITO: Linea[] = [linea(REF.comprable, 2), linea(240, 1)];
export const CATEGORIA = { ruta: "/c/seguridad-electronica", nombre: "Seguridad electrónica", productos: PRODUCTOS.filter((p) => [11, 12, 13].includes(p.cat ?? 0)).length };

export type Pantalla = {
  id: string; nombre: string;
  /** entra a la captura base y al informe de axe (las siete del plan); las demas solo a la prueba de scroll */
  base: boolean;
  abrir: (page: Page) => Promise<void>;
};

async function ficha(page: Page, id: number) {
  await page.goto(rutaFicha(id));
  await tiendaLista(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(prod(id).name);
  await expect(page.locator(".vt-buybox")).toBeVisible(); // la ficha completa ya llego (/item), no solo la tarjeta del snapshot
}

async function checkout(page: Page, paso: 0 | 1 | 2 | 3, titulo: string) {
  await sembrarCarrito(page, CARRITO);
  await sembrarCheckout(page, paso);
  await page.goto("/?v=pagar");
  await tiendaLista(page);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(titulo);
  await expect(page.locator(".ck-mini summary b")).not.toHaveText("…"); // ya hay cotizacion: el total dejo de ser «…»
}

async function pedido(page: Page, token: string) {
  await page.goto(`/?pedido=${token}`);
  await tiendaLista(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("ORD-000128");
}

export const PANTALLAS: Pantalla[] = [
  { id: "portada", nombre: "portada", base: true, abrir: async (page) => {
    await page.goto("/");
    await tiendaLista(page);
    await expect(page.locator(".vt-kit:not(.vt-kit--skel)").first()).toBeVisible(); // /showcase ya respondio (trae los kits)
    await expect(page.locator(".vt-kit--skel")).toHaveCount(0);
  } },
  { id: "categoria", nombre: "categoría", base: true, abrir: async (page) => {
    await page.goto(CATEGORIA.ruta);
    await tiendaLista(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(CATEGORIA.nombre);
    await expect(page.locator(".vt-grid .vt-card:not(.vt-card--skel)")).toHaveCount(CATEGORIA.productos);
  } },
  { id: "ficha-comprable", nombre: "ficha comprable (en bodega)", base: true, abrir: (page) => ficha(page, REF.comprable) },
  { id: "ficha-por-confirmar", nombre: "ficha por confirmar («consultar»)", base: true, abrir: (page) => ficha(page, REF.porConfirmar) },
  { id: "carrito", nombre: "carrito abierto", base: true, abrir: async (page) => {
    await sembrarCarrito(page, CARRITO);
    await page.goto(CATEGORIA.ruta);
    await tiendaLista(page);
    await page.getByRole("button", { name: /^Carrito:/ }).click();
    const cajon = page.locator(".sh-drawer");
    await expect(cajon).toHaveAttribute("data-open", "true");
    await expect(cajon.getByRole("button", { name: "Finalizar pedido" })).toBeVisible();
    // quieto: termino de deslizarse
    await page.waitForFunction(() => document.querySelector(".sh-drawer")!.getAnimations().every((a) => a.playState !== "running"));
  } },
  { id: "checkout", nombre: "checkout · paso 1, tus datos", base: true, abrir: (page) => checkout(page, 0, "Tus datos") },
  { id: "checkout-entrega", nombre: "checkout · paso 2, entrega", base: false, abrir: (page) => checkout(page, 1, "Entrega") },
  { id: "checkout-pago", nombre: "checkout · paso 3, pago", base: false, abrir: (page) => checkout(page, 2, "Pago") },
  { id: "checkout-confirmar", nombre: "checkout · paso 4, confirmar", base: true, abrir: (page) => checkout(page, 3, "Confirmar") },
  { id: "mi-pedido", nombre: "«Mi pedido» recién hecho (cómo pagar y comprobante)", base: true, abrir: (page) => pedido(page, "ejemplo-nuevo") },
  { id: "mi-pedido-enviado", nombre: "«Mi pedido» enviado (guía e historial)", base: false, abrir: (page) => pedido(page, "ejemplo-enviado") },
  { id: "mi-pedido-buscar", nombre: "«Mi pedido» · buscar por número y correo", base: false, abrir: async (page) => {
    await page.goto("/?v=mi-pedido");
    await tiendaLista(page);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Mi pedido");
  } },
];

export const TERMINOS = CATALOGO.legal.terms ?? "";
