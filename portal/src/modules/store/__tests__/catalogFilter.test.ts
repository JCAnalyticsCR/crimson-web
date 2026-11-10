/* Primeras pruebas de catalogFilter.ts (tarea O0-02): FIJAN EL COMPORTAMIENTO DE HOY de la busqueda, el orden, los
   filtros y las facetas que la tienda calcula en el navegador a partir del snapshot. No dicen que ese comportamiento
   sea el deseado: donde el plan de la tienda lo va a cambiar, el nombre de la prueba lleva la tarea que lo cambia
   (p. ej. O1-14 para los plurales) y esa tarea actualiza la expectativa.

   Datos 100 % ficticios. «Hikvision» aparece como texto porque la regla de orden lo lleva escrito en el codigo
   (decision del dueno del 21/10: Hikvision destacado y primero). */
import { describe, expect, it } from "vitest";
import {
  descendants, filterCatalog, pidFromRef, snapToVProd,
  type SnapCategory, type SnapProduct, type Snapshot,
} from "../catalogFilter";

const sinTildes = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
const LABEL: Record<string, string> = { en_bodega: "En bodega", bajo_pedido: "Bajo pedido", consultar: "Consultar disponibilidad", agotado: "Agotado" };

function prod(id: number, o: Partial<Omit<SnapProduct, "availability">> & { state?: string }): SnapProduct {
  const { state = "en_bodega", ...rest } = o;
  const base = {
    id, path: `/p/producto-${id}`, name: `Producto ${id}`, brand: null as string | null, model: null as string | null, code: `SKU-${id}`,
    category_id: null as number | null, display_price: 1000, web_featured: false, image: `/img/${id}.png` as string | null,
    type: null as string | null, created_ts: id,
    ...rest,
  };
  return {
    ...base,
    availability: { state, label: LABEL[state], accion: state === "en_bodega" || state === "bajo_pedido" ? "comprar" : "whatsapp", max_qty: null, plazo: null },
    // mismo campo que arma el API (store_snapshot._sf): nombre + marca + modelo, en minusculas y sin tildes
    sf: rest.sf ?? sinTildes([base.name, base.brand, base.model].filter(Boolean).join(" ")),
  };
}

const CATS: SnapCategory[] = [
  { id: 1, name: "Seguridad", slug: "seguridad", parent_id: null, count: 0 },
  { id: 11, name: "Cámaras", slug: "camaras", parent_id: 1, count: 2 },
  { id: 12, name: "Grabadores", slug: "grabadores", parent_id: 1, count: 1 },
  { id: 2, name: "Redes", slug: "redes", parent_id: null, count: 1 },
  { id: 3, name: "Vacía", slug: "vacia", parent_id: null, count: 0 },
  { id: 9, name: "Huérfana", slug: "huerfana", parent_id: 99, count: 1 }, // su madre no existe en el snapshot
];

const P1 = prod(1, { name: "Cámara bullet 4 MP", brand: "Vigia", model: "VG-100", category_id: 11, display_price: 1500, type: "Cámara IP", created_ts: 100 });
const P2 = prod(2, { name: "Cámara domo 2 MP", brand: "Hikvision", model: "HK-200", category_id: 11, display_price: 1200, type: "Cámara IP", created_ts: 200, state: "bajo_pedido" });
const P3 = prod(3, { name: "Grabador NVR 8 canales", brand: "Vigia", model: "VG-NVR8", category_id: 12, display_price: 1900, image: null, type: "Grabador NVR", created_ts: 300, state: "consultar" });
const P4 = prod(4, { name: "Switch PoE 8 puertos", brand: "Nodo", model: "ND-8P", category_id: 2, display_price: 0, type: "Switch PoE", created_ts: 400, state: "agotado" });
const P5 = prod(5, { name: "Antena exterior", category_id: 9, display_price: 800, web_featured: true, created_ts: 50 });
const P6 = prod(6, { name: "Cable sin categoría", brand: "Nodo", display_price: 100, type: "Cable", created_ts: 400 });

const SNAP: Snapshot = {
  v: 1, version: "1", built_at: "2026-11-10T00:00:00+00:00", currency: "CRC", n: 6,
  products: [P1, P2, P3, P4, P5, P6],
  categories: CATS,
  brands: [], types: [],
  showcase: { headline: null, subline: null, featured: [], banners: [], trust: [] },
};

