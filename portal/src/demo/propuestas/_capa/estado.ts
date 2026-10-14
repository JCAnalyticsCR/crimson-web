/* Estado de la capa de propuestas, compartido entre la franja (interruptor + chips, en DemoShell) y el motor de
   marcadores (CapaPropuestas). Tienda externa minima con useSyncExternalStore: sin contexto ni dependencias. */
import { useSyncExternalStore } from "react";
import { CLAVES, EVENTOS, porId, type Etiqueta, type Existente, type Propuesta } from "../registro";
import { almacen } from "../_comun/pedidos";
import { sembrarCarritoSiVacio } from "./carrito";

export type Marca = { e: Etiqueta; el: HTMLElement; tipo: "propuesta" | "existente"; info: Propuesta | Existente };
export type EstadoCapa = {
  activa: boolean; existentes: boolean; marcas: Marca[];
  abierta: string | null; // id de la etiqueta con la ficha abierta
  foco: string | null; // «Ver dónde va»: etiqueta a centrar y abrir en cuanto aparezca
  pulso: string | null; // etiqueta que late (solo al llegar por «Ver dónde va»)
  pista: boolean; // mostrar la pista de la primera vez
};

let estado: EstadoCapa = {
  activa: almacen.sesion.get(CLAVES.capa) === "1",
  existentes: almacen.sesion.get(CLAVES.capaExistentes) !== "0",
  marcas: [], abierta: null,
  foco: almacen.sesion.get(CLAVES.foco),
  pulso: null, pista: false,
};
const oyentes = new Set<() => void>();
const emitir = () => oyentes.forEach((f) => f());
const poner = (p: Partial<EstadoCapa>) => { estado = { ...estado, ...p }; emitir(); };

export const getCapa = () => estado;
export const useCapa = () => useSyncExternalStore((f) => { oyentes.add(f); return () => { oyentes.delete(f); }; }, getCapa, getCapa);

const movil = () => typeof matchMedia === "function" && matchMedia("(max-width: 767px)").matches;
const cerrarGuiaEnMovil = () => { if (movil()) window.dispatchEvent(new CustomEvent(EVENTOS.tourCerrar)); };

export function setActiva(activa: boolean, foco?: string | null) {
  almacen.sesion.set(CLAVES.capa, activa ? "1" : "0");
  const pista = activa && !estado.activa && almacen.sesion.get(CLAVES.pista) !== "1";
  if (foco !== undefined) { if (foco) almacen.sesion.set(CLAVES.foco, foco); else almacen.sesion.del(CLAVES.foco); }
  poner({ activa, abierta: activa ? estado.abierta : null, foco: foco !== undefined ? foco : estado.foco, pista: activa ? (pista || estado.pista) : false });
  if (activa) cerrarGuiaEnMovil();
}
export function setExistentes(v: boolean) {
  almacen.sesion.set(CLAVES.capaExistentes, v ? "1" : "0");
  poner({ existentes: v, abierta: !v && estado.abierta && porId(estado.abierta.split("-")[0])?.id.startsWith("H") ? null : estado.abierta });
}
export function setMarcas(marcas: Marca[]) { poner({ marcas }); }
export function abrirFicha(id: string) { poner({ abierta: id }); cerrarGuiaEnMovil(); }
export function cerrarFicha() { if (estado.abierta) poner({ abierta: null }); }
export function consumirFoco() { almacen.sesion.del(CLAVES.foco); poner({ foco: null }); }
export function setPulso(id: string | null) { poner({ pulso: id }); }
export function cerrarPista() { almacen.sesion.set(CLAVES.pista, "1"); poner({ pista: false }); }

/** «Ver dónde va»: enciende la capa, prepara el carrito si hace falta y navega; el motor centra y abre la ficha. */
export async function verDondeVa(e: Etiqueta, navegar: (to: string) => void) {
  if (e.propuesta.startsWith("H") && !estado.existentes) setExistentes(true);
  if (e.preparar === "carrito") await sembrarCarritoSiVacio();
  setActiva(true, e.id);
  window.dispatchEvent(new CustomEvent(EVENTOS.capa, { detail: { activa: true, foco: e.id } }));
  navegar(e.irA);
}

// Otros modulos pueden encender o apagar la capa con el evento (detalle: {activa, foco?}).
if (typeof window !== "undefined") {
  window.addEventListener(EVENTOS.capa, (ev) => {
    const d = (ev as CustomEvent<{ activa?: boolean; foco?: string }>).detail || {};
    if (typeof d.activa !== "boolean") return;
    if (d.activa === estado.activa && (d.foco ?? null) === (estado.foco ?? null)) return;
    setActiva(d.activa, d.foco ?? (d.activa ? estado.foco : null));
  });
}
