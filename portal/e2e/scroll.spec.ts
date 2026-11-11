/* Criterio de salida comun del plan de la tienda: ninguna pantalla se desliza de lado a 320, 360, 375, 768 y 1280 px.
   Corre en los cinco anchos (un proyecto por ancho en playwright.config.ts) y en todas las pantallas de
   soporte/pantallas.ts. Ademas del scroll (ancho del documento contra el de la ventana) revisa que nada visible quede
   cortado por el borde: un contenedor con overflow oculto esconde el scroll, pero el cliente ve el boton a medias.

   CONOCIDOS: lo que hoy falla queda como test.fixme con la tarea del plan que lo resuelve. Quien haga esa tarea borra
   la linea de CONOCIDOS y la prueba pasa a exigirse. */
import { PANTALLAS } from "./soporte/pantallas";
import { cargarTodo, expect, medirAncho, test } from "./soporte/tienda";

/** pantalla -> anchos en que falla hoy, tarea dueña y que se ve */
const CONOCIDOS: Record<string, { anchos: number[]; tarea: string; nota: string }> = {
  "ficha-comprable": { anchos: [320], tarea: "O1-03", nota: "ficha comprable a 320 px: cantidad + «Agregar al carrito» no caben y la columna queda cortada a la derecha" },
  "checkout-pago": { anchos: [320], tarea: "O1-10a", nota: "checkout, paso de pago a 320 px: el botón «Aplicar» del cupón queda cortado" },
  "checkout-confirmar": { anchos: [320, 360, 375], tarea: "O1-10a", nota: "checkout, paso de confirmar: «Atrás» + «Confirmar pedido · total» no caben y el botón queda cortado" },
};

for (const p of PANTALLAS) {
  const c = CONOCIDOS[p.id];
  const cuerpo = async ({ page }: { page: import("@playwright/test").Page }) => {
    await p.abrir(page);
    await cargarTodo(page);
    const m = await medirAncho(page);
    expect(m.documento, `scroll horizontal: el documento mide ${m.documento} px en una ventana de ${m.ventana} px`).toBeLessThanOrEqual(m.ventana);
    expect(m.fuera, "elementos cortados por el borde de la ventana").toEqual([]);
  };
  test(`sin scroll horizontal ni contenido cortado · ${p.nombre}`, async ({ page }, info) => {
    const ancho = info.project.use.viewport!.width;
    test.fixme(!!c && c.anchos.includes(ancho), c ? `[${c.tarea}] ${c.nota}` : "");
    await cuerpo({ page });
  });
}
