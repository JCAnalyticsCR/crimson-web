#!/usr/bin/env node
/* Peso inicial de la tienda y frontera con el panel (O2-01d). Corre despues de `vite build` (lo usa el CI):

     node scripts/peso-tienda.mjs              comprueba
     node scripts/peso-tienda.mjs --registrar  guarda el peso actual como linea base (scripts/peso-tienda.base.json)

   Lee .peso-tienda/trozos.json (lo escribe el plugin mapaDeTrozos de vite.tienda.ts) y arma lo que descarga quien abre
   la tienda: la entrada (index.html -> src/main.tsx) con sus importaciones estaticas, mas la vitrina
   (src/modules/store/PublicStore.tsx, que StoreHost carga de inmediato) con las suyas, y sus hojas de estilo.

   Falla si:
   1. ese conjunto trae un modulo del panel (src/app/, otros modulos que no sean la tienda publica, lib/api, la demo);
   2. su peso comprimido (gzip) supera en mas de 5 % la linea base;
   3. algun archivo del build menciona Google (analitica, Tag Manager o fuentes): la tienda no usa nada de terceros;
   4. index.html no enlaza la hoja de la vitrina en el bloque del cuerpo por SSI (O2-01b). */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const RAIZ = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MAPA = resolve(RAIZ, ".peso-tienda/trozos.json");
const BASE = resolve(RAIZ, "scripts/peso-tienda.base.json");
const VITRINA = "src/modules/store/PublicStore.tsx";
const MARGEN = 0.05;