const ids = (r: { items: SnapProduct[] }) => r.items.map((p) => p.id);
const todos = (params: Parameters<typeof filterCatalog>[1] = {}) => filterCatalog(SNAP, params);

describe("filterCatalog · sin filtros", () => {
  it("devuelve todo, con la moneda del snapshot y 24 por página", () => {
    const r = todos();
    expect(r.total).toBe(6);
    expect(r.offset).toBe(0);
    expect(r.currency).toBe("CRC");
    expect(r.category).toBeNull();
    const muchos: Snapshot = { ...SNAP, products: Array.from({ length: 30 }, (_, i) => prod(100 + i, {})) };
    expect(filterCatalog(muchos, {}).items).toHaveLength(24);
    expect(filterCatalog(muchos, {}).total).toBe(30);
  });

  it("orden por defecto: primero lo que está en bodega, luego bajo pedido, luego lo demás", () => {
    // en bodega: destacado (5) antes que el resto, que va por nombre (6 «Cable…» antes que 1 «Cámara…»)
    // lo que no se puede comprar: con foto (4) antes que sin foto (3)
    expect(ids(todos())).toEqual([5, 6, 1, 2, 4, 3]);
  });

  it("pagina con limit y offset sin cambiar el total", () => {
    const r = todos({ limit: 2, offset: 2 });
    expect(ids(r)).toEqual([1, 2]);
    expect(r.total).toBe(6);
    expect(r.offset).toBe(2);
  });
});

describe("filterCatalog · búsqueda", () => {
  it("ignora mayúsculas y tildes de lo que escribe la persona", () => {
    expect(ids(todos({ q: "CÁMARA" }))).toEqual([1, 2]);
    expect(ids(todos({ q: "camara" }))).toEqual([1, 2]);
  });

  it("exige todos los términos (Y), en cualquier orden y con espacios de más", () => {
    expect(ids(todos({ q: "  domo   cámara " }))).toEqual([2]);
    expect(ids(todos({ q: "vigia nvr" }))).toEqual([3]);
    expect(todos({ q: "camara nvr" }).total).toBe(0);
  });

  it("busca en nombre, marca y modelo", () => {
    expect(ids(todos({ q: "hk-200" }))).toEqual([2]);
    expect(ids(todos({ q: "nodo" })).sort()).toEqual([4, 6]);
  });

  it("hoy el plural no encuentra el singular (lo cambia O1-14)", () => {
    expect(todos({ q: "camaras" }).total).toBe(0);
    expect(todos({ q: "grabadores" }).total).toBe(0);
  });

  it("hoy no encuentra por código (SKU) ni por modelo escrito sin guion (lo cambia O1-14)", () => {
    expect(todos({ q: "SKU-3" }).total).toBe(0);
    expect(todos({ q: "hk200" }).total).toBe(0);
  });

  it("entre coincidencias manda la disponibilidad: en bodega antes que bajo pedido", () => {
    expect(ids(todos({ q: "camara" }))).toEqual([1, 2]); // la 2 es Hikvision, pero está bajo pedido
  });
});

describe("filterCatalog · filtros", () => {
  it("marca: sin distinguir mayúsculas y varias a la vez", () => {
    expect(ids(todos({ brands: ["vigia"] })).sort()).toEqual([1, 3]);
    expect(ids(todos({ brands: ["VIGIA", "Nodo", ""] })).sort()).toEqual([1, 3, 4, 6]);
  });

  it("tipo: coincidencia exacta", () => {
    expect(ids(todos({ tipo: "Cámara IP" })).sort()).toEqual([1, 2]);
    expect(todos({ tipo: "cámara ip" }).total).toBe(0);
  });

  it("precio: el máximo deja fuera lo que no tiene precio; el mínimo en 0 no", () => {
    expect(ids(todos({ min: 1000 })).sort()).toEqual([1, 2, 3]);
    expect(ids(todos({ max: 1000 })).sort()).toEqual([5, 6]); // el 4 («a consultar», precio 0) no entra
    expect(todos({ min: 0 }).total).toBe(6);
    expect(ids(todos({ min: 800, max: 1500 })).sort()).toEqual([1, 2, 5]);
  });

  it("disponibilidad: «disponible» es solo en bodega; «bajo_pedido» incluye también lo de bodega", () => {
    expect(ids(todos({ avail: "disponible" })).sort()).toEqual([1, 5, 6]);
    expect(ids(todos({ avail: "inmediata" })).sort()).toEqual([1, 5, 6]);
    expect(ids(todos({ avail: "bajo_pedido" })).sort()).toEqual([1, 2, 5, 6]);
    expect(todos({ avail: "otra-cosa" }).total).toBe(6);
  });

  it("categoría: por dirección o por número, e incluye las subcategorías", () => {
    expect(ids(todos({ category: "seguridad" })).sort()).toEqual([1, 2, 3]);
    expect(ids(todos({ category: "camaras" })).sort()).toEqual([1, 2]);
    expect(ids(todos({ category: "11" })).sort()).toEqual([1, 2]);
    expect(todos({ category: "vacia" }).total).toBe(0);
  });

  it("hoy una categoría que no existe no filtra nada y devuelve category null", () => {
    const r = todos({ category: "no-existe" });
    expect(r.total).toBe(6);
    expect(r.category).toBeNull();
  });

  it("los filtros se combinan", () => {
    expect(ids(todos({ category: "seguridad", brands: ["Vigia"], avail: "disponible" }))).toEqual([1]);
    expect(ids(todos({ q: "camara", max: 1300 }))).toEqual([2]);
  });
});

