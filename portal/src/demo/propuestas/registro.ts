/* Registro de la capa de propuestas de la DEMO de la tienda (solo build de la demo: vite.demo.config.ts).
   Fuente unica de: las 17 propuestas (A1…E3, NINGUNA incluida aun), las 16 piezas que YA existen en la plataforma
   (H1…H16), las etapas, las etiquetas que se pintan sobre la tienda real y las claves de almacenamiento/eventos
   que comparten los modulos. Sin dependencias: lo importan main-demo (eager), la capa, _comun y la guia.
   Textos en espanol de Costa Rica; nada de cifras de mercado ni promesas. */

export type PropuestaId = "A1" | "A2" | "A3" | "A4" | "A5" | "B1" | "B2" | "B3" | "B4" | "C1" | "C2" | "C3" | "D1" | "D2" | "E1" | "E2" | "E3";
export type ExistenteId = "H1" | "H2" | "H3" | "H4" | "H5" | "H6" | "H7" | "H8" | "H9" | "H10" | "H11" | "H12" | "H13" | "H14" | "H15" | "H16";
export type MaquetaId = PropuestaId | "DE" | "RT"; // DE = Detras de escena, RT = ¿Es rentable?
export type Familia = "A" | "B" | "C" | "D" | "E";
export type Etapa = 1 | 2 | 3 | 4 | 5;
export type Propuesta = {
  id: PropuestaId; familia: Familia; titulo: string; corto: string /* <= 26 caracteres */; beneficio: string;
  hoy?: string; depende?: string; etapa: Etapa; maqueta?: string; seApoyaEn: ExistenteId[];
};
export type Existente = { id: ExistenteId; titulo: string; detalle: string; verEn?: string; preparar?: "carrito" };
export type Etiqueta = {
  id: string; // unico, p. ej. "A1-ficha"
  propuesta: PropuestaId | ExistenteId;
  rutas: string[]; // patrones (gramatica en _capa/rutas.ts)
  selector: string[]; // gana el primero que exista y sea visible
  aqui: string; // que cambiaria en ese punto exacto
  irA: string; // ruta concreta para «Ver dónde va»
  lado?: "arriba-der" | "arriba-izq" | "abajo-der" | "abajo-izq"; // por defecto "arriba-der"
  preparar?: "carrito"; // sembrar el carrito antes de ir
};

export const FAMILIAS: Record<Familia, string> = {
  A: "Vender más en cada visita",
  B: "Que el cliente vuelva",
  C: "Manejar la tienda con datos",
  D: "Proveedor conectado",
  E: "Confianza y ayuda",
};

export const ETAPAS: { n: Etapa; titulo: string; ids: PropuestaId[]; porque: string; depende?: string }[] = [
  { n: 1, titulo: "Cada venta abre la siguiente", ids: ["B1", "A1", "A3"],
    porque: "B1 crea la cuenta y el registro de equipos que usan el plan, los recordatorios y las reseñas. A1 y A3 venden servicio y proyectos desde la misma tienda.", depende: "Tarifas de instalación que defina Crimson" },
  { n: 2, titulo: "Ingreso que se repite", ids: ["B2", "C1", "E1"],
    porque: "El plan suma ingreso cada mes; el tablero confirma con datos reales si los supuestos se cumplen.", depende: "ONVO para cobrar con tarjeta · servicio del asistente (E1) con costo por uso" },
  { n: 3, titulo: "Recuperar y fidelizar", ids: ["A2", "B3", "B4", "C2"],
    porque: "Con la cuenta y el tablero listos, se automatiza lo que trae ventas de vuelta.", depende: "Canal de mensajería" },
  { n: 4, titulo: "Empresas y paquetes", ids: ["A4", "A5", "E2", "E3"],
    porque: "Precios de empresa, paquetes cerrados y contenido que da confianza.", depende: "Reglas de crédito, fotos y textos de Crimson" },
  { n: 5, titulo: "Proveedor conectado", ids: ["D1", "C3", "D2"],
    porque: "Compras y precios al día con el proveedor.", depende: "Llave de API de Eurocomp" },
];

