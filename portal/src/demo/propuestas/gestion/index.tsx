/* Modulo GESTION de la demo (equipo 4): C1 tablero, E1 asistente (guion simulado) y "Detras de escena"
   (pedidos web, inventario web, Mi Tienda). Liviano a proposito: se carga eager en el paquete principal, asi que
   solo trae react, tipos y las pantallas en lazy. Las etiquetas marcan propuestas sobre SUS propias pantallas. */
import { lazy, type ReactNode } from "react";
import type { Etiqueta } from "./comun";

const C1 = lazy(() => import("./C1Tablero"));
const E1 = lazy(() => import("./E1Asistente"));
const Indice = lazy(() => import("./DetrasIndice"));
const Pedidos = lazy(() => import("./Pedidos"));
const Inventario = lazy(() => import("./Inventario"));
const MiTienda = lazy(() => import("./MiTienda"));

export const rutas: { path: string; element: ReactNode }[] = [
  { path: "/propuestas/c1", element: <C1 /> },
  { path: "/propuestas/e1", element: <E1 /> },
  { path: "/detras-de-escena", element: <Indice /> },
  { path: "/detras-de-escena/pedidos", element: <Pedidos /> },
  { path: "/detras-de-escena/inventario", element: <Inventario /> },
  { path: "/detras-de-escena/mi-tienda", element: <MiTienda /> },
];

export const etiquetas: Etiqueta[] = [
  { id: "C1-dde", propuesta: "C1", rutas: ["/detras-de-escena"], selector: ["#ges-enlace-c1"], irA: "/detras-de-escena",
    aqui: "El tablero que dice qué se vende, dónde se cae la compra y cuánto deja cada pedido." },
  { id: "C2-dde", propuesta: "C2", rutas: ["/detras-de-escena/pedidos"], selector: ["#ges-por-atender"], irA: "/detras-de-escena/pedidos",
    aqui: "Cada pedido nuevo o comprobante subido avisaría al celular de quien atiende." },
  { id: "D2-dde", propuesta: "D2", rutas: ["/detras-de-escena/pedidos"], selector: ["#ges-requiere-compra"], irA: "/detras-de-escena/pedidos",
    aqui: "Hoy este «requiere compra» crea una solicitud interna en borrador. Con D2 (y D1), la orden se armaría con los costos de Eurocomp y se enviaría al proveedor con un clic." },
  { id: "D1-dde", propuesta: "D1", rutas: ["/detras-de-escena/inventario"], selector: ["#ges-inv-proveedor"], irA: "/detras-de-escena/inventario",
    aqui: "Esta columna se llenaría sola desde Eurocomp, sin cargar a mano." },
  { id: "C3-dde", propuesta: "C3", rutas: ["/detras-de-escena/inventario"], selector: ["#ges-inv-precio"], irA: "/detras-de-escena/inventario",
    aqui: "Cuando sube el costo, aquí aparecería el precio sugerido que mantiene el margen sobre venta que defina Crimson (35 % en el ejemplo)." },
];