describe("filterCatalog · orden", () => {
  it("nombre: destacados primero, luego Hikvision, luego alfabético", () => {
    expect(ids(todos({ sort: "name" }))).toEqual([5, 2, 6, 1, 3, 4]);
  });

  it("precio: lo que no tiene precio va siempre al final", () => {
    expect(ids(todos({ sort: "price_asc" }))).toEqual([6, 5, 2, 1, 3, 4]);
    expect(ids(todos({ sort: "price_desc" }))).toEqual([3, 1, 2, 5, 6, 4]);
  });

  it("novedades: el más nuevo primero y, con la misma fecha, el id más alto", () => {
    expect(ids(todos({ sort: "recent" }))).toEqual([6, 4, 3, 2, 1, 5]);
    expect(ids(todos({ sort: "cualquier-otro" }))).toEqual([6, 4, 3, 2, 1, 5]); // un orden desconocido cae aquí
  });
});

describe("filterCatalog · facetas", () => {
  it("marcas con Hikvision primero y luego por cantidad; tipos por cantidad", () => {
    const f = todos().facets;
    expect(f.brands).toEqual([{ name: "Hikvision", count: 1 }, { name: "Vigia", count: 2 }, { name: "Nodo", count: 2 }]);
    expect(f.types).toEqual([{ name: "Cámara IP", count: 2 }, { name: "Grabador NVR", count: 1 }, { name: "Switch PoE", count: 1 }, { name: "Cable", count: 1 }]);
  });

  it("disponibilidad y rango de precios (sin contar lo que no tiene precio)", () => {
    const f = todos().facets;
    expect(f.availability).toEqual({ disponible: 3, bajo_pedido: 4 });
    expect(f.price).toEqual({ min: 100, max: 1900 });
  });

  it("cada faceta ignora su propio filtro y respeta los demás", () => {
    const f = todos({ brands: ["Vigia"] }).facets;
    expect(f.brands).toEqual([{ name: "Hikvision", count: 1 }, { name: "Vigia", count: 2 }, { name: "Nodo", count: 2 }]);
    expect(f.types).toEqual([{ name: "Cámara IP", count: 1 }, { name: "Grabador NVR", count: 1 }]);
    expect(f.availability).toEqual({ disponible: 1, bajo_pedido: 1 });
    expect(f.price).toEqual({ min: 1500, max: 1900 });
    // con el filtro de disponibilidad puesto, su propia faceta sigue contando todo
    expect(todos({ avail: "disponible" }).facets.availability).toEqual({ disponible: 3, bajo_pedido: 4 });
  });

  it("sin productos con precio el rango queda en 0", () => {
    expect(todos({ brands: ["Nodo"], tipo: "Switch PoE" }).facets.price).toEqual({ min: 0, max: 0 });
  });

  it("no pasa de 30 marcas", () => {
    const muchas: Snapshot = { ...SNAP, products: Array.from({ length: 40 }, (_, i) => prod(200 + i, { brand: `Marca ${i}` })) };
    expect(filterCatalog(muchas, {}).facets.brands).toHaveLength(30);
  });
});

