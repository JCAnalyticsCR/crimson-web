/* Modulo «Lo que viene» (capa). Liviano: solo la ruta y la pantalla en lazy. */
import { lazy, type ReactNode } from "react";

const LoQueViene = lazy(() => import("./LoQueViene"));

export const rutas: { path: string; element: ReactNode }[] = [
  { path: "/propuestas", element: <LoQueViene /> },
];
