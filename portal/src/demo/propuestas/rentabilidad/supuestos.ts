/* Supuestos de «¿Es rentable?»: rótulos, unidades, insignias, rangos y los textos «¿Por qué este ejemplo?».
   Los VALORES viven en modelo.ts (BASE_EJEMPLO, ESCENARIO_EJEMPLO, INICIO_EJEMPLO) para que el modelo se pueda
   probar con Node sin importar nada. Ningún supuesto se presenta como dato de mercado. */
import type { PropuestaId } from "./comun";
import type { ClaveBase, ClaveEscenario, EscenarioId } from "./modelo";

export type Unidad = "colones" | "porcentaje" | "horas" | "cantidad" | "meses" | "mes";
/** EJEMPLO (supuesto editable) · DATO DE CRIMSON (real) · TU DATO (vacío hasta que lo escribás). */
export type TipoInsignia = "ejemplo" | "crimson" | "tu-dato";

export type Campo<K extends string> = {
  clave: K; rotulo: string; unidad: Unidad; insignia: TipoInsignia; porque: string; min: number; max: number; corto: string;
};

const PLATA = 1_000_000_000;

export const CAMPOS_BASE: Record<ClaveBase, Campo<ClaveBase>> = {
  P0: { clave: "P0", corto: "pedidos al mes", rotulo: "Pedidos en línea por mes, hoy", unidad: "cantidad", insignia: "ejemplo", min: 0, max: 100_000,
    porque: "Número redondo para mostrar la cuenta. Escribí cuántos pedidos te entran hoy por Fygaro en un mes normal." },
  T: { clave: "T", corto: "monto promedio", rotulo: "Monto promedio por pedido, sin IVA", unidad: "colones", insignia: "ejemplo", min: 0, max: PLATA,
    porque: "En el catálogo, una cámara IP ronda ₡55 600 sin IVA y el kit «Casa segura · 4 cámaras» ₡378 153 sin IVA. Usá tu promedio real." },
  pc: { clave: "pc", corto: "pedidos con cámaras", rotulo: "Pedidos que llevan cámaras o equipo que se instala", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "Ejemplo: la mitad de los pedidos." },
  Hh: { clave: "Hh", corto: "horas manuales", rotulo: "Horas al mes en tareas manuales de la tienda", unidad: "horas", insignia: "ejemplo", min: 0, max: 744,
    porque: "Cargar existencias y precios en Fygaro, pasar pedidos a facturación, revisar comprobantes, contestar «¿cómo va mi pedido?». Medí las tuyas." },
  ch: { clave: "ch", corto: "costo de la hora", rotulo: "Costo de una hora de ese trabajo", unidad: "colones", insignia: "ejemplo", min: 0, max: 10_000_000,
    porque: "Con cargas sociales. Usá el tuyo." },
  K: { clave: "K", corto: "carritos con contacto", rotulo: "Carritos abandonados por mes que dejaron correo o WhatsApp", unidad: "cantidad", insignia: "ejemplo", min: 0, max: 100_000,
    porque: "Hoy no se mide; el tablero (C1) lo contaría desde el primer mes." },
  Tr: { clave: "Tr", corto: "monto de una recompra", rotulo: "Monto promedio de una recompra, sin IVA", unidad: "colones", insignia: "ejemplo", min: 0, max: PLATA,
    porque: "Un disco de 2 TB cuesta ₡43 766 sin IVA en el catálogo." },
  m: { clave: "m", corto: "margen de equipo", rotulo: "Margen sobre venta de equipo de tienda", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "35 % es el margen sobre venta que Crimson confirmó para sus levantamientos (proyectos con costeo); el del equipo que se vende en la tienda puede ser otro: escribí el tuyo. Margen sobre venta, no recargo: un recargo de 35 % sobre el costo deja 25,9 % sobre venta." },
  mi: { clave: "mi", corto: "margen de instalación", rotulo: "Margen sobre venta de instalación", unidad: "porcentaje", insignia: "crimson", min: 0, max: 100,
    porque: "Es el margen sobre venta que Crimson confirmó para sus levantamientos: de cada ₡100 de instalación sin IVA quedan ₡35." },
  mp: { clave: "mp", corto: "margen del plan", rotulo: "Margen del plan de mantenimiento", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "Misma regla; el costo real de las visitas lo define Crimson." },
  I: { clave: "I", corto: "precio de instalación", rotulo: "Precio promedio de una instalación, sin IVA", unidad: "colones", insignia: "ejemplo", min: 0, max: PLATA,
    porque: "Con las tarifas de ejemplo de A1, 4 cámaras en casa salen entre ₡145 000 y ₡174 000 sin IVA." },
  c: { clave: "c", corto: "cuota del plan", rotulo: "Cuota mensual del plan, sin IVA", unidad: "colones", insignia: "ejemplo", min: 0, max: 100_000_000,
    porque: "Lo decide Andrés; es el mismo ejemplo de la maqueta B2." },
  Fm: { clave: "Fm", corto: "mensualidad de Fygaro", rotulo: "Mensualidad de Fygaro", unidad: "colones", insignia: "tu-dato", min: 0, max: PLATA,
    porque: "Lo que pagás hoy. Vacío = no se cuenta ese ahorro." },
  cF: { clave: "cF", corto: "comisión de Fygaro", rotulo: "Comisión de Fygaro sobre ventas", unidad: "porcentaje", insignia: "tu-dato", min: 0, max: 100,
    porque: "Solo la de Fygaro; la del banco o la pasarela de tarjeta se paga igual con tienda propia." },
  Cop: { clave: "Cop", corto: "costo de operar", rotulo: "Costo mensual de operar la tienda propia", unidad: "colones", insignia: "tu-dato", min: 0, max: PLATA,
    porque: "Servidor, soporte y mejoras: lo define la propuesta de JC Analytics." },
};