describe("filterCatalog · categorías", () => {
  it("menú: solo raíces con productos, sumando subcategorías, de mayor a menor", () => {
    // «Huérfana» apunta a una madre que no existe: se trata como raíz. «Vacía» no sale.
    expect(todos().nav).toEqual([
      { name: "Seguridad", path: "/c/seguridad", count: 3 },
      { name: "Redes", path: "/c/redes", count: 1 },
      { name: "Huérfana", path: "/c/huerfana", count: 1 },
    ]);
  });

  it("el menú no cambia con los filtros ni con la búsqueda", () => {
    expect(todos({ q: "camara", brands: ["Vigia"] }).nav).toEqual(todos().nav);
  });

  it("datos de la categoría abierta: madre e hijas", () => {
    expect(todos({ category: "camaras" }).category).toEqual({
      id: 11, name: "Cámaras", slug: "camaras", description: null,
      parent: { name: "Seguridad", path: "/c/seguridad" }, children: [],
    });
    expect(todos({ category: "seguridad" }).category).toMatchObject({
      parent: null,
      children: [{ name: "Cámaras", path: "/c/camaras" }, { name: "Grabadores", path: "/c/grabadores" }],
    });
    expect(todos({ category: "huerfana" }).category).toMatchObject({ id: 9, parent: null });
  });

  it("descendants incluye a la propia categoría", () => {
    expect([...descendants(CATS, 1)].sort((a, b) => a - b)).toEqual([1, 11, 12]);
    expect([...descendants(CATS, 2)]).toEqual([2]);
  });
});

describe("snapToVProd", () => {
  it("arma la tarjeta de la vitrina con la categoría y el IVA de 13 %", () => {
    const v = snapToVProd(P1, "CRC", CATS);
    expect(v).toMatchObject({
      id: 1, code: "SKU-1", name: "Cámara bullet 4 MP", brand: "Vigia", model: "VG-100", image: "/img/1.png", images: ["/img/1.png"],
      category: "Cámaras", category_id: 11, category_path: "/c/camaras", path: "/p/producto-1", type: "Cámara IP",
      tax_rate: 13, display_price: 1500, display_currency: "CRC", price: 1500, currency: "CRC", price_on_request: false,
      description: null, compare_display_price: null, variants: [],
    });
  });

  it("solo en bodega y bajo pedido se pueden comprar; consultar y agotado quedan bloqueados", () => {
    expect(snapToVProd(P1, "CRC", CATS).availability).toMatchObject({ state: "en_bodega", blocked: false, disponible: true });
    expect(snapToVProd(P2, "CRC", CATS).availability).toMatchObject({ state: "bajo_pedido", blocked: false, disponible: true });
    expect(snapToVProd(P3, "CRC", CATS).availability).toMatchObject({ state: "consultar", label: "Consultar disponibilidad", blocked: true, disponible: false });
    expect(snapToVProd(P4, "CRC", CATS).availability).toMatchObject({ state: "agotado", blocked: true, disponible: false });
  });

  it("el snapshot no trae cantidad ni «últimas»: la tarjeta sale sin ellas", () => {
    expect(snapToVProd(P1, "CRC", CATS).availability).toMatchObject({ cantidad: null, ultimas: false, max_qty: null });
  });

  it("sin precio es «a consultar»; sin foto, sin categoría o sin tipo no se rompe", () => {
    expect(snapToVProd(P4, "CRC", CATS).price_on_request).toBe(true);
    expect(snapToVProd(P3, "CRC", CATS)).toMatchObject({ image: null, images: [] });
    expect(snapToVProd(P6, "CRC", CATS)).toMatchObject({ category: null, category_id: null, category_path: null });
    expect(snapToVProd(P5, "CRC", CATS).type).toBeUndefined();
    expect(snapToVProd(P5, "CRC", CATS).model).toBeUndefined();
  });
});

describe("pidFromRef", () => {
  it("saca el id del final de la dirección de la ficha", () => {
    expect(pidFromRef("camara-bullet-4-mp-68")).toBe(68);
    expect(pidFromRef("producto-7")).toBe(7);
  });

  it("sin guion antes del número, o sin número, no hay id", () => {
    expect(pidFromRef("68")).toBeNull();
    expect(pidFromRef("camara-bullet")).toBeNull();
    expect(pidFromRef("camara-12a")).toBeNull();
  });
});
