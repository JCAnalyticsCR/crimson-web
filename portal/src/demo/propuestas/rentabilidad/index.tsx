import { lazy, type ReactNode } from "react";

const Rentabilidad = lazy(() => import("./Rentabilidad"));

export const rutas: { path: string; element: ReactNode }[] = [{ path: "/rentabilidad", element: <Rentabilidad /> }];
