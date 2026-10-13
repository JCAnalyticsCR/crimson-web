/* API comun de las maquetas (contrato de la especificacion, seccion 1.6). Cada equipo importa SOLO desde su
   ./comun.ts, que al integrar queda en una linea: export * from "../_comun";
   Reexportaciones explicitas: nada fuera del contrato se filtra a los equipos. */
export type { PropuestaId, ExistenteId, MaquetaId, Etapa, Etiqueta, Propuesta, Existente } from "../registro";
export { CLAVES, EVENTOS } from "../registro";

export { SelloPropuesta, MarcoMaqueta, NavPropuestas, Pantalla, BotonMeInteresa, HiloCarmesi, BarraMonitor } from "./componentes";
export type { Paso, MarcoMaquetaProps, Superficie, SeccionAdmin, Estacion } from "./componentes";

export { usePaso, useInteres, useProducto } from "./hooks";
export type { ProductoDemo } from "./hooks";

export { leerPedidosDemo, escribirEstadoDemo, almacen } from "./pedidos";
export type { EstadoDemo, PedidoDemo } from "./pedidos";

export { colones, colones2, porcentaje, fecha, hora, desdeAhora } from "./formato";
