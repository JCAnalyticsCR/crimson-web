/* Datos canonicos del equipo venta (seccion 3.0 de la especificacion). Productos REALES del snapshot de la demo:
   precio con IVA = usd x 512 x 1,13 (redondeo a centimos igual que catalog.ts). Personas, empresa y numeros de
   documento son de EJEMPLO. No se importan de otro equipo: si cambian, se cambian aqui. */

export type ProductoVenta = {
  id: number; nombre: string; codigo: string; marca: string; sinIva: number; conIva: number; disponibilidad: string;
};

export const PRODUCTOS: Record<number, ProductoVenta> = {
  53: { id: 53, nombre: "Camara IP Hikvision 4MP Acusense Bullet IP67", codigo: "DS2CD2043G2LI2U", marca: "Hikvision", sinIva: 55603.2, conIva: 62831.62, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  118: { id: 118, nombre: "Camara Tipo Domo Hikvision 4MP Acusense Smart Hybrid Lght", codigo: "DS-2CD2143G2-LIS2U(2.8mm)", marca: "Hikvision", sinIva: 61327.36, conIva: 69299.92, disponibilidad: "Disponible" },
  177: { id: 177, nombre: "Grabador IP NVR Hikvision 4CH PoE", codigo: "DS-7104NI-Q1/4P", marca: "Hikvision", sinIva: 52684.8, conIva: 59533.82, disponibilidad: "Disponible" },
  93: { id: 93, nombre: "Grabador Hikvision NVR 8ch 4MP H.265+ 1 SATA PoE", codigo: "DS-7108NI-Q1/8P", marca: "Hikvision", sinIva: 64000, conIva: 72320, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  11: { id: 11, nombre: "Disco duro 2TB Western Digital Sata Purple", codigo: "CRWD23PURZ", marca: "Western Digital", sinIva: 43765.76, conIva: 49455.31, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  12: { id: 12, nombre: "Disco duro 6TB Western Digital Sata Purple", codigo: "CRWD64PURZ", marca: "Western Digital", sinIva: 121687.04, conIva: 137506.36, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  92: { id: 92, nombre: "CABLE U/UTP CAT5E HIKVISION 305 METROS", codigo: "DS-1LN5EUEC0", marca: "Hikvision", sinIva: 40673.28, conIva: 45960.81, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
  318: { id: 318, nombre: "CAJA CUADRADA EXTERIOR FS TEKLINK-T 3 ENTRADAS 1/2 PULGADA INCLUYE 2 TAPONES DE CIERRE 2G50-3", codigo: "2G50-3", marca: "Teklink-T", sinIva: 3584, conIva: 4049.92, disponibilidad: "Disponible" },
  108: { id: 108, nombre: "UPS APC BACK-UPS 1600VA / 960W 120V AVR", codigo: "BX1600M-LM", marca: "APC", sinIva: 97280, conIva: 109926.4, disponibilidad: "Bajo pedido · 3 a 5 días hábiles" },
};

/** ficha real de la tienda que replica A1 */
export const FICHA_A1 = { id: 53, ruta: "/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53", direccion: "tienda.crimsoncr.com/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53" };

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

/** "Casa segura · 4 cámaras": 2 adentro, 2 afuera, 15 dias, celular. Total real de la portada. */
export const KIT_CASA = {
  titulo: "Casa segura · 4 cámaras",
  texto: "2 adentro, 2 afuera, 15 días de grabación y vista desde el celular.",
  total: 427313,
  articulos: 9,
  lineas: [
    { id: 53, cant: 2, rol: "Cámaras exteriores", razon: "2 punto(s) afuera: formato bullet, resistente al agua" },
    { id: 118, cant: 2, rol: "Cámaras interiores", razon: "2 punto(s) adentro: formato domo, discreto" },
    { id: 177, cant: 1, rol: "Grabador", razon: "4 cámara(s): nvr 4 canales con PoE integrado (no hace falta switch)" },
    { id: 11, cant: 1, rol: "Almacenamiento", razon: "15 días × 4 cámara(s) ≈ 1.4 TB en grabación continua" },
    { id: 92, cant: 1, rol: "Instalación", razon: "92 m de cable UTP (20 m por cámara + 15 %)" },
    { id: 318, cant: 2, rol: "Instalación", razon: "cámaras en exterior: C1, C2" },
  ] as LineaKit[],
  faltantes: [{ nombre: "Conectores RJ45", cant: 8, rol: "Instalación", razon: "2 por cámara (4 cámaras)" }] as FaltanteKit[],
};

/** "Negocio u oficina · 8 cámaras": 4 adentro, 4 afuera, 30 dias, celular. Total ₡800 513 (disco de 6 TB). */
export const KIT_NEGOCIO = {
  titulo: "Negocio u oficina · 8 cámaras",
  camaras: 8,
  tb: "5.5",
  total: 800513,
  respuestas: { place: "negocio", indoor: 4, outdoor: 4, days: 30, remote: true },
  lineas: [
    { id: 53, cant: 4, rol: "Cámaras exteriores", razon: "4 punto(s) afuera: formato bullet, resistente al agua" },
    { id: 118, cant: 4, rol: "Cámaras interiores", razon: "4 punto(s) adentro: formato domo, discreto" },
    { id: 93, cant: 1, rol: "Grabador", razon: "8 cámara(s): nvr 8 canales con PoE integrado (no hace falta switch)" },
    { id: 12, cant: 1, rol: "Almacenamiento", razon: "30 días × 8 cámara(s) ≈ 5.5 TB en grabación continua" },
    { id: 92, cant: 1, rol: "Instalación", razon: "276 m de cable UTP (30 m por cámara + 15 %)" },
    { id: 318, cant: 4, rol: "Instalación", razon: "cámaras en exterior: C1, C2, C3, C4" },
  ] as LineaKit[],
  faltantes: [{ nombre: "Conectores RJ45", cant: 16, rol: "Instalación", razon: "2 por cámara (8 cámaras)" }] as FaltanteKit[],
  notas: [
    "Kit sugerido: un técnico de Crimson lo valida antes de cobrar o instalar (distancias, alturas y red del sitio).",
    "Almacenamiento estimado con grabación continua a 4 MP en H.265+ (~21 GB por cámara al día). Con grabación por movimiento rinde 2 a 3 veces más.",
    "Vista desde el celular: los grabadores sugeridos usan la app gratuita del fabricante; solo necesitás internet en el sitio.",
  ],
};

/** la linea que agrega la v2 de la cotizacion A3 (Laura pide una UPS) */
export const UPS_V2 = 108;
export const CAMBIO_A3 = "¿Pueden agregar una UPS para que el grabador siga grabando si se va la luz?";

export const LEMA = "Seguridad electrónica, redes y soporte técnico en Costa Rica.";
export const ROTULO_TARIFAS = "Tarifas de ejemplo: Crimson define las suyas en Mi Tienda. El precio final sale del levantamiento.";
