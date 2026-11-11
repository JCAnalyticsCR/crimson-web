/* Tipos minimos de Node para que `npm run e2e:tipos` revise las pruebas y las configuraciones.

   A proposito NO se instala @types/node: entraria tambien al programa de src/ (por los tipos de vitest) y puede
   cambiar los globales del navegador por los de Node (p. ej. lo que devuelve setTimeout), con `tsc -b` en rojo en
   archivos que nadie toco. Aqui va solo lo que las pruebas usan; si falta algo, se agrega aqui. */
declare const process: { env: Record<string, string | undefined>; platform: string; cwd(): string };
declare const __dirname: string;

declare module "node:fs" {
  export function existsSync(path: string): boolean;
  export function mkdirSync(path: string, options?: { recursive?: boolean }): string | undefined;
  export function writeFileSync(path: string, data: string): void;
  export function readFileSync(path: string, encoding: "utf8"): string;
  export function readdirSync(path: string): string[];
}
declare module "node:path" {
  export function resolve(...parts: string[]): string;
  export function relative(from: string, to: string): string;
}
/* O2-01d: el codigo TOTP del administrador con 2FA (mi-tienda.spec.ts) */
declare class Buffer extends Uint8Array {
  static from(data: number[]): Buffer;
  static alloc(size: number): Buffer;
  writeBigUInt64BE(value: bigint, offset?: number): number;
  readUInt32BE(offset?: number): number;
  toString(encoding?: string): string;
}
declare module "node:crypto" {
  export function createHmac(algorithm: string, key: Uint8Array): { update(data: Uint8Array): { digest(): Buffer } };
}