const P = (p: Omit<Propuesta, "familia">): Propuesta => ({ ...p, familia: p.id[0] as Familia });

export const PROPUESTAS: Propuesta[] = [
  P({ id: "A1", titulo: "Instalación con fecha y precio estimado", corto: "Instalación con fecha",
    beneficio: "El cliente ve cuánto cuesta instalar y aparta fecha desde la ficha; al técnico le llega el levantamiento armado.",
    hoy: "«¿Lo instalamos nosotros?» ya crea la solicitud de asesoría en el CRM.", depende: "Tarifas de instalación que defina Crimson.",
    etapa: 1, maqueta: "/propuestas/a1", seApoyaEn: ["H3", "H11"] }),
  P({ id: "A2", titulo: "Recuperación de carrito abandonado", corto: "Carrito abandonado",
    beneficio: "Quien deja el carrito con su contacto recibe a las 24 h un recordatorio por WhatsApp o correo, con el carrito listo.",
    hoy: "El carrito ya se guarda en el navegador del cliente.", depende: "Canal de mensajería y permiso del cliente para recibir avisos.",
    etapa: 3, seApoyaEn: ["H4"] }),
  P({ id: "A3", titulo: "Cotización formal para empresas", corto: "Cotización para empresas",
    beneficio: "La empresa descarga una cotización en PDF con la marca de Crimson y la aprueba en línea; cada cambio queda como versión.",
    hoy: "La plataforma ya tiene cotizaciones con versiones y aceptación, de uso interno.",
    etapa: 1, maqueta: "/propuestas/a3", seApoyaEn: ["H12", "H1"] }),
  P({ id: "A4", titulo: "Precios por volumen y cuentas de empresa", corto: "Precios por volumen",
    beneficio: "Mejor precio desde cierta cantidad y cuentas de empresa con crédito a 30 días aprobado por Crimson.",
    hoy: "El pago ya pide cédula jurídica para facturar a empresas.", depende: "Escalas de precio y política de crédito que defina Crimson.",
    etapa: 4, seApoyaEn: ["H4", "H9"] }),
  P({ id: "A5", titulo: "Paquetes con instalación incluida", corto: "Paquetes instalados",
    beneficio: "Kits con equipo e instalación a precio cerrado: el cliente sabe el total desde el principio.",
    hoy: "Los kits listos ya existen; la instalación se cotiza aparte.", depende: "Tarifas de A1.",
    etapa: 4, maqueta: "/propuestas/a1?paso=paquete", seApoyaEn: ["H2", "H11"] }),
  P({ id: "B1", titulo: "La compra abre el portal del cliente", corto: "Portal desde la compra",
    beneficio: "Cada compra deja al cliente con cuenta: compras, facturas, equipos con garantía y soporte en un solo lugar.",
    hoy: "El portal del cliente ya existe; el acceso es por solicitud aprobada y las compras de la tienda no aparecen solas.",
    etapa: 1, maqueta: "/propuestas/b1", seApoyaEn: ["H9", "H13", "H6", "H5"] }),
  P({ id: "B2", titulo: "Plan de mantenimiento como suscripción", corto: "Plan de mantenimiento",
    beneficio: "Al comprar cámaras se ofrece un plan mensual: cada sistema vendido puede volverse ingreso que se repite.",
    hoy: "Contratos de mantenimiento y facturación recurrente ya existen; hoy se arman a mano.",
    depende: "Qué incluye y cuánto cuesta lo decide Andrés; el cobro automático con tarjeta requiere contratar ONVO.",
    etapa: 2, maqueta: "/propuestas/b2", seApoyaEn: ["H10", "H9"] }),
  P({ id: "B3", titulo: "Recordatorios automáticos", corto: "Recordatorios",
    beneficio: "Avisos de fin de garantía, revisión del disco del grabador y revisión anual: motivos para volver a Crimson.",
    hoy: "Seriales y equipos con garantía ya existen.", depende: "B1 y un canal de mensajería.",
    etapa: 3, seApoyaEn: ["H13", "H10"] }),
  P({ id: "B4", titulo: "Reseñas verificadas", corto: "Reseñas verificadas",
    beneficio: "Solo opina quien compró: confianza real en cada ficha.",
    hoy: "«Mi pedido» ya identifica cada compra.", depende: "B1.",
    etapa: 3, seApoyaEn: ["H6"] }),
  P({ id: "C1", titulo: "Tablero de la tienda", corto: "Tablero de la tienda",
    beneficio: "Visitas, lo más visto, carritos abandonados, conversión, ventas por canal y margen real por pedido en una pantalla.",
    hoy: "Pedidos e inventario web ya se gestionan; los números se sacan a mano.",
    etapa: 2, maqueta: "/propuestas/c1", seApoyaEn: ["H7", "H8", "H11"] }),
  P({ id: "C2", titulo: "Alertas al celular", corto: "Alertas al celular",
    beneficio: "Aviso inmediato de pedido nuevo, comprobante subido y existencias bajas de lo más vendido.",
    hoy: "Hoy los avisos de la tienda llegan por correo al equipo.", depende: "Canal de mensajería.",
    etapa: 3, seApoyaEn: ["H7", "H8"] }),
  P({ id: "C3", titulo: "Precio sugerido automático", corto: "Precio sugerido",
    beneficio: "Cuando sube el costo del proveedor, la plataforma sugiere el precio que mantiene el margen.",
    hoy: "El costeo con margen sobre venta ya existe en los levantamientos; el precio web se edita en Inventario web.", depende: "D1 o carga de costos.",
    etapa: 5, seApoyaEn: ["H11", "H8"] }),
  P({ id: "D1", titulo: "Conexión con Eurocomp (SiReTT)", corto: "Conexión con Eurocomp",
    beneficio: "Disponibilidad y costos del proveedor al día, sin cargarlos a mano.",
    hoy: "Inventario web ya muestra la columna «Proveedor» con la fecha del dato.", depende: "Llave de API de Eurocomp, que gestiona Andrés.",
    etapa: 5, seApoyaEn: ["H8"] }),
  P({ id: "D2", titulo: "Orden de compra automática", corto: "Compra automática",
    beneficio: "Si un pedido queda «requiere compra», la orden al proveedor se arma sola con los costos de Eurocomp y se envía con un clic.",
    hoy: "Cuando falta existencia, el pedido ya queda «requiere compra» y se crea una solicitud interna de compra en borrador; la orden al proveedor se arma y se envía a mano.", depende: "D1 (sin D1, la orden sale por correo).",
    etapa: 5, seApoyaEn: ["H1", "H7"] }),
  P({ id: "E1", titulo: "Asistente técnico", corto: "Asistente técnico",
    beneficio: "Responde dudas con el catálogo real y pasa la conversación a WhatsApp con una persona de Crimson.",
    hoy: "Las dudas llegan por WhatsApp y se contestan una por una.", depende: "Servicio de asistente con costo por uso, a definir.",
    etapa: 2, maqueta: "/propuestas/e1", seApoyaEn: ["H2"] }),
  P({ id: "E2", titulo: "Casos de éxito", corto: "Casos de éxito",
    beneficio: "Fotos reales de instalaciones, sin nombres de clientes, para dar confianza.",
    depende: "Fotos y permiso de cada cliente.",
    etapa: 4, seApoyaEn: [] }),
  P({ id: "E3", titulo: "Guías y centro de ayuda", corto: "Guías y ayuda",
    beneficio: "Guías para elegir y un centro de ayuda conectado a cada ficha.",
    hoy: "Las preguntas frecuentes básicas ya existen.", depende: "Textos revisados por los técnicos de Crimson.",
    etapa: 4, seApoyaEn: ["H15"] }),
];

