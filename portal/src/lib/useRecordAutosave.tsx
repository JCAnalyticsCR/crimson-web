/* Autoguardado para cualquier formulario o detalle (Andres 31:16: "un botón que se haga un autoguardado, todo lo que
   es"). Generaliza lo que useSurveyAutosave hace a mano para el levantamiento; NO lo reemplaza (ese sigue igual).
   La mecanica vive en lib/useAutosave.ts; aqui: la copia por empresa y usuario, los avisos con toast, la linea de
   estado lista para el pie y el ultimo cuerpo aceptado (`prev`) para mandar solo lo que cambio.

   Convencion de botones (la misma en todas las pantallas):
   - Registro existente en modo "close": "Guardar" = exclusive; "Cancelar" = descartar
     (`if (auto.isDirty() && !confirm("¿Descartar los cambios?")) return; auto.discard(); cerrar`);
     clic fuera / Escape / X = guarda.
   - Registro existente en modo "pause": no hay "Cancelar"; el boton se llama "Cerrar".
   - Registro nuevo (local): "Guardar" = crear (exclusive); "Cancelar" = descartar con la misma confirmacion;
     clic fuera / Escape / X = queda borrador en el dispositivo + toast.
   Nunca: crear registros nuevos por autoguardado (gastan consecutivo, arrancan SLA o mandan correos), publicar notas
   de ticket o seguimientos solos, ni guardar en localStorage datos sensibles (localCopy: false). */
import { useCallback, useEffect, useRef, type ReactElement } from "react";
import { useSession } from "../app/session";
import { AutosaveBar } from "../ui/AutosaveBar";
import { useAutosave, type AutosaveMode, type SaveCtx } from "./useAutosave";

export type RecordSaveCtx = SaveCtx & { prev: Record<string, unknown> | null; headers: Record<string, string> };
type Id = number | string;

/** "de OT-2026-0004" · "del ticket nuevo" · "de la oportunidad nueva" */
const de = (lbl: string) => (/^El /.test(lbl) ? `del ${lbl.slice(3)}` : /^La /.test(lbl) ? `de la ${lbl.slice(3)}` : `de ${lbl}`);

/** Header de los guardados automaticos (el servidor los distingue del boton Guardar). */
export const AUTOSAVE_HEADERS = { "X-Autosave": "1" };

