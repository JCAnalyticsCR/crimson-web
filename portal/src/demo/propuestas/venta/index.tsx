/* Modulo del equipo venta: maquetas A1 (con A5 como paso final) y A3. Liviano: se carga eager en el paquete
   principal, asi que solo trae react y las pantallas en lazy (cada una importa su CSS y sus datos). */
import { lazy, type ReactNode } from "react";

const A1 = lazy(() => import("./A1Instalacion"));
const A3 = lazy(() => import("./A3Cotizacion"));

export const rutas: { path: string; element: ReactNode }[] = [
  { path: "/propuestas/a1", element: <A1 /> },
  { path: "/propuestas/a3", element: <A3 /> },
];