/** Modulos de src/ que la tienda nunca debe descargar al entrar. */
const DEL_PANEL = [
  /^src\/app\//,
  /^src\/demo\//,
  /^src\/lib\/api\.ts$/,
  /^src\/modules\/(?!store\/)/,
  // dentro de store/: las pantallas del panel (Mi Tienda, pedidos, vitrina administrable, tienda de pruebas)
  /^src\/modules\/store\/(Store|Orders|StoreShowcaseAdmin|TestShop|CheckoutSettings)\.tsx$/,
];
/** Nada de Google ni de medicion de terceros en lo que se publica. */
const TERCEROS = /googletagmanager|google-analytics|googleapis|gstatic\.com|gtag\(|\bdataLayer\b|doubleclick\.net|googlesyndication/i;

if (!existsSync(MAPA)) {
  console.error(`Falta ${MAPA}: corra primero \`npx vite build\` (el plugin mapaDeTrozos de vite.tienda.ts lo escribe).`);
  process.exit(2);
}
const { outDir, trozos } = JSON.parse(readFileSync(MAPA, "utf8"));
const DIST = resolve(RAIZ, outDir);
const porArchivo = new Map(trozos.map((t) => [t.file, t]));
const entrada = trozos.find((t) => t.entry);
const vitrina = trozos.find((t) => t.facade === VITRINA);
if (!entrada || !vitrina) {
  console.error(`No se encontro ${entrada ? "el trozo de la vitrina" : "la entrada"} en ${MAPA}.`);
  process.exit(2);
}

/** el trozo y todo lo que importa de forma estatica (lo que el navegador baja si o si) */
function cierre(t, vistos = new Set()) {
  if (vistos.has(t.file)) return vistos;
  vistos.add(t.file);
  for (const f of t.imports) if (porArchivo.has(f)) cierre(porArchivo.get(f), vistos);
  return vistos;
}
const tienda = [...cierre(vitrina, cierre(entrada))].map((f) => porArchivo.get(f));

// 1. frontera con el panel
const intrusos = tienda.flatMap((t) => t.modules.filter((m) => DEL_PANEL.some((r) => r.test(m))).map((m) => `${m}  (en ${t.file})`));

// 2. peso comprimido: JavaScript + CSS de esos trozos (gzip nivel 9, como el informe de Vite)
const css = [...new Set(tienda.flatMap((t) => t.css))];
const archivos = [...tienda.map((t) => t.file), ...css];
const filas = archivos.map((f) => {
  const buf = readFileSync(resolve(DIST, f));
  return { f, crudo: buf.length, gz: gzipSync(buf, { level: 9 }).length };
}).sort((a, b) => b.gz - a.gz);
const total = { crudo: filas.reduce((a, r) => a + r.crudo, 0), gz: filas.reduce((a, r) => a + r.gz, 0) };
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

console.log("Peso inicial de la tienda (entrada + vitrina, JS y CSS):");
for (const r of filas) console.log(`  ${r.f.padEnd(44)} ${kb(r.crudo).padStart(10)}  gzip ${kb(r.gz).padStart(9)}`);
console.log(`  ${"TOTAL".padEnd(44)} ${kb(total.crudo).padStart(10)}  gzip ${kb(total.gz).padStart(9)}`);

// 3. terceros en cualquier archivo publicado del build
const conTerceros = [];
for (const f of [...trozos.map((t) => t.file), "index.html"]) {
  const p = resolve(DIST, f);
  if (existsSync(p) && TERCEROS.test(readFileSync(p, "utf8"))) conTerceros.push(f);
}

// 4. O2-01b: el bloque del cuerpo por SSI de index.html enlaza la hoja de la vitrina (plugin cssDeLaTienda)
const indexHtml = existsSync(resolve(DIST, "index.html")) ? readFileSync(resolve(DIST, "index.html"), "utf8") : "";
const hojaVitrina = vitrina.css.find((c) => indexHtml.includes(`href="/${c}"`));
const sinHoja = !!indexHtml && (indexHtml.includes("<!--sh-store-css-->") || !hojaVitrina);

if (process.argv.includes("--registrar")) {
  const previo = existsSync(BASE) ? JSON.parse(readFileSync(BASE, "utf8")) : {};
  const base = { ...previo, gzip_bytes: total.gz, bytes: total.crudo, archivos: filas.length, registrado: new Date().toISOString().slice(0, 10) };
  writeFileSync(BASE, `${JSON.stringify(base, null, 2)}\n`);
  console.log(`Linea base registrada en scripts/peso-tienda.base.json: gzip ${kb(total.gz)}.`);
}

let mal = false;
if (intrusos.length) {
  mal = true;
  console.error(`\nLa tienda descarga ${intrusos.length} modulo(s) del panel al entrar (cargarlos con import() o lazy()):`);
  for (const m of intrusos) console.error(`  ${m}`);
}
if (conTerceros.length) {
  mal = true;
  console.error(`\nArchivos del build con Google o medicion de terceros (la tienda no usa ninguno): ${conTerceros.join(", ")}`);
}
if (sinHoja) {
  mal = true;
  console.error("\nindex.html no enlaza la hoja de la vitrina en el bloque del cuerpo por SSI (O2-01b): revisar cssDeLaTienda en vite.tienda.ts.");
}
if (existsSync(BASE)) {
  const base = JSON.parse(readFileSync(BASE, "utf8"));
  const tope = Math.round(base.gzip_bytes * (1 + MARGEN));
  const dif = ((total.gz / base.gzip_bytes - 1) * 100).toFixed(1);
  console.log(`\nLinea base: gzip ${kb(base.gzip_bytes)} (${base.registrado}) · ahora ${kb(total.gz)} (${dif >= 0 ? "+" : ""}${dif} %) · tope ${kb(tope)} (+5 %)`);
  if (total.gz > tope) {
    mal = true;
    console.error(`El peso inicial de la tienda subio mas de 5 %. Si es a proposito, registrar la nueva linea base con \`node scripts/peso-tienda.mjs --registrar\` y explicarlo en el PR.`);
  }
} else if (!process.argv.includes("--registrar")) {
  mal = true;
  console.error("\nFalta la linea base (scripts/peso-tienda.base.json): `node scripts/peso-tienda.mjs --registrar`.");
}
process.exit(mal ? 1 : 0);
