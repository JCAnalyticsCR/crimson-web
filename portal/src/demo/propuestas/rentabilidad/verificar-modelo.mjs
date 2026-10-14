/* Pruebas del modelo de «¿Es rentable?» con Node, sin dependencias ni runner de pruebas.
   Node 22.18+ / 23.6+ quita los tipos de TypeScript solo (modelo.ts no importa nada y usa solo sintaxis borrable).
     cd portal && node src/demo/propuestas/rentabilidad/verificar-modelo.mjs
   Sale con código 1 si algún vector de la sección 6.7 no coincide (tolerancia ±₡1). No entra al build: Vite solo
   empaqueta lo que importa index.html, y tsc no revisa .mjs. */
import { calcular, entradasEjemplo, rampa, verificarModelo } from "./modelo.ts";

const r = verificarModelo();
const fallas = r.filas.filter((f) => !f.pasa);
for (const f of r.filas) {
  const marca = f.pasa ? "pasa   " : "NO PASA";
  console.log(`${marca}  ${f.escenario.padEnd(11)}  ${f.nombre.padEnd(34)}  esperado ${String(f.esperado).padStart(12)}  obtenido ${String(f.obtenido).padStart(14)}`);
}

// Propiedades que no dependen de los ejemplos (si fallan, el modelo está mal aunque los vectores pasen).
const props = [];
const check = (nombre, ok) => props.push({ nombre, ok });
check("rampa: antes del arranque vale 0", rampa(1, 2, 6) === 0);
check("rampa: llega a 1 en R meses y no pasa de 1", rampa(7, 2, 6) === 1 && rampa(30, 2, 6) === 1);
check("rampa: R = 0 no divide entre cero", rampa(3, 1, 0) === 1);
{
  const sin = calcular(entradasEjemplo("medio"));
  const conCop = calcular(entradasEjemplo("medio", { base: { Cop: 10_000 } }));
  check("Cop resta lo mismo cada mes", sin.meses.every((m, i) => Math.abs(m.U - conCop.meses[i].U - 10_000) < 1e-6));
  const e = entradasEjemplo("medio");
  e.incluida = [true, false, false, false, true, true];
  const solo0 = calcular(e);
  check("apagar etapas 1-3 deja solo S1 (y S7)", solo0.meses.every((m) => m.S2 === 0 && m.S3 === 0 && m.S4 === 0 && m.S5 === 0));
  check("los aportes no dependen de los interruptores", solo0.aportes.every((a, i) => Math.abs(a - sin.aportes[i]) < 1e-6));
  check("la suma de aportes = acumulado 24 con todo incluido", Math.abs(sin.aportes.reduce((a, b) => a + b, 0) - sin.meses[23].acum) < 1e-6);
  const e2 = entradasEjemplo("medio", { inversion: [1_000_000, null, 2_000_000, null, null, null] });
  e2.incluida = [true, true, false, true, true, true];
  check("la inversión de una etapa apagada no cuenta", calcular(e2).inversion === 1_000_000);
  check("inversión 0 = sin inversión", calcular(entradasEjemplo("medio", { inversion: [0, null, null, null, null, null] })).equilibrio.tipo === "sin-inversion");
  check("los planes nunca son negativos", sin.meses.every((m) => m.A >= 0 && m.MRR >= 0));
  const e3 = entradasEjemplo("medio", { base: { Fm: 30_000, Cop: 10_000 } });
  e3.inicio = [3, 3, 4, 6, 9, 12];
  const tarde = calcular(e3);
  check("Fygaro y el costo de operar corren desde que arranca la Etapa 0", tarde.meses[1].S7 === 0 && tarde.meses[1].costos === 0 && tarde.meses[2].S7 === 30_000 && tarde.meses[2].costos === 10_000);
  const maxN = Math.max(...sin.meses.map((m) => m.N));
  check("los compradores con cuenta (B) no pasan de 12 meses de pedidos", sin.meses.every((m) => m.B <= 12 * maxN + 1e-6));
}
for (const p of props) console.log(`${p.ok ? "pasa   " : "NO PASA"}  propiedad   ${p.nombre}`);

const malas = props.filter((p) => !p.ok).length;
console.log(`\n${r.pasan}/${r.total} vectores y ${props.length - malas}/${props.length} propiedades pasan.`);
if (fallas.length || malas) process.exit(1);