export const EXISTENTES: Existente[] = [
  { id: "H1", titulo: "Tienda conectada al inventario real, con reservas atómicas: no vende dos veces la última unidad",
    detalle: "Las existencias de la tienda salen del inventario de Crimson. Cada pedido aparta lo que compra en una sola operación, así que la última unidad nunca se vende dos veces.", verEn: "/catalogo" },
  { id: "H2", titulo: "«Arma tu sistema»",
    detalle: "Cuatro preguntas y la tienda arma un kit con productos reales del catálogo, con precio y disponibilidad, listo para el carrito.", verEn: "/arma-tu-sistema" },
  { id: "H3", titulo: "«¿Lo instalamos nosotros?»: crea la solicitud de asesoría y la oportunidad en el CRM",
    detalle: "Desde la ficha, el cliente pide la instalación con el producto ya identificado; la solicitud entra al CRM como oportunidad y avisa al equipo.", verEn: "/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53" },
  { id: "H4", titulo: "Pago en 4 pasos: SINPE Móvil con comprobante privado, transferencia, tarjeta ONVO (integrada; falta contratar) y efectivo",
    detalle: "Datos, entrega, pago y confirmación. El comprobante de SINPE Móvil solo lo ve el equipo de Crimson; la tarjeta pasa por ONVO, ya integrada, cuando se contrate el servicio.", verEn: "/?v=pagar", preparar: "carrito" },
  { id: "H5", titulo: "Factura electrónica automática al confirmarse el pago",
    detalle: "Al confirmar el pago se emite sola la factura electrónica (o el tiquete, si no hay cédula) y se envía al cliente.", verEn: "/detras-de-escena/pedidos" },
  { id: "H6", titulo: "«Mi pedido» con enlace firmado",
    detalle: "El cliente sigue su pedido con el enlace del correo, sin cuenta ni contraseña; también lo encuentra con su número y su correo.", verEn: "/?pedido=ejemplo-enviado" },
  { id: "H7", titulo: "Gestión de pedidos web: confirmar pago, comprobante, estados, factura y despacho",
    detalle: "Crimson revisa el comprobante, confirma el pago, cambia el estado con aviso al cliente, emite la factura y despacha, todo desde su plataforma.", verEn: "/detras-de-escena/pedidos" },
  { id: "H8", titulo: "Inventario web",
    detalle: "Lo publicado contra el inventario real: disponible para la web, reservado por pedidos, bajo pedido y alertas.", verEn: "/detras-de-escena/inventario" },
  { id: "H9", titulo: "Portal del cliente: registro por solicitud aprobada, roles, reportes con fotos, equipos, mantenimientos, cotizaciones y facturas en PDF, aislamiento probado",
    detalle: "Los clientes de Crimson entran con una cuenta aprobada, con roles de administrador y de usuario. Reportan fallas con fotos y ven sus equipos, mantenimientos, cotizaciones y facturas; cada cliente ve solo lo suyo." },
  { id: "H10", titulo: "Contratos de mantenimiento y facturación recurrente",
    detalle: "Contratos con sus visitas programadas y un cobro que se repite cada periodo, con su factura." },
  { id: "H11", titulo: "Levantamientos técnicos con costeo y margen sobre venta de 35 %",
    detalle: "El técnico levanta el sitio y la plataforma costea equipo y mano de obra; el precio sale con un margen sobre venta de 35 %, que no es lo mismo que un recargo de 35 %." },
  { id: "H12", titulo: "Cotizaciones con versiones y aceptación",
    detalle: "Cada cambio a una cotización queda como versión nueva y el cliente acepta la que corresponde." },
  { id: "H13", titulo: "Seriales y equipos del cliente con garantía",
    detalle: "Cada equipo vendido queda registrado con su serie, su cliente y el fin de su garantía." },
  { id: "H14", titulo: "Bitácora de auditoría",
    detalle: "Los cambios importantes quedan registrados: quién hizo qué y cuándo.", verEn: "/detras-de-escena/pedidos" },
  { id: "H15", titulo: "Posicionamiento en Google: metas en el servidor y redirecciones 301 desde las direcciones de Fygaro",
    detalle: "Cada producto y categoría tiene su dirección, su título y su descripción desde el servidor; las direcciones viejas de Fygaro redirigen con 301 para no perder lo ganado en Google." },
  { id: "H16", titulo: "Mi Tienda: marca, vitrina, páginas, pagos y envíos sin programador",
    detalle: "Crimson cambia colores, titulares, destacados, páginas, formas de pago y tarifas de envío desde su plataforma.", verEn: "/detras-de-escena/mi-tienda" },
];

