/* Autoguardado del levantamiento (lo usa Surveys.tsx con dos o tres lineas). La mecanica general vive en
   lib/useAutosave.ts; aqui solo lo propio del levantamiento:
   - mismo cuerpo y mismos endpoints que el boton Guardar (POST la primera vez, PUT despues);
   - no se autoguarda lo que el flujo no deja editar (cotizado o cerrado), lo que esta en la papelera ni lo que el
     usuario no puede editar despues de crearlo (vendedor: lo crea una sola vez, al cerrar);
   - si la API rechaza un dato (422: un numero mal escrito, un codigo vacio), se guarda todo lo demas y se avisa
     que dato quedo afuera; lo escrito completo sigue en el formulario y en la copia del dispositivo. */
import { useCallback, useRef } from "react";
import { api, ApiError } from "../../lib/api";
import { useSession } from "../../app/session";
import { useAutosave, type SaveCtx, type SaveResult } from "../../lib/useAutosave";
import { AutosaveBar } from "../../ui/AutosaveBar";
import { pickReview, type ReviewInfo } from "./SurveyReview";

type SurveyResp = Partial<ReviewInfo> & { id: number; number: string; status: string; template_version?: number };
type DraftLike = { id?: number; number?: string; status?: string; trashed_at?: string | null };
type Body = Record<string, unknown> & { points?: { code?: string; data?: Record<string, unknown> }[]; common?: Record<string, unknown> | null };

/* Lo que trae el servidor y no se pisa al recuperar una copia local (estado, revision, numero...). */
const META = ["id", "number", "status", "quote_id", "opportunity", "sent_at", "sent_by", "pending_review", "archived_at", "trashed_at", "review", "template_version"];
const LIMITS: Record<string, number> = { site: 300, received_by: 160 };

/* Arregla lo que la API rechazaria sin que nadie tenga que decidir nada: largos maximos y cantidades fuera de rango.
   Devuelve una copia (el formulario no se toca). */
function clamp(b: Body): Body {
  const out: Body = structuredClone(b);
  for (const [k, n] of Object.entries(LIMITS)) if (typeof out[k] === "string" && (out[k] as string).length > n) out[k] = (out[k] as string).slice(0, n);
  (out.points || []).forEach((p, i) => {
    const pt = p as Record<string, unknown>;
    if (!String(pt.code ?? "").trim()) pt.code = `P-${String(i + 1).padStart(2, "0")}`;
    if (typeof pt.code === "string" && pt.code.length > 20) pt.code = pt.code.slice(0, 20);
    if (typeof pt.label === "string" && pt.label.length > 160) pt.label = pt.label.slice(0, 160);
  });
  const labor = out.labor as Record<string, { people: number; days: number }> | undefined;
  if (labor) for (const l of Object.values(labor)) { l.people = Math.min(Math.max(0, l.people || 0), 200); l.days = Math.min(Math.max(0, l.days || 0), 365); }
  const items = out.items as { name: string; unit?: string; note?: string | null; quantity: number }[] | undefined;
  if (items) for (const it of items) {
    if (it.name.length > 200) it.name = it.name.slice(0, 200);
    if (it.unit && it.unit.length > 10) it.unit = it.unit.slice(0, 10);
    if (it.note && it.note.length > 200) it.note = it.note.slice(0, 200);
    if (!(it.quantity >= 0)) it.quantity = 0;
  }
  return out;
}

/* "PTO-01: Altura: '2.5.3' no es un número" / "Altura: no puede ser negativo": vacia ese dato (en el punto que
   nombra el mensaje, o en la seccion comun) para que lo demas se guarde. Devuelve false si no supo que vaciar. */
function blankBad(b: Body, msg: string): boolean {
  const bad = msg.match(/'(.*)' no es un número/)?.[1];
  const negative = /no puede ser negativo/.test(msg);
  if (bad === undefined && !negative) return false;
  const code = msg.split(":")[0]?.trim();
  const hit = (v: unknown) => (bad !== undefined ? String(v ?? "").trim() === bad.trim() : Number(String(v ?? "").replace(",", ".")) < 0);
  let done = false;
  const clean = (data?: Record<string, unknown> | null) => {
    if (!data) return;
    for (const [k, v] of Object.entries(data)) {
      if (v && typeof v === "object" && !Array.isArray(v)) clean(v as Record<string, unknown>);
      else if (typeof v !== "boolean" && !Array.isArray(v) && v !== null && v !== "" && hit(v)) { data[k] = ""; done = true; }
    }
  };
  const pts = (b.points || []).filter((p) => p.code === code);
  if (pts.length) pts.forEach((p) => clean(p.data));
  else clean(b.common || null);
  return done;
}

