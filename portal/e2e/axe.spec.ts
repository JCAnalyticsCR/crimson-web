/* Accesibilidad con axe en MODO INFORME (O0-02): revisa las pantallas `base` a 375 y 1280 px contra WCAG 2.1 A y AA
   y deja los hallazgos en e2e/informes/axe.md (lo arma soporte/informe.ts). No falla por hallazgos: hoy hay varios y
   el plan los resuelve en la Ola 2. Pasa a bloqueante en O2-02 (ahi se cambia el `expect` del final).
   Solo falla si axe no pudo revisar la pagina. */
import { AxeBuilder } from "@axe-core/playwright";
import { PANTALLAS } from "./soporte/pantallas";
import { cargarTodo, expect, test } from "./soporte/tienda";

for (const p of PANTALLAS.filter((x) => x.base)) {
  test(`axe (informe) · ${p.nombre}`, async ({ page }, info) => {
    await p.abrir(page);
    await cargarTodo(page);
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const hallazgos = r.violations.map((v) => ({
      id: v.id, impact: v.impact ?? null, help: v.help, helpUrl: v.helpUrl, nodos: v.nodes.length,
      ejemplos: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
    }));
    await info.attach("axe", { body: JSON.stringify({ pantalla: p.id, hallazgos }), contentType: "application/json" });
    for (const h of hallazgos) info.annotations.push({ type: "axe", description: `${h.impact}: ${h.id} · ${h.nodos} elemento(s)` });
    expect(r.passes.length, "axe no llegó a revisar la página").toBeGreaterThan(0);
  });
}