export const CAMPOS_ESCENARIO: Campo<ClaveEscenario>[] = [
  { clave: "u", corto: "aumento de ventas", rotulo: "Aumento de ventas en línea por tener tienda propia", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 500,
    porque: "Fichas completas, «Arma tu sistema» y Google. No es un dato de mercado: es la palanca para ver qué pasa si sube. El tablero (C1) lo mediría." },
  { clave: "ti", corto: "instalaciones adicionales", rotulo: "De los pedidos con cámaras, cuántos agregan una instalación que hoy no entra (A1)", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "Solo las instalaciones adicionales a las que ya entran hoy por «¿Lo instalamos nosotros?»: las que se suman cuando el cliente ve precio y fecha desde la ficha." },
  { clave: "tp", corto: "toman el plan", rotulo: "De los pedidos con cámaras, cuántos toman el plan (B2)", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "De cada 100 pedidos con cámaras, cuántos suman el plan en el pago. No es un dato de mercado: es la palanca para ver cuánto pesa el ingreso que se repite." },
  { clave: "b", corto: "bajas del plan", rotulo: "Planes que se dan de baja cada mes", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "De cada 100 planes, cuántos se cancelan en un mes." },
  { clave: "rr", corto: "carritos que vuelven", rotulo: "Carritos con contacto que vuelven con el recordatorio (A2)", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "De cada 100 carritos con contacto, cuántos terminan la compra después del recordatorio. El tablero (C1) lo mediría desde el primer mes." },
  { clave: "ra", corto: "recompra extra", rotulo: "Aumento de recompra gracias a B1, por encima de lo que ya vuelve hoy (B1)", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "De cada 100 compradores con cuenta, cuántas compras más al año hacen gracias a «Volver a comprar» y «Mis equipos» (discos, cámaras extra, accesorios). Solo lo que se suma a lo que ya vuelve hoy por Fygaro o WhatsApp; los que compraron hace más de un año dejan de contar." },
  { clave: "he", corto: "horas que hace la plataforma", rotulo: "Parte de esas horas que hace la plataforma sola", unidad: "porcentaje", insignia: "ejemplo", min: 0, max: 100,
    porque: "Factura automática, inventario conectado, comprobantes en un solo lugar y «Mi pedido»." },
  { clave: "R", corto: "meses de rampa", rotulo: "Meses hasta que cada etapa rinde completo", unidad: "meses", insignia: "ejemplo", min: 1, max: 36,
    porque: "Al arrancar una etapa, su efecto sube parejo hasta llegar al 100 %." },
];
export const CAMPO_ESCENARIO = Object.fromEntries(CAMPOS_ESCENARIO.map((c) => [c.clave, c])) as Record<ClaveEscenario, Campo<ClaveEscenario>>;

