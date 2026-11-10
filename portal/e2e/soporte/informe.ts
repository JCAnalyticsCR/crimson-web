/* Informe propio de `npm run e2e` (ademas del de Playwright). Al terminar deja en e2e/informes/ (fuera de git):
     axe.md / axe.json  — hallazgos de accesibilidad por regla y pantalla (axe corre en MODO INFORME: no bloquea)
     pendientes.md      — pruebas marcadas test.fixme, cada una con la tarea del plan que la resuelve
   y escribe un resumen corto en la consola, que es lo que se lee en el registro del CI. */
import { mkdirSync, writeFileSync } from "node:fs";
import type { FullResult, Reporter, TestCase, TestResult } from "@playwright/test/reporter";

type Hallazgo = { id: string; impact: string | null; help: string; helpUrl: string; nodos: number; ejemplos: string[] };
type Corrida = { pantalla: string; ancho: string; hallazgos: Hallazgo[] };

const DIR = "e2e/informes";
const ORDEN = ["critical", "serious", "moderate", "minor"];
const TAREA = /^\[([^\]]+)\]\s*/;

export default class Informe implements Reporter {
  private axe: Corrida[] = [];
  private pendientes = new Map<string, { tarea: string; titulo: string; archivo: string; anchos: Set<string> }>();

  onTestEnd(test: TestCase, result: TestResult) {
    const ancho = test.parent.project()?.name ?? "";
    for (const a of result.attachments) {
      if (a.name !== "axe" || !a.body) continue;
      try { this.axe.push({ ...(JSON.parse(a.body.toString("utf8")) as Omit<Corrida, "ancho">), ancho }); } catch { /* adjunto ilegible: se ignora */ }
    }
    const fixme = test.annotations.find((n) => n.type === "fixme");
    if (fixme) {
      // la tarea va entre corchetes: en el motivo de test.fixme(condicion, motivo) o, si no hay motivo, en el nombre
      const texto = fixme.description || test.title;
      const m = TAREA.exec(texto);
      const archivo = test.location.file.replace(/\\/g, "/").split("/e2e/").pop() ?? "";
      const key = `${archivo}::${texto}`;
      const p = this.pendientes.get(key) ?? { tarea: m ? m[1] : "sin tarea", titulo: texto.replace(TAREA, ""), archivo, anchos: new Set<string>() };
      p.anchos.add(ancho);
      this.pendientes.set(key, p);
    }
  }

  onEnd(_result: FullResult) {
    mkdirSync(DIR, { recursive: true });
    const lineas: string[] = [];

    // ---------- accesibilidad ----------
    if (this.axe.length) {
      const porRegla = new Map<string, { h: Hallazgo; donde: Map<string, number>; ejemplos: Set<string> }>();
      for (const c of this.axe) for (const h of c.hallazgos) {
        const r = porRegla.get(h.id) ?? { h, donde: new Map<string, number>(), ejemplos: new Set<string>() };
        r.donde.set(`${c.pantalla} (${c.ancho})`, h.nodos);
        for (const e of h.ejemplos) r.ejemplos.add(e);
        porRegla.set(h.id, r);
      }
      const reglas = [...porRegla.values()].sort((a, b) => ORDEN.indexOf(a.h.impact ?? "minor") - ORDEN.indexOf(b.h.impact ?? "minor") || a.h.id.localeCompare(b.h.id));
      const md = [
        "# Accesibilidad (axe) · modo informe", "",
        `Pantallas revisadas: ${this.axe.length}. Reglas con hallazgos: ${reglas.length}. WCAG 2.1 A y AA. Este informe no bloquea; pasa a bloqueante en O2-02.`, "",
        "| Regla | Impacto | Qué pide | Dónde (elementos) |", "|---|---|---|---|",
        ...reglas.map((r) => `| [${r.h.id}](${r.h.helpUrl}) | ${r.h.impact ?? "—"} | ${r.h.help.replace(/\|/g, "\\|")} | ${[...r.donde.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, n]) => `${k}: ${n}`).join("<br>")} |`),
        "", "## Elementos señalados (selector; hasta 25 por regla)", "",
        ...reglas.flatMap((r) => [`### ${r.h.id}`, "", ...[...r.ejemplos].sort().slice(0, 25).map((e) => `- \`${e}\``), ""]),
      ].join("\n");
      writeFileSync(`${DIR}/axe.md`, md);
      writeFileSync(`${DIR}/axe.json`, JSON.stringify(this.axe, null, 2));
      lineas.push(`axe (modo informe): ${reglas.length} regla(s) con hallazgos en ${this.axe.length} pantalla(s) -> ${DIR}/axe.md`);
      for (const r of reglas) lineas.push(`  ${String(r.h.impact ?? "—").padEnd(9)} ${r.h.id.padEnd(28)} ${r.donde.size} pantalla(s)`);
    }

    // ---------- pendientes (test.fixme) ----------
    if (this.pendientes.size) {
      const ps = [...this.pendientes.values()].sort((a, b) => a.tarea.localeCompare(b.tarea) || a.titulo.localeCompare(b.titulo));
      const md = [
        "# Fallos conocidos (test.fixme) y la tarea que los resuelve", "",
        "| Tarea | Qué falla hoy | Anchos | Archivo |", "|---|---|---|---|",
        ...ps.map((p) => `| ${p.tarea} | ${p.titulo.replace(/\|/g, "\\|")} | ${[...p.anchos].sort().join(", ")} | ${p.archivo} |`), "",
      ].join("\n");
      writeFileSync(`${DIR}/pendientes.md`, md);
      lineas.push(`pendientes (test.fixme): ${ps.length} -> ${DIR}/pendientes.md`);
      for (const p of ps) lineas.push(`  ${p.tarea.padEnd(9)} ${p.titulo}`);
    }
    if (lineas.length) console.log(`\n${lineas.join("\n")}\n`);
  }
}
