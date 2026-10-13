/* Modulo «cliente» de las propuestas (maquetas B1 y B2). Liviano a proposito: se carga eager en el paquete
   principal de la demo, asi que solo trae react y las pantallas en lazy (cada una importa su propio CSS). */
import { lazy, type ReactNode } from "react";

const B1 = lazy(() => import("./B1Portal"));
const B2 = lazy(() => import("./B2Plan"));

export const rutas: { path: string; element: ReactNode }[] = [
  { path: "/propuestas/b1", element: <B1 /> },
  { path: "/propuestas/b2", element: <B2 /> },
];
