/* Plugins de Vite de la tienda (O2-01d). Los usa vite.config.ts (el build real); no cambian lo que se publica.

   mapaDeTrozos: al terminar el build escribe .peso-tienda/trozos.json (fuera de dist/: no se publica) con cada trozo de
   JavaScript, sus importaciones, sus hojas de estilo y los modulos de src/ que trae. Lo lee scripts/peso-tienda.mjs para
   medir el peso inicial de la tienda y comprobar que la tienda no descarga codigo del panel. */
import { mkdirSync, writeFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import type { Plugin } from "vite";

export type Trozo = { file: string; entry: boolean; facade: string | null; imports: string[]; dynamicImports: string[]; css: string[]; modules: string[] };

export function mapaDeTrozos(): Plugin {
  let raiz = process.cwd();
  let salida = "dist";
  return {
    name: "crimson:mapa-de-trozos",
    apply: "build",
    configResolved(c) { raiz = c.root; salida = relative(c.root, resolve(c.root, c.build.outDir)).replace(/\\/g, "/"); },
    generateBundle(_opts, bundle) {
      const rel = (id: string) => relative(raiz, id.split("?")[0]).replace(/\\/g, "/");
      const trozos: Trozo[] = [];
      for (const b of Object.values(bundle)) {
        if (b.type !== "chunk") continue;
        const meta = (b as typeof b & { viteMetadata?: { importedCss: Set<string> } }).viteMetadata;
        trozos.push({
          file: b.fileName,
          entry: b.isEntry,
          facade: b.facadeModuleId ? rel(b.facadeModuleId) : null,
          imports: b.imports,
          dynamicImports: b.dynamicImports,
          css: meta ? [...meta.importedCss] : [],
          modules: Object.keys(b.modules).filter((m) => !m.includes("node_modules") && !m.startsWith("\0")).map(rel),
        });
      }
      const dir = resolve(raiz, ".peso-tienda");
      mkdirSync(dir, { recursive: true });
      writeFileSync(resolve(dir, "trozos.json"), JSON.stringify({ outDir: salida, trozos }, null, 1));
    },
  };
}
