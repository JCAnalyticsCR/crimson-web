/* Capturas base de la tienda ANTES de tocar estilos (O0-02): pagina completa de las pantallas marcadas `base` en
   soporte/pantallas.ts, a 375, 768 y 1280 px. Viven en e2e/capturas-base/<sistema>/ y se comparan en cada corrida.

   - Un cambio de estilos a proposito: `npm run e2e:base`, mirar el diff de las imagenes y confirmarlas en el mismo
     commit que el cambio.
   - Las capturas son por sistema (las letras no se dibujan igual en Windows que en Linux). Si no hay carpeta para el
     sistema donde corre, la prueba se salta con aviso en vez de fallar. Ver e2e/README.md.
   - Lo fijo a la ventana (barra de compra del celular, botones del checkout) sale donde cae en la primera pantalla. */
import { existsSync } from "node:fs";
import { PANTALLAS } from "./soporte/pantallas";
import { cargarTodo, expect, test } from "./soporte/tienda";

for (const p of PANTALLAS.filter((x) => x.base)) {
  test(`captura base · ${p.nombre}`, async ({ page }, info) => {
    const archivo = info.snapshotPath(`${p.id}.png`);
    const carpeta = archivo.slice(0, Math.max(archivo.lastIndexOf("/"), archivo.lastIndexOf("\\")));
    const generando = info.config.updateSnapshots === "all" || info.config.updateSnapshots === "changed";
    test.skip(!generando && !existsSync(carpeta), `sin capturas base para ${process.platform}: generarlas con «npm run e2e:base» (e2e/README.md)`);
    await p.abrir(page);
    await cargarTodo(page);
    await expect(page).toHaveScreenshot(`${p.id}.png`, { fullPage: true });
  });
}
