/* Plugins de Vite de la tienda (O2-01d y O2-01b). Los usa vite.config.ts (el build real).

   mapaDeTrozos: al terminar el build escribe .peso-tienda/trozos.json (fuera de dist/: no se publica) con cada trozo de
   JavaScript, sus importaciones, sus hojas de estilo y los modulos de src/ que trae. Lo lee scripts/peso-tienda.mjs para
   medir el peso inicial de la tienda y comprobar que la tienda no descarga codigo del panel. */
import { mkdirSync, writeFileSync } from "node:fs";
import { relative, resolve } from "node:path";
import type { Plugin } from "vite";

/* O2-01b: lugar de index.html donde va el enlace a la hoja de la tienda (dentro del bloque SSI del cuerpo) */
export const MARCA_CSS_TIENDA = "<!--sh-store-css-->";
const VITRINA = "src/modules/store/PublicStore.tsx";

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

/* cssDeLaTienda (O2-01b): la hoja de la tienda (shop.css, vitrina.css...) viaja en el trozo perezoso de la vitrina e
   index.html solo enlaza la del panel. El contenido que nginx incrusta en el HTML (cuerpo por SSI) usa las clases de la
   tienda: sin su hoja se pintaria sin estilo y despues saltaria todo. Este plugin cambia la marca <!--sh-store-css--> de
   index.html (que solo existe dentro del bloque SSI del cuerpo) por el enlace a esas hojas con su hash. Es el mismo
   archivo que pide la vitrina al cargar: Vite no lo vuelve a pedir si el enlace ya esta en la pagina. */
export function cssDeLaTienda(): Plugin {
  let raiz = process.cwd();
  return {
    name: "crimson:css-de-la-tienda",
    apply: "build",
    configResolved(c) { raiz = c.root; },
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        if (!html.includes(MARCA_CSS_TIENDA) || !ctx.bundle) return html;
        const rel = (id: string) => relative(raiz, id.split("?")[0]).replace(/\\/g, "/");
        const trozos = Object.values(ctx.bundle).filter((b) => b.type === "chunk");
        const vitrina = trozos.find((b) => b.type === "chunk" && b.facadeModuleId && rel(b.facadeModuleId) === VITRINA);
        if (!vitrina || vitrina.type !== "chunk") throw new Error(`cssDeLaTienda: no encontre el trozo de ${VITRINA}`);
        const entrada = new Set(trozos.filter((b) => b.type === "chunk" && b.isEntry).flatMap((b) => [...((b as typeof b & { viteMetadata?: { importedCss: Set<string> } }).viteMetadata?.importedCss || [])]));
        const hojas = new Set<string>();
        const visto = new Set<string>();
        const juntar = (file: string) => {
          if (visto.has(file)) return;
          visto.add(file);
          const b = ctx.bundle![file];
          if (!b || b.type !== "chunk" || b.isEntry) return;
          for (const css of (b as typeof b & { viteMetadata?: { importedCss: Set<string> } }).viteMetadata?.importedCss || []) if (!entrada.has(css)) hojas.add(css);
          b.imports.forEach(juntar);
        };
        juntar(vitrina.fileName);
        if (!hojas.size) throw new Error("cssDeLaTienda: la vitrina no trae hoja de estilo");
        return html.replace(MARCA_CSS_TIENDA, [...hojas].map((h) => `<link rel="stylesheet" crossorigin href="/${h}">`).join(""));
      },
    },
  };
}