const FICHA = "/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53";
const PAGO = "/?v=pagar";
const LISTADOS = ["/catalogo", "/c/*", "/buscar"];

/** Marcas sobre la tienda REAL. Selectores verificados contra modules/store (no se toca nada de la tienda). */
export const ETIQUETAS: Etiqueta[] = [
  // ---------- portada
  { id: "E1-buscar", propuesta: "E1", rutas: ["/", ...LISTADOS], selector: [".sh-head__search-m .sh-search", ".sh-head__row > .sh-search"], lado: "abajo-izq", irA: "/",
    aqui: "Junto al buscador, un asistente que entiende preguntas como «¿qué cámara sirve para ver el portón de noche?» y responde con productos de la tienda." },
  { id: "H1-portada", propuesta: "H1", rutas: ["/"], selector: [".vt-hero__facts"], irA: "/",
    aqui: "Ya funciona: las existencias salen del inventario real de Crimson, con reservas; nunca se vende dos veces la última unidad." },
  { id: "A2-carrito", propuesta: "A2", rutas: ["/"], selector: [".sh-cartbtn"], lado: "abajo-izq", irA: "/",
    aqui: "Si alguien deja el carrito con su contacto y no termina, a las 24 h le llega un recordatorio con el carrito listo." },
  { id: "A5-kits", propuesta: "A5", rutas: ["/"], selector: [".vt-kits__note"], irA: "/",
    aqui: "Hoy dice «la instalación se cotiza aparte». Con A5, el kit sale con la instalación incluida, a precio cerrado." },
  { id: "E2-confianza", propuesta: "E2", rutas: ["/"], selector: [".vt-trust"], irA: "/",
    aqui: "Una franja de casos de éxito con fotos reales de instalaciones, sin nombres de clientes." },
  { id: "H2-banda", propuesta: "H2", rutas: ["/"], selector: [".vt-band"], irA: "/",
    aqui: "Ya funciona: cuatro preguntas y un kit con productos reales, precio y disponibilidad." },
  { id: "A3-portada", propuesta: "A3", rutas: ["/"], selector: [".vt-advice"], irA: "/",
    aqui: "Además de pedir asesoría, una empresa podría descargar su cotización formal en PDF y aprobarla en línea." },
  { id: "B1-pie", propuesta: "B1", rutas: ["/"], selector: [".sh-foot .vt-foot__col button"], irA: "/",
    aqui: "Con B1, «Mi pedido» se vuelve una cuenta: todas las compras, facturas y equipos sin buscar por número." },
  // ---------- ficha
  { id: "A1-ficha", propuesta: "A1", rutas: ["/p/*"], selector: [".vt-install"], irA: FICHA,
    aqui: "«¿Lo instalamos nosotros?» pasaría a calcular un rango de precio por zona y lugar y a apartar fecha tentativa; el levantamiento le llega armado al técnico." },
  { id: "A4-ficha", propuesta: "A4", rutas: ["/p/*"], selector: [".vt-buy"], irA: FICHA,
    aqui: "Al subir la cantidad, precio por volumen; y para empresas aprobadas, crédito a 30 días." },
  { id: "A3-ficha", propuesta: "A3", rutas: ["/p/*"], selector: [".vt-buybox__tools"], irA: FICHA,
    aqui: "Una herramienta más: «Descargar cotización formal (PDF)» con este producto, para empresas." },
  { id: "D1-ficha", propuesta: "D1", rutas: ["/p/*"], selector: [".vt-info__price .vt-avail"], irA: FICHA,
    aqui: "«Bajo pedido · 3 a 5 días» saldría de Eurocomp al día, con las existencias reales del proveedor." },
  { id: "B3-ficha", propuesta: "B3", rutas: ["/p/*"], selector: [".vt-assure"], irA: FICHA,
    aqui: "La garantía deja de ser solo un texto: el cliente recibe aviso antes de que venza." },
  { id: "B4-ficha", propuesta: "B4", rutas: ["/p/*"], selector: [".vt-pdp__detail"], irA: FICHA,
    aqui: "Debajo de las especificaciones: reseñas solo de compradores verificados." },
  // ---------- catalogo, categorias y busqueda
  { id: "E3-catalogo", propuesta: "E3", rutas: LISTADOS, selector: [".vt-cathead"], irA: "/catalogo",
    aqui: "Guías para elegir («¿Bullet o domo?», «¿Cuántos días de grabación?») conectadas a cada categoría." },
  { id: "D1-catalogo", propuesta: "D1", rutas: LISTADOS, selector: [".vt-results .vt-avail"], irA: "/catalogo",
    aqui: "La disponibilidad de cada producto saldría de Eurocomp al día." },
  // ---------- arma tu sistema
  { id: "H2-kit", propuesta: "H2", rutas: ["/arma-tu-sistema"], selector: [".vt-q"], irA: "/arma-tu-sistema",
    aqui: "Ya funciona: kit con productos reales, precio y disponibilidad, listo para el carrito." },
  { id: "A1-kit", propuesta: "A1", rutas: ["/arma-tu-sistema"], selector: [".vt-kitres__cta"], irA: "/arma-tu-sistema",
    aqui: "«Quiero que Crimson lo instale» mostraría el precio estimado de la instalación y la fecha tentativa." },
  { id: "A5-kit", propuesta: "A5", rutas: ["/arma-tu-sistema"], selector: [".vt-kitres__sum"], irA: "/arma-tu-sistema",
    aqui: "«Instalación: se cotiza aparte» pasaría a un paquete con instalación incluida, a precio cerrado." },
  { id: "A3-kit", propuesta: "A3", rutas: ["/arma-tu-sistema"], selector: [".vt-kitres__total"], irA: "/arma-tu-sistema",
    aqui: "Para empresas: este kit en una cotización formal en PDF, con aprobación en línea." },
  // ---------- pago
  { id: "H4-pago", propuesta: "H4", rutas: ["?v=pagar"], selector: [".ck-steps"], irA: PAGO, preparar: "carrito",
    aqui: "Ya funciona: pago en 4 pasos con SINPE Móvil y comprobante privado, transferencia, tarjeta (ONVO integrada; falta contratar) y efectivo." },
  { id: "A2-pago", propuesta: "A2", rutas: ["?v=pagar"], selector: [".ck-steps"], lado: "abajo-der", irA: PAGO, preparar: "carrito",
    aqui: "Si el cliente deja su correo o WhatsApp en el paso 1 y no termina, a las 24 h recibe su carrito listo para retomar." },
  { id: "B2-pago", propuesta: "B2", rutas: ["?v=pagar"], selector: [".ck-aside", ".ck-mini"], irA: PAGO, preparar: "carrito",
    aqui: "Si el carrito trae cámaras, aquí se ofrecería el plan de mantenimiento mensual." },
  { id: "A4-pago", propuesta: "A4", rutas: ["?v=pagar"], selector: [".ck-seg"], irA: PAGO, preparar: "carrito",
    aqui: "Con cédula jurídica, la empresa podría pagar con su cuenta de crédito a 30 días." },
  // ---------- mi pedido
  { id: "B1-pedido", propuesta: "B1", rutas: ["?pedido=*"], selector: [".ck-thanks", ".ck-ohead"], irA: "/?pedido=ejemplo-entregado", // arriba: abajo taparia el estado del pedido
    aqui: "Con la compra se crearía la cuenta del cliente: este pedido quedaría en «Mis compras», con su factura, XML y equipos con garantía." },
  { id: "H6-pedido", propuesta: "H6", rutas: ["?pedido=*"], selector: [".ck-track"], irA: "/?pedido=ejemplo-enviado",
    aqui: "Ya funciona: el cliente sigue su pedido con un enlace firmado, sin cuenta ni contraseña." },
  { id: "H5-pedido", propuesta: "H5", rutas: ["?pedido=*"], selector: [".ck-totals"], irA: "/?pedido=ejemplo-pagado",
    aqui: "Ya funciona: al confirmarse el pago, la factura electrónica sale sola." },
  { id: "B3-pedido", propuesta: "B3", rutas: ["?pedido=*"], selector: [".ck-info"], irA: "/?pedido=ejemplo-entregado",
    aqui: "Después de la entrega: avisos de revisión del disco del grabador y de fin de garantía." },
  { id: "B4-pedido", propuesta: "B4", rutas: ["?pedido=*"], selector: [".ck-olines"], irA: "/?pedido=ejemplo-entregado",
    aqui: "Después de la entrega, el cliente podría dejar una reseña verificada de lo que compró." },
  { id: "B1-buscar", propuesta: "B1", rutas: ["?v=mi-pedido"], selector: [".ck-lookup h1"], irA: "/?v=mi-pedido",
    aqui: "Con cuenta, el cliente vería todas sus compras sin escribir número y correo." },
  // ---------- asesoria e informacion
  { id: "H3-asesoria", propuesta: "H3", rutas: ["/asesoria"], selector: [".vt-advform"], irA: "/asesoria",
    aqui: "Ya funciona: esta solicitud crea la oportunidad en el CRM y avisa al equipo." },
  { id: "A1-asesoria", propuesta: "A1", rutas: ["/asesoria"], selector: [".vt-advpage__intro"], irA: "/asesoria",
    aqui: "Para instalaciones, el cliente vería el rango de precio y elegiría fecha antes de enviar." },
  { id: "A3-asesoria", propuesta: "A3", rutas: ["/asesoria"], selector: [".vt-solutions"], irA: "/asesoria",
    aqui: "Para proyectos de empresa: cotización formal en PDF con aprobación en línea." },
  { id: "E3-info", propuesta: "E3", rutas: ["/info/*"], selector: [".vt-faq", ".vt-infopage__body"], irA: "/info/preguntas-frecuentes",
    aqui: "Un centro de ayuda conectado a cada ficha: preguntas por producto y guías paso a paso." },
  { id: "E1-info", propuesta: "E1", rutas: ["/info/*"], selector: [".vt-infonav"], irA: "/info/preguntas-frecuentes",
    aqui: "Si la respuesta no está aquí, el asistente la busca en el catálogo o pasa con una persona." },
];

