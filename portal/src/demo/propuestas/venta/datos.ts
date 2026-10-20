/* Datos canonicos del equipo venta (seccion 3.0 de la especificacion). Productos REALES de la tienda propia
   (catalogo GET /public/store/crimson/products). precio con IVA = crc x 1,13 (redondeo a centimos igual que
   catalog.ts). Personas, empresa y numeros de documento son de EJEMPLO. No se importan de otro equipo: si
   cambian, se cambian aqui. */

export type ProductoVenta = {
  id: number; nombre: string; codigo: string; marca: string; sinIva: number; conIva: number; disponibilidad: string;
};

export const PRODUCTOS: Record<number, ProductoVenta> = {
  68:  { id: 68,  nombre: "Cámara bullet Hikvision DS-2CD2043G2-LI2U(2.8MM) 4MP AcuSense smart hybrid light", codigo: "SG2045",       marca: "Hikvision", sinIva: 55800,  conIva: 63054,   disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  90:  { id: 90,  nombre: "Cámara dome Hikvision DS-2CD2143G2-LIS2U(2.8mm) 4MP AcuSense smart hybrid",       codigo: "SG2044",       marca: "Hikvision", sinIva: 59000,  conIva: 66670,   disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  186: { id: 186, nombre: "Grabadora NVR Hikvision DS-7104NI-Q1/4P 4 canales IP hasta 6 MP PoE",             codigo: "3102068",      marca: "Hikvision", sinIva: 50700,  conIva: 57291,   disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  188: { id: 188, nombre: "Grabadora NVR Hikvision DS-7608NXI-K1/8P 8 canales IP 8 puertos PoE 4K",         codigo: "SG5046",       marca: "Hikvision", sinIva: 177400, conIva: 200462,  disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  240: { id: 240, nombre: "Disco duro interno 2TB Seagate ST2000VX017 3.5'' SATA optimizado videovigilancia",codigo: "ST2000VX017",  marca: "Seagate",   sinIva: 87100,  conIva: 98423,   disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  242: { id: 242, nombre: "Disco duro interno 6TB Western Digital Purple WD64PURZ SATA",                     codigo: "WD64PURZ",     marca: "Western Digital", sinIva: 211200, conIva: 238656, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  245: { id: 245, nombre: "Cable UTP Cat6 Nova FAN60305001C 305M PVC",                                       codigo: "FAN60305001C", marca: "Nova",      sinIva: 28500,  conIva: 32205,   disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  17:  { id: 17,  nombre: "Soporte de muro para cámara de seguridad Hikvision DS-1602ZJ",                    codigo: "300025",       marca: "Hikvision", sinIva: 8900,   conIva: 10057,   disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  39:  { id: 39,  nombre: "Switch Hikvision DS-3T1506HP-EI-UPS 4 puertos PoE / 2 SFP con respaldo de energía", codigo: "RC5081",    marca: "Hikvision", sinIva: 93000,  conIva: 105090,  disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
};

/** ficha real de la tienda que replica A1 */
export const FICHA_A1 = { id: 68, ruta: "/p/camara-bullet-hikvision-ds-2cd2043g2-li2u-2-8mm-4mp-acusense-smart-68", direccion: "tienda.crimsoncr.com/p/camara-bullet-hikvision-ds-2cd2043g2-li2u-2-8mm-4mp-acusense-smart-68" };

// ---------- personas y empresa de ejemplo (correos @example.com, RFC 2606) ----------
export const CLIENTE = { nombre: "María", completo: "María Rodríguez", correo: "maria@example.com", zona: "San Rafael, Alajuela" };
export const EMPRESA = { razon: "Comercial Ejemplo S.A.", cedula: "3-101-000000", atencion: "Laura Méndez", correo: "compras@example.com" };
export const TECNICO = "Luis";

// ---------- numeros de documento de ejemplo ----------
export const DOC = {
  levantamiento: "LEV-2026-0213",
  oportunidad: "OPO-2026-0412",
  cotizacion: "COT-2026-0417",
  pedidoA3: "ORD-000129",
};

// ---------- kits reales de la portada (mismas reglas que "Arma tu sistema") ----------
export type LineaKit = { id: number; cant: number; rol: string; razon: string };
export type FaltanteKit = { nombre: string; cant: number; rol: string; razon: string };

/** "Casa segura · 4 cámaras": 2 adentro, 2 afuera, 15 dias, celular.
    Totales calculados con catalog.ts gross(p) = crc × 1,13. */
export const KIT_CASA = {
  titulo: "Casa segura · 4 cámaras",
  texto: "2 adentro, 2 afuera, 15 días de grabación y vista desde el celular.",
  total: 447367,
  articulos: 7,
  lineas: [
    { id: 68,  cant: 2, rol: "Cámaras exteriores", razon: "2 punto(s) afuera: formato bullet, resistente al agua" },
    { id: 90,  cant: 2, rol: "Cámaras interiores", razon: "2 punto(s) adentro: formato domo, discreto" },
    { id: 186, cant: 1, rol: "Grabador",            razon: "4 cámara(s): nvr 4 canales con PoE integrado (no hace falta switch)" },
    { id: 240, cant: 1, rol: "Almacenamiento",      razon: "15 días × 4 cámara(s) ≈ 1.4 TB en grabación continua" },
    { id: 245, cant: 1, rol: "Instalación",         razon: "92 m de cable UTP (20 m por cámara + 15 %)" },
  ] as LineaKit[],
  faltantes: [{ nombre: "Conectores RJ45", cant: 8, rol: "Instalación", razon: "2 por cámara (4 cámaras)" }] as FaltanteKit[],
};

/** "Negocio u oficina · 8 cámaras": 4 adentro, 4 afuera, 30 dias, celular.
    Totales calculados con catalog.ts gross(p) = crc × 1,13. */
export const KIT_NEGOCIO = {
  titulo: "Negocio u oficina · 8 cámaras",
  camaras: 8,
  tb: "5.5",
  total: 990219,
  respuestas: { place: "negocio", indoor: 4, outdoor: 4, days: 30, remote: true },
  lineas: [
    { id: 68,  cant: 4, rol: "Cámaras exteriores", razon: "4 punto(s) afuera: formato bullet, resistente al agua" },
    { id: 90,  cant: 4, rol: "Cámaras interiores", razon: "4 punto(s) adentro: formato domo, discreto" },
    { id: 188, cant: 1, rol: "Grabador",            razon: "8 cámara(s): nvr 8 canales con PoE integrado (no hace falta switch)" },
    { id: 242, cant: 1, rol: "Almacenamiento",      razon: "30 días × 8 cámara(s) ≈ 5.5 TB en grabación continua" },
    { id: 245, cant: 1, rol: "Instalación",         razon: "276 m de cable UTP (30 m por cámara + 15 %)" },
  ] as LineaKit[],
  faltantes: [{ nombre: "Conectores RJ45", cant: 16, rol: "Instalación", razon: "2 por cámara (8 cámaras)" }] as FaltanteKit[],
  notas: [
    "Kit sugerido: un técnico de Crimson lo valida antes de cobrar o instalar (distancias, alturas y red del sitio).",
    "Almacenamiento estimado con grabación continua a 4 MP en H.265+ (~21 GB por cámara al día). Con grabación por movimiento rinde 2 a 3 veces más.",
    "Vista desde el celular: los grabadores sugeridos usan la app gratuita del fabricante; solo necesitás internet en el sitio.",
  ],
};

/** la linea que agrega la v2 de la cotizacion A3 (Laura pide un equipo de respaldo eléctrico) */
export const UPS_V2 = 39;
export const CAMBIO_A3 = "¿Pueden agregar un switch con respaldo de energía para que el grabador siga grabando si se va la luz?";

export const LEMA = "Seguridad electrónica, redes y soporte técnico en Costa Rica.";
export const ROTULO_TARIFAS = "Tarifas de ejemplo: Crimson define las suyas en Mi Tienda. El precio final sale del levantamiento.";
