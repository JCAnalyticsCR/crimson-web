/* Barra fija de abajo de Mi Tienda (O4-02): «3 cambios sin guardar · Descartar · Guardar y publicar». Junta los
   borradores de la seccion (puede haber mas de una fuente, p. ej. Paginas: vitrina y textos legales), los guarda en
   orden y despues ofrece «Deshacer». Un 409 (alguien guardo en el medio) se dice en la barra con «Recargar».
   Tambien ofrece recuperar lo escrito que quedo en este dispositivo. */
import { useEffect, useId, useState } from "react";
import { useSession } from "../../../app/session";
import type { Draft } from "./useDraft";
import { useHub } from "./StoreHub";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyDraft = Draft<any>;

const hace = (t: number) => {
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  return `del ${new Date(t).toLocaleString("es-CR", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}`;
};

export function SaveBar({ drafts, validate, labels, onSaved }: {
  drafts: AnyDraft[];
  /** texto del problema que impide guardar (p. ej. un color mal escrito); null = se puede */
  validate?: () => string | null;
  labels?: Record<string, string>;
  onSaved?: () => void;
}) {
  const { toast } = useSession();
  const hub = useHub();
  const id = useId();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ text: string; conflict: boolean } | null>(null);
  const changed = drafts.flatMap((d) => d.changed.map(String));
  const n = changed.length;
  const rec = drafts.find((d) => d.recovery);
  const { setDirty } = hub;
  useEffect(() => { setDirty(id, n); return () => setDirty(id, 0); }, [id, n, setDirty]);

  const save = async () => {
    const problema = validate?.();
    if (problema) { setErr({ text: problema, conflict: false }); return; }
    setBusy(true);
    setErr(null);
    const undos: (() => Promise<void>)[] = [];
    let fallo = false;
    for (const d of drafts) {
      if (!d.changed.length) continue;
      const r = await d.save();
      if (!r.ok) { setErr({ text: r.error, conflict: r.conflict }); fallo = true; break; }
      if (r.undo) undos.push(r.undo);
    }
    setBusy(false);
    if (undos.length) {
      hub.reload();
      onSaved?.();
      if (!fallo) {
        toast("Cambios guardados y publicados", "ok", {
          label: "Deshacer",
          run: async () => {
            try {
              for (const u of [...undos].reverse()) await u();
              hub.reload();
              onSaved?.();
              toast("Cambios deshechos");
            } catch (e) {
              toast(e instanceof Error ? e.message : "No se pudo deshacer", "bad");
            }
          },
        });
      }
    }
  };

  if (!n && !rec && !err) return null;
  const nombres = [...new Set(changed.map((k) => labels?.[k] || k))];
  return (
    <div className="savebar" role="region" aria-label="Cambios sin guardar">
      {rec && !n ? (
        <>
          <span className="savebar__t">Hay cambios sin guardar {hace(rec.recovery!.at)} en este dispositivo.</span>
          <span className="savebar__acts">
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => drafts.forEach((d) => d.dropRecovery())}>Descartarlos</button>
            <button type="button" className="btn btn--crimson btn--sm" onClick={() => drafts.forEach((d) => d.recover())}>Recuperarlos</button>
          </span>
        </>
      ) : (
        <>
          <span className="savebar__t" title={nombres.join(", ")}>
            <b>{n === 1 ? "1 cambio sin guardar" : `${n} cambios sin guardar`}</b>
            {nombres.length > 0 && <span className="savebar__what">{nombres.join(" · ")}</span>}
            {err && <span role="alert" className="savebar__err">{err.text}</span>}
          </span>
          <span className="savebar__acts">
            {err?.conflict && <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setErr(null); drafts.forEach((d) => d.reload()); }}>Recargar</button>}
            <button type="button" className="btn btn--ghost btn--sm" disabled={busy || !n} onClick={() => { setErr(null); drafts.forEach((d) => d.discard()); }}>Descartar</button>
            <button type="button" className="btn btn--crimson btn--sm" disabled={busy || !n} onClick={save}>{busy ? "Guardando…" : "Guardar y publicar"}</button>
          </span>
        </>
      )}
    </div>
  );
}
