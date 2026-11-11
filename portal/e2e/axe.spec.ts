/* Accesibilidad con axe (O0-02): revisa las pantallas `base` a 375 y 1280 px contra WCAG 2.1 A y AA y deja los
   hallazgos en e2e/informes/axe.md (lo arma soporte/informe.ts).
   Desde O2-02 el CONTRASTE es bloqueante (`BLOQUEAN`): un par de colores por debajo de AA pone la prueba en rojo. Las
   demas reglas siguen en modo informe. El checkout y «Mi pedido» entran al bloqueo con O2-11 (`AUN_NO`). */
import { AxeBuilder } from "@axe-core/playwright";
import { PANTALLAS } from "./soporte/pantallas";
import { cargarTodo, expect, test } from "./soporte/tienda";

/** reglas que ya no se toleran */
const BLOQUEAN = ["color-contrast", "link-name", "button-name"]; // O2-05: ningun enlace o boton sin nombre
/** pantallas que se migran despues (O2-11): siguen en modo informe */
const AUN_NO = new Set<string>([]); // O2-11: el checkout y «Mi pedido» ya entran al bloqueo

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
    if (!AUN_NO.has(p.id)) {
      const graves = hallazgos.filter((h) => BLOQUEAN.includes(h.id));
      expect(graves, `axe: ${graves.map((h) => `${h.id} en ${h.ejemplos.join(", ")}`).join(" · ")}`).toEqual([]);
    }
  });
}
