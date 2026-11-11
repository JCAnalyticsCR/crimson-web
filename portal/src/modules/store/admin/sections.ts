/* Mi Tienda por secciones (O4-01): cada seccion con su direccion, su grupo del menu y el permiso que la abre. Modulo
   puro (sin React) para probar los permisos sin navegador (__tests__/secciones.test.ts).

   Permisos (los mismos de hoy, la API manda):
   - settings.configurar (solo el administrador): apariencia, portada, kits, paginas, pagos y envios, SEO, publicacion y
     el contenido por aprobar.
   - catalog.editar: catalogo y categorias (publicar un producto pide ademas catalog.publicar; ver costos, catalog.costos).
   - El resumen lo ve quien abre cualquiera de las dos cosas.
   - La Tienda de pruebas: solo el rol administrador (la API responde 404 a cualquier otro). */

export type SectionKey =
  | "resumen" | "apariencia" | "portada" | "kits" | "paginas" | "catalogo" | "categorias" | "contenido"
  | "pagos-envios" | "seo" | "publicacion" | "historial" | "pruebas";

export type Section = {
  key: SectionKey;
  /** direccion bajo /mi-tienda ("" = el resumen) */
  path: string;
  label: string;
  hint: string;
  need: string;
  group: "Tienda" | "Catálogo" | "Venta" | "Aparte";
  adminOnly?: boolean;
};

export const BASE = "/mi-tienda";

export const SECTIONS: Section[] = [
  { key: "resumen", path: "", label: "Resumen", hint: "Lista para vender y salud del catálogo", need: "settings.configurar|catalog.editar", group: "Tienda" },
  { key: "apariencia", path: "apariencia", label: "Apariencia", hint: "Nombre, logo, colores y tipografía", need: "settings.configurar", group: "Tienda" },
  { key: "portada", path: "portada", label: "Portada", hint: "Titular, banners, sellos y orden de las secciones", need: "settings.configurar", group: "Tienda" },
  { key: "kits", path: "kits", label: "Destacados y kits", hint: "Productos destacados y kits por necesidad", need: "settings.configurar", group: "Tienda" },
  { key: "paginas", path: "paginas", label: "Páginas", hint: "Envíos, garantías, términos, privacidad y preguntas", need: "settings.configurar", group: "Tienda" },
  { key: "catalogo", path: "catalogo", label: "Catálogo", hint: "Todos los productos de la web, con filtros de calidad", need: "catalog.editar", group: "Catálogo" },
  { key: "categorias", path: "categorias", label: "Categorías", hint: "Orden, visibilidad, imagen y texto de cada categoría", need: "catalog.editar", group: "Catálogo" },
  /* Ola 3 (O3-11): la pantalla «Contenido por aprobar» la trae la ola del catalogo; aqui queda su lugar en el menu */
  { key: "contenido", path: "contenido-por-aprobar", label: "Contenido por aprobar", hint: "Nombres, fichas y descripciones propuestos", need: "settings.configurar", group: "Catálogo" },
  { key: "pagos-envios", path: "pagos-envios", label: "Pagos y envíos", hint: "Tarifas, envío gratis, retiro, formas de pago y datos del comercio", need: "settings.configurar", group: "Venta" },
  { key: "seo", path: "seo", label: "SEO", hint: "Título, descripción y vista previa en Google", need: "settings.configurar", group: "Venta" },
  { key: "publicacion", path: "publicacion", label: "Publicación y dominio", hint: "Publicar, dominio, tipo de tienda y moneda", need: "settings.configurar", group: "Aparte" },
  /* O4-02: los ultimos 20 guardados, con «Restaurar» para lo que se puede volver atras */
  { key: "historial", path: "historial", label: "Historial", hint: "Quién cambió qué y cuándo, y restaurar", need: "settings.configurar", group: "Aparte" },
  { key: "pruebas", path: "pruebas", label: "Tienda de pruebas", hint: "Pagos con ONVO en modo Test", need: "settings.configurar", group: "Aparte", adminOnly: true },
];

export const GROUPS: Section["group"][] = ["Tienda", "Catálogo", "Venta", "Aparte"];

export const sectionUrl = (s: Pick<Section, "path">) => (s.path ? `${BASE}/${s.path}` : BASE);

/** Las secciones que esta cuenta puede abrir, en el orden del menu. */
export function visibleSections(allows: (need?: string) => boolean, isAdmin: boolean): Section[] {
  return SECTIONS.filter((s) => allows(s.need) && (!s.adminOnly || isAdmin));
}

/** La seccion de una direccion (/mi-tienda/catalogo/123 -> catalogo). Fuera de Mi Tienda, undefined. */
export function sectionFor(pathname: string): Section | undefined {
  const p = pathname.replace(/\/+$/, "");
  if (p === BASE) return SECTIONS[0];
  if (!p.startsWith(`${BASE}/`)) return undefined;
  const first = p.slice(BASE.length + 1).split("/")[0];
  return SECTIONS.find((s) => s.path && s.path === first);
}
