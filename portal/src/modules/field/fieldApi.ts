/* Rutas del trabajo de campo según quién lo usa: el panel interno (/surveys, /work-orders...) o el portal de
   aliados (/aliado/levantamientos, /aliado/ordenes...). El portal de aliados toma las suyas de ALLY_FIELD_API; fuera
   de ese contexto nada cambia (el valor por defecto es INTERNAL_FIELD_API). La API del aliado aplica su propio
   alcance y listas blancas: aquí solo se eligen rutas. */
import { createContext, useContext } from "react";

export type FieldApi = {
  mode: "interno" | "aliado";
  surveys: string; survey: (id: number) => string; createSurvey: string; saveSurvey: (id: number) => string;
  sendSurvey: (id: number) => string; resolveObs: (sid: number, oid: number) => string;
  specs: string; specsCommon: string; system: (id: number) => string; technicians: string;
  orders: string; order: (id: number) => string; orderAction: (id: number, a: string) => string; today: string;
  productSearch: string; mediaUpload: string;
};

export const INTERNAL_FIELD_API: FieldApi = {
  mode: "interno",
  surveys: "/surveys",
  survey: (id) => `/surveys/${id}`,
  createSurvey: "/surveys",
  saveSurvey: (id) => `/surveys/${id}`,
  sendSurvey: (id) => `/surveys/${id}/send`,
  resolveObs: (s, o) => `/surveys/${s}/observations/${o}`,
  specs: "/field/specs",
  specsCommon: "/field/specs/common",
  system: (id) => `/surveys/${id}/system`,
  technicians: "/field/technicians",
  orders: "/work-orders",
  order: (id) => `/work-orders/${id}`,
  orderAction: (id, a) => `/work-orders/${id}/${a}`,
  today: "/work-orders/meta/today",
  productSearch: "/products",
  mediaUpload: "/media",
};

export const ALLY_FIELD_API: FieldApi = {
  mode: "aliado",
  surveys: "/aliado/levantamientos",
  survey: (id) => `/aliado/levantamientos/${id}`,
  createSurvey: "/aliado/levantamientos",
  saveSurvey: (id) => `/aliado/levantamientos/${id}`,
  sendSurvey: (id) => `/aliado/levantamientos/${id}/enviar`,
  resolveObs: (s, o) => `/aliado/levantamientos/${s}/observaciones/${o}`,
  specs: "/aliado/levantamientos/specs",
  specsCommon: "/aliado/levantamientos/specs/common",
  system: (id) => `/aliado/levantamientos/${id}/sistema`,
  technicians: "/aliado/tecnicos",
  orders: "/aliado/ordenes",
  order: (id) => `/aliado/ordenes/${id}`,
  orderAction: (id, a) => `/aliado/ordenes/${id}/${a}`,
  today: "/aliado/mi-dia",
  productSearch: "/aliado/productos/buscar",
  mediaUpload: "/aliado/media",
};

export const FieldApiContext = createContext<FieldApi>(INTERNAL_FIELD_API);
export const useFieldApi = () => useContext(FieldApiContext);