export type Grupo = { id: "tienda" | "margenes" | "escenario" | "precios" | "costos" | "calendario"; titulo: string; bajada: string; claves: ClaveBase[] };
export const GRUPOS: Grupo[] = [
  { id: "tienda", titulo: "Tu tienda hoy", bajada: "Cómo vende hoy la tienda en línea. Todos son ejemplos: cambialos por los tuyos.", claves: ["P0", "T", "pc", "Hh", "ch", "K", "Tr"] },
  { id: "margenes", titulo: "Márgenes", bajada: "Margen sobre venta: de cada ₡100 vendidos sin IVA, cuánto queda después del costo.", claves: ["m", "mi", "mp"] },
  { id: "escenario", titulo: "Lo que cambia con la tienda propia y las propuestas", bajada: "Lo único que cambia entre los tres escenarios. Tus cambios se guardan por escenario.", claves: [] },
  { id: "precios", titulo: "Precios que decide Crimson", bajada: "Montos de ejemplo hasta que Crimson fije sus tarifas.", claves: ["I", "c"] },
  { id: "costos", titulo: "Costos que faltan", bajada: "Lo que no sabemos queda vacío hasta que lo escribás. No sugerimos montos.", claves: ["Fm", "cF", "Cop"] },
  { id: "calendario", titulo: "Calendario y etapas", bajada: "Calendario de ejemplo; el real lo fija la propuesta.", claves: [] },
];

export const ESCENARIO_INFO: Record<EscenarioId, { nombre: string; texto: string }> = {
  conservador: { nombre: "Conservador", texto: "La tienda tarda 6 meses en tomar ritmo y pocos clientes usan lo nuevo." },
  medio: { nombre: "Medio", texto: "Ritmo en 4 meses y uso moderado." },
  optimista: { nombre: "Optimista", texto: "Ritmo en 3 meses y buena adopción." },
};

/** Lo que cada propuesta necesita mostrar aquí (copia del registro: no se importa entre equipos). */
export const PROPUESTAS_RNT: Record<PropuestaId, { corto: string; titulo: string; maqueta?: string }> = {
  A1: { corto: "Instalación con fecha", titulo: "Instalación con fecha y precio estimado", maqueta: "/propuestas/a1" },
  A2: { corto: "Carrito abandonado", titulo: "Recuperación de carrito abandonado" },
  A3: { corto: "Cotización para empresas", titulo: "Cotización formal para empresas", maqueta: "/propuestas/a3" },
  A4: { corto: "Precios por volumen", titulo: "Precios por volumen y cuentas de empresa" },
  A5: { corto: "Paquetes instalados", titulo: "Paquetes con instalación incluida", maqueta: "/propuestas/a1?paso=paquete" },
  B1: { corto: "Portal desde la compra", titulo: "La compra abre el portal del cliente", maqueta: "/propuestas/b1" },
  B2: { corto: "Plan de mantenimiento", titulo: "Plan de mantenimiento como suscripción", maqueta: "/propuestas/b2" },
  B3: { corto: "Recordatorios", titulo: "Recordatorios automáticos" },
  B4: { corto: "Reseñas verificadas", titulo: "Reseñas verificadas" },
  C1: { corto: "Tablero de la tienda", titulo: "Tablero de la tienda", maqueta: "/propuestas/c1" },
  C2: { corto: "Alertas al celular", titulo: "Alertas al celular" },
  C3: { corto: "Precio sugerido", titulo: "Precio sugerido automático" },
  D1: { corto: "Conexión con Eurocomp", titulo: "Conexión con Eurocomp (SiReTT)" },
  D2: { corto: "Compra automática", titulo: "Orden de compra automática" },
  E1: { corto: "Asistente técnico", titulo: "Asistente técnico", maqueta: "/propuestas/e1" },
  E2: { corto: "Casos de éxito", titulo: "Casos de éxito" },
  E3: { corto: "Guías y ayuda", titulo: "Guías y centro de ayuda" },
};

export type EtapaRnt = { n: 0 | 1 | 2 | 3 | 4 | 5; titulo: string; corto: string; ids: PropuestaId[]; modela: string; nota?: string; depende?: string; conCifra: boolean };