export function useSurveyAutosave<D extends DraftLike>({ draft, setDraft, body, onSaved }: {
  draft: D | null;
  setDraft: (fn: (d: D | null) => D | null) => void;
  body: (d: D) => Body;
  /** Despues de guardar (recalcula y refresca la lista). Se llama al crear, al cerrar y como mucho cada 15 s. */
  onSaved?: () => void;
}) {
  const { me, allows, toast } = useSession();
  const canEdit = allows("field.editar");
  const lastNotice = useRef(0);
  const readOnly = draft?.status === "cotizado" || draft?.status === "cerrado";
  const enabled = !!draft && !readOnly && !draft.trashed_at && (canEdit || !draft.id);

  const save = useCallback(async (d: D, ctx: SaveCtx): Promise<SaveResult> => {
    const id = d.id ?? (ctx.id as number | undefined);
    const send = (json: Body) => {
      const size = JSON.stringify(json).length;
      return api<SurveyResp>(id ? `/surveys/${id}` : "/surveys", {
        method: id ? "PUT" : "POST", json, headers: { "X-Autosave": "1" },
        // al salir de la pagina el pedido tiene que sobrevivir (keepalive admite hasta 64 KB)
        keepalive: ctx.keepalive && size < 60_000,
      });
    };
    const full = body(d);
    let s: SurveyResp;
    let warning: string | null = null;
    try {
      s = await send(full);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 422)) throw e;
      // se guarda lo valido; lo que la API rechaza queda afuera y se avisa
      const part = clamp(full);
      const notes: string[] = [e.message];
      let last: unknown = e;
      let ok: SurveyResp | null = null;
      for (let i = 0; i < 8 && !ok; i++) {
        try { ok = await send(part); }
        catch (e2) {
          if (!(e2 instanceof ApiError && e2.status === 422)) throw e2;
          last = e2;
          if (!blankBad(part, e2.message)) break;
          if (!notes.includes(e2.message)) notes.push(e2.message);
        }
      }
      if (!ok) throw last;
      s = ok;
      warning = notes.join(" · ");
    }
    const created = !id;
    if (ctx.isCurrent()) {
      setDraft((cur) => (cur ? ({ ...cur, id: s.id, number: s.number, status: s.status, review: pickReview(s), template_version: s.template_version } as D) : cur));
    }
    const now = Date.now();
    if (onSaved && (created || ctx.reason === "close" || now - lastNotice.current > 15_000)) { lastNotice.current = now; onSaved(); }
    return { id: s.id, warning };
  }, [body, onSaved, setDraft]);

  const auto = useAutosave<D>({
    value: draft,
    idOf: (d) => d.id,
    serialize: (d) => JSON.stringify(body(d)),
    save,
    enabled,
    createEarly: canEdit,
    storage: me ? `lev:${me.tenant.id}:${me.user.id}` : null,
    // el formulario ya se cerro: la linea de estado no se ve, se avisa con un toast
    notify: (state, d, msg) => {
      if (state === "draft") return;
      const ref = d.number || "El levantamiento nuevo";
      if (state === "local") toast(`Sin conexión: ${ref} quedó guardado en este dispositivo y se sube solo al volver la conexión.`, "bad");
      else if (state === "error") toast(`${ref} no se pudo guardar: ${msg}. Lo escrito quedó en este dispositivo; al abrirlo se puede recuperar.`, "bad");
      else if (state === "warn") toast(`${ref} se guardó salvo esto: ${msg}. Al abrirlo se puede corregir.`, "bad");
      else toast(`${ref}: se subió lo que había quedado en este dispositivo.`);
    },
  });

  /* La copia del dispositivo vuelve al formulario; lo que es del servidor (estado, revision, numero) no se pisa. */
  const recover = useCallback(() => {
    const data = auto.takeRecovery();
    if (!data) return;
    setDraft((cur) => {
      if (!cur) return cur;
      const next = { ...cur } as Record<string, unknown>;
      for (const [k, v] of Object.entries(data as Record<string, unknown>)) {
        if (META.includes(k)) continue;
        if (k === "opportunity_id" && cur.id) continue; // el enlace con la oportunidad se maneja aparte
        next[k] = v;
      }
      return next as D;
    });
  }, [auto, setDraft]);

  /* Linea de estado lista para el pie del modal ("Guardando…", "Guardado hace 3 s", "Sin conexión…", recuperar). */
  const bar = <AutosaveBar status={auto.status} recovery={auto.recovery} enabled={enabled} onRecover={recover} onDiscard={auto.discardRecovery} />;
  return { ...auto, enabled, recover, bar };
}