export function useRecordAutosave<V>(o: {
  /** prefijo de la copia: "ot", "ot-form", "tck", "tck-nota", "tck-resolver", "opp", "opp-seg", "doc-q", "doc-i",
      "ctr", "cli", "cli-dat", "cli-contacto", "costeo"… */
  ns: string;
  /** null = formulario cerrado */
  value: V | null;
  /** id del registro (o del padre, para borradores "por ticket") */
  idOf: (v: V) => number | undefined;
  /** lo que se compara y se manda */
  body: (v: V) => Record<string, unknown>;
  save?: (body: Record<string, unknown>, id: number | undefined, ctx: RecordSaveCtx) => Promise<{ id?: number; warning?: string | null } | void>;
  /** defecto "close" */
  mode?: AutosaveMode;
  /** defecto "local": un registro nuevo solo se crea con Guardar */
  newRecords?: "local" | "server";
  /** sin permiso, solo lectura, finalizado o en papelera: false */
  enabled: boolean;
  /** "OT-2026-0004", "El ticket nuevo" */
  label: (v: V) => string;
  /** aplica una copia recuperada; respeta lo que es del servidor (id, numero, estado) */
  apply: (data: V) => void;
  /** defecto true; false en formularios con datos sensibles */
  localCopy?: boolean;
  delay?: number;
}) {
  const { me, toast } = useSession();
  const mode = o.mode ?? "close";
  const ref = useRef(o);
  ref.current = o;
  // el valor puede armarse en cada render ({ doc, lines }): misma foto = mismo objeto, asi el autoguardado solo
  // reacciona cuando de verdad cambio algo (y no entra en un ciclo de renders)
  const stable = useRef<{ json: string; v: V } | null>(null);
  let value = o.value;
  if (value === null) stable.current = null;
  else {
    const json = JSON.stringify(value);
    if (stable.current && stable.current.json === json) value = stable.current.v;
    else stable.current = { json, v: value };
  }
  // ultimo cuerpo aceptado: al abrir (o cambiar de registro), al recargar del servidor y tras cada guardado
  const prev = useRef<Record<string, unknown> | null>(null);
  const opened = useRef<{ open: boolean; id: number | undefined }>({ open: false, id: undefined });
  const takePrev = useRef(false);
  useEffect(() => {
    const v = value;
    if (v === null) { opened.current = { open: false, id: undefined }; return; }
    const id = ref.current.idOf(v);
    const otro = !opened.current.open || (id !== undefined && opened.current.id !== undefined && id !== opened.current.id);
    if (otro || takePrev.current) { prev.current = ref.current.body(v); takePrev.current = false; }
    opened.current = { open: true, id: id ?? opened.current.id };
  }, [value]);

  const save = useCallback(async (v: V, ctx: SaveCtx) => {
    const fn = ref.current.save;
    if (!fn) return;
    const body = ref.current.body(v);
    const r = await fn(body, (ctx.id as number | undefined) ?? ref.current.idOf(v), { ...ctx, prev: prev.current, headers: AUTOSAVE_HEADERS });
    if (ctx.isCurrent()) prev.current = body;
    return r ? { id: r.id, warning: r.warning ?? null } : undefined;
  }, []);

  const auto = useAutosave<V>({
    value,
    idOf: (v) => o.idOf(v),
    serialize: (v) => JSON.stringify(o.body(v)),
    save,
    enabled: o.enabled,
    server: mode,
    createOnClose: o.newRecords === "server",
    createEarly: false,
    storage: o.localCopy !== false && me ? `${o.ns}:${me.tenant.id}:${me.user.id}` : null,
    maxAgeDays: 30, // borradores de mas de un mes se limpian al montar (el levantamiento no pasa esto: no borra nada)
    delay: o.delay,
    // el formulario ya se cerro: la linea de estado no se ve, se avisa con un toast
    notify: (state, v, msg) => {
      const lbl = ref.current.label(v);
      if (state === "draft") toast(`Quedó un borrador ${de(lbl)} en este dispositivo: al volver a abrirlo lo recuperás.`);
      else if (state === "local") toast(`Sin conexión: ${lbl} quedó guardado en este dispositivo y se sube solo al volver la conexión.`, "bad");
      else if (state === "error") toast(`${lbl} no se pudo guardar: ${msg}. Lo escrito quedó en este dispositivo; al abrirlo se puede recuperar.`, "bad");
      else if (state === "warn") toast(`${lbl} se guardó salvo esto: ${msg}. Al abrirlo se puede corregir.`, "bad");
      else toast(`${lbl}: se subió lo que había quedado en este dispositivo.`);
    },
  });

  const { exclusive: autoExclusive, fromServer: autoFromServer, takeRecovery } = auto;
  /** Guardado manual (Guardar, Enviar): nunca corre a la par del autoguardado. */
  const exclusive = useCallback(<R,>(value: V, fn: (id: Id | undefined) => Promise<R>): Promise<R> =>
    autoExclusive(value, async (id) => { const r = await fn(id); prev.current = ref.current.body(value); return r as R & { id?: Id }; }), [autoExclusive]);
  /** El proximo valor viene recargado del servidor (no es un cambio del usuario). */
  const fromServer = useCallback(() => { takePrev.current = true; autoFromServer(); }, [autoFromServer]);
  /** La copia del dispositivo vuelve al formulario (apply decide que no se pisa). */
  const recover = useCallback(() => { const data = takeRecovery(); if (data) ref.current.apply(data); }, [takeRecovery]);

  const isNew = value !== null && o.idOf(value) === undefined;
  // un registro nuevo "local" no llega al sistema al cerrar (solo con Guardar): para la linea es modo "never"
  const barMode: AutosaveMode = isNew && o.newRecords !== "server" ? "never" : mode;
  const bar: ReactElement = <AutosaveBar mode={barMode} isNew={isNew} status={auto.status} recovery={auto.recovery} enabled={o.enabled} onRecover={recover} onDiscard={auto.discardRecovery} />;
  /** Ultimo cuerpo que el servidor acepto (para mandar solo lo que cambio desde ahi, p. ej. dentro de exclusive). */
  const accepted = useCallback(() => prev.current, []);
  return {
    status: auto.status, recovery: auto.recovery, enabled: o.enabled, bar,
    flush: auto.flush, exclusive, fromServer, discard: auto.discard, recover, isDirty: auto.isDirty, accepted,
  };
}
export type RecordAutosave = ReturnType<typeof useRecordAutosave>;
