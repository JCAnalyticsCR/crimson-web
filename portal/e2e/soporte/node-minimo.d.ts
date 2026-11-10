/* Tipos minimos de Node para que `npm run e2e:tipos` revise las pruebas y las configuraciones.

   A proposito NO se instala @types/node: entraria tambien al programa de src/ (por los tipos de vitest) y puede
   cambiar los globales del navegador por los de Node (p. ej. lo que devuelve setTimeout), con `tsc -b` en rojo en
   archivos que nadie toco. Aqui va solo lo que las pruebas usan; si falta algo, se agrega aqui. */
declare const process: { env: Record<string, string | undefined>; platform: string };
declare const __dirname: string;

declare module "node:fs" {
  export function existsSync(path: string): boolean;
  export function mkdirSync(path: string, options?: { recursive?: boolean }): string | undefined;
  export function writeFileSync(path: string, data: string): void;
}
declare module "node:path" {
  export function resolve(...parts: string[]): string;
}