/** Recorrido de maquetas (anterior / siguiente). */
export const ORDEN_MAQUETAS: MaquetaId[] = ["A1", "A3", "B1", "B2", "C1", "E1", "RT"];

export const CLAVES = {
  interes: "crimson-demo:interes",
  notas: "crimson-demo:notas",
  capa: "crimson-demo:capa",
  capaExistentes: "crimson-demo:capa-existentes",
  foco: "crimson-demo:capa-foco",
  pista: "crimson-demo:capa-pista",
  ultimaRuta: "crimson-demo:ultima-ruta",
  pedidos: "crimson-demo:pedidos",
  estados: "crimson-demo:estados",
  vitrina: "crimson-demo:vitrina",
  planB2: "crimson-demo:b2:plan",
  rentabilidad: "crimson-demo:rentabilidad",
  rentabilidadResumen: "crimson-demo:rentabilidad-resumen",
  etapa0: "crimson-demo:etapa0", // «Me interesa esta etapa» de la Etapa 0 (la tienda propia): "1"/"0"
  carrito: "crimson-shop:crimson",
} as const;

export const EVENTOS = {
  aviso: "crimson-demo:aviso",
  interes: "crimson-demo:interes",
  capa: "crimson-demo:capa",
  recargarTienda: "crimson-demo:recargar-tienda",
  tourCerrar: "crimson-demo:tour-cerrar",
} as const;

export function porId(id: string): Propuesta | Existente | undefined {
  return PROPUESTAS.find((p) => p.id === id) || EXISTENTES.find((h) => h.id === id);
}

export const esPropuestaId = (id: string): id is PropuestaId => PROPUESTAS.some((p) => p.id === id);

/** Titulo, rotulo corto y ruta de cada maqueta (navegacion entre maquetas). */
export function infoMaqueta(id: MaquetaId): { titulo: string; corto: string; ruta: string } {
  if (id === "RT") return { titulo: "¿Es rentable?", corto: "¿Es rentable?", ruta: "/rentabilidad" };
  if (id === "DE") return { titulo: "Detrás de escena", corto: "Detrás de escena", ruta: "/detras-de-escena" };
  const p = PROPUESTAS.find((x) => x.id === id);
  return { titulo: p?.titulo || id, corto: p?.corto || id, ruta: p?.maqueta || "/propuestas" };
}