/** Las seis tarjetas del cierre «Aceptar por etapas» (6.8) y del calendario. */
export const ETAPAS_RNT: EtapaRnt[] = [
  { n: 0, titulo: "La tienda propia: dejar Fygaro (lo que ya viste funcionando)", corto: "La tienda propia", ids: [], conCifra: true,
    modela: "Ventas incrementales, horas y Fygaro." },
  { n: 1, titulo: "Cada venta abre la siguiente", corto: "Cada venta abre la siguiente", ids: ["B1", "A1", "A3"], conCifra: true, depende: "Tarifas de instalación que defina Crimson",
    modela: "Instalaciones (A1) y recompras (B1).", nota: "A3 no lleva cifra propia." },
  { n: 2, titulo: "Ingreso que se repite", corto: "Ingreso que se repite", ids: ["B2", "C1", "E1"], conCifra: true, depende: "ONVO para cobrar con tarjeta · servicio del asistente (E1) con costo por uso",
    modela: "Plan de mantenimiento (B2).",
    nota: "Arranca chico y crece cada mes: es ingreso que se repite. C1 y E1 no llevan cifra propia: C1 es el tablero que va a decir si estos supuestos se cumplen." },
  { n: 3, titulo: "Recuperar y fidelizar", corto: "Recuperar y fidelizar", ids: ["A2", "B3", "B4", "C2"], conCifra: true, depende: "Canal de mensajería",
    modela: "Carritos recuperados (A2).", nota: "B3, B4 y C2 no llevan cifra: no las inflamos." },
  { n: 4, titulo: "Empresas y paquetes", corto: "Empresas y paquetes", ids: ["A4", "A5", "E2", "E3"], conCifra: false, depende: "Reglas de crédito, fotos y textos de Crimson",
    modela: "Sin cifra propia: su valor depende de datos que hoy no tenemos." },
  { n: 5, titulo: "Proveedor conectado", corto: "Proveedor conectado", ids: ["D1", "C3", "D2"], conCifra: false, depende: "Llave de API de Eurocomp",
    modela: "Sin cifra propia: su valor depende de datos que hoy no tenemos." },
];

/** Las fuentes de valor del modelo, en el orden del gráfico 2. tono = color de su barra. */
export type FuenteId = "S1" | "S2" | "S5" | "S3" | "S4" | "S7" | "S6";
export const FUENTES: { id: FuenteId; nombre: string; etapa: 0 | 1 | 2 | 3; propuesta?: PropuestaId; color: string; mueven: (ClaveBase | ClaveEscenario)[] }[] = [
  // Ordenadas por etapa. Fygaro va junto a las ventas (las dos son Etapa 0): así el ocre nunca queda al lado del gris
  // de la Etapa 3 (el validador de color los da casi iguales a simple vista). Cada barra lleva además su nombre y su etapa.
  { id: "S1", nombre: "Ventas incrementales (tienda propia)", etapa: 0, color: "#15131a", mueven: ["u", "P0", "T", "m"] },
  { id: "S7", nombre: "Fygaro", etapa: 0, color: "#8a5208", mueven: ["Fm", "cF"] },
  { id: "S2", nombre: "Instalaciones (A1)", etapa: 1, propuesta: "A1", color: "#c41a2f", mueven: ["ti", "I", "mi", "pc"] },
  { id: "S5", nombre: "Recompras (B1)", etapa: 1, propuesta: "B1", color: "#c41a2f", mueven: ["ra", "Tr", "m"] },
  { id: "S3", nombre: "Plan de mantenimiento (B2)", etapa: 2, propuesta: "B2", color: "#8a1424", mueven: ["tp", "c", "b", "mp"] },
  { id: "S4", nombre: "Carritos recuperados (A2)", etapa: 3, propuesta: "A2", color: "#6f6a74", mueven: ["K", "rr", "T"] },
  { id: "S6", nombre: "Horas liberadas", etapa: 0, color: "#6f6a74", mueven: ["Hh", "he", "ch"] },
];

/** ?enfoque= → id del bloque a resaltar. */
export const ENFOQUES: Record<string, { destino: string; texto: string }> = {
  A1: { destino: "rnt-fuente-S2", texto: "Viniste desde la maqueta A1: esta es la cuenta de las instalaciones." },
  B1: { destino: "rnt-fuente-S5", texto: "Viniste desde la maqueta B1: esta es la cuenta de las recompras." },
  B2: { destino: "rnt-fuente-S3", texto: "Viniste desde la maqueta B2: esta es la cuenta del plan de mantenimiento." },
  A2: { destino: "rnt-fuente-S4", texto: "Esta es la cuenta de los carritos recuperados (A2)." },
  horas: { destino: "rnt-fuente-S6", texto: "Esta es la cuenta de las horas liberadas." },
  fygaro: { destino: "rnt-fuente-S7", texto: "Esta es la cuenta de lo que se deja de pagar a Fygaro." },
  etapa0: { destino: "rnt-etapa-0", texto: "Esta es la etapa que preguntaste." },
  etapa1: { destino: "rnt-etapa-1", texto: "Esta es la etapa que preguntaste." },
  etapa2: { destino: "rnt-etapa-2", texto: "Esta es la etapa que preguntaste." },
  etapa3: { destino: "rnt-etapa-3", texto: "Esta es la etapa que preguntaste." },
  etapa4: { destino: "rnt-etapa-4", texto: "Esta es la etapa que preguntaste." },
  etapa5: { destino: "rnt-etapa-5", texto: "Esta es la etapa que preguntaste." },
};
