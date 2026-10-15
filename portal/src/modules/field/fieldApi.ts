/* Rutas del trabajo de campo del portal de aliados (/aliado/levantamientos, /aliado/ordenes...). La API del aliado
   aplica su propio alcance y listas blancas: aquí solo se eligen rutas.
   aliado_final §10.4 dejaba dos caminos: montar las pantallas internas (Surveys, WorkOrders...) con un contexto de
   rutas, o pantallas propias del portal. Se tomó la alternativa: AllySurvey.tsx y AllyOrders.tsx son pantallas propias
   (reusan las piezas de SurveyV2, PhotoStrip y Lookup) que toman sus rutas de ALLY_FIELD_API, así las pantallas
   internas no cambian ni cargan con ramas del portal externo. Por eso no hay contexto de rutas. */

export type FieldApi = {
  mode: "aliado";
  surveys: string; survey: (id: number) => string; createSurvey: string; saveSurvey: (id: number) => string;
  sendSurvey: (id: number) => string; resolveObs: (sid: number, oid: number) => string;
  specs: string; specsCommon: string; system: (id: number) => string; technicians: string;
  orders: string; order: (id: number) => string; orderAction: (id: number, a: string) => string; today: string;
  productSearch: string; mediaUpload: string;
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
