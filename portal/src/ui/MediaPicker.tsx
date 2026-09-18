/* Subida y seleccion de imagenes (productos, logo, eventos) o PDF (adjuntos). Arrastrar o elegir. */
import { useEffect, useRef, useState } from "react";
import { api, uploadFile } from "../lib/api";
import { useSession } from "../app/session";
import { I, Icon, Modal } from "./components";

export type MediaItem = { id: number; key: string; url: string; filename: string; content_type: string; size: number };

export function useUpload() {
  const { toast } = useSession();
  const [busy, setBusy] = useState(false);
  const upload = async (file: File): Promise<MediaItem | null> => {
    setBusy(true);
    try { return await uploadFile<MediaItem>("/media", file); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo subir", "bad"); return null; }
    finally { setBusy(false); }
  };
  return { upload, busy };
}

/** Zona de subida: devuelve el archivo ya guardado. */
export function Dropzone({ onDone, accept = "image/*", label = "Arrastrá una imagen o hacé clic", compact }: { onDone: (m: MediaItem) => void; accept?: string; label?: string; compact?: boolean }) {
  const { upload, busy } = useUpload();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const take = async (f?: File | null) => { if (!f) return; const m = await upload(f); if (m) onDone(m); };
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") input.current?.click(); }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files?.[0]); }}
      style={{ border: `1.5px dashed ${over ? "var(--crimson)" : "var(--hair-2)"}`, borderRadius: 12, padding: compact ? "10px 12px" : "22px 16px", textAlign: "center", cursor: "pointer", background: over ? "var(--crimson-soft)" : "transparent", color: "var(--text-3)", fontSize: 13, transition: "all .15s" }}
    >
      <input ref={input} type="file" accept={accept} hidden onChange={(e) => { take(e.target.files?.[0]); e.target.value = ""; }} />
      {busy ? <span className="spinner" /> : <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Icon d={I.plus} size={16} />{label}</span>}
      {!compact && <div className="meta" style={{ marginTop: 6 }}>PNG, JPG, WEBP o GIF · máx. 5 MB{accept.includes("pdf") ? " · PDF 10 MB" : ""}</div>}
    </div>
  );
}

/** Una imagen, con biblioteca de lo ya subido. */
export function ImageField({ value, onChange, label = "Imagen" }: { value: string | null | undefined; onChange: (url: string | null) => void; label?: string }) {
  const [lib, setLib] = useState(false);
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
      <div style={{ width: 72, height: 72, borderRadius: 12, border: "1px solid var(--hair-2)", background: value ? `center/contain no-repeat url("${value}")` : "var(--bg-2)", flex: "none", display: "grid", placeItems: "center" }}>
        {!value && <span className="meta">{label}</span>}
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
        <Dropzone compact onDone={(m) => onChange(m.url)} label="Subir imagen" />
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setLib(true)}>Biblioteca</button>
          {value && <button type="button" className="btn btn--ghost btn--sm" onClick={() => onChange(null)}>Quitar</button>}
        </div>
      </div>
      {lib && <Library onPick={(m) => { onChange(m.url); setLib(false); }} onClose={() => setLib(false)} />}
    </div>
  );
}

export type GalleryImage = { url: string; main?: boolean };

/** Galeria de producto: varias imagenes, una principal. */
export function GalleryField({ value, onChange }: { value: GalleryImage[]; onChange: (v: GalleryImage[]) => void }) {
  const [lib, setLib] = useState(false);
  const add = (url: string) => onChange([...value, { url, main: value.length === 0 }]);
  const setMain = (i: number) => onChange(value.map((x, j) => ({ ...x, main: j === i })));
  const remove = (i: number) => { const v = value.filter((_, j) => j !== i); if (v.length && !v.some((x) => x.main)) v[0] = { ...v[0], main: true }; onChange(v); };
  return (
    <div>
      {value.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(92px, 1fr))", gap: 8, marginBottom: 8 }}>
          {value.map((img, i) => (
            <div key={img.url + i} style={{ position: "relative", aspectRatio: "1", borderRadius: 10, border: `2px solid ${img.main ? "var(--crimson)" : "var(--hair-2)"}`, background: `center/cover no-repeat url("${img.url}")` }}>
              <div style={{ position: "absolute", inset: "auto 4px 4px 4px", display: "flex", gap: 4, justifyContent: "space-between" }}>
                <button type="button" className="btn btn--soft btn--sm" style={{ padding: "2px 6px", fontSize: 11 }} onClick={() => setMain(i)} title="Usar como principal">{img.main ? "Principal" : "★"}</button>
                <button type="button" className="btn btn--danger btn--sm" style={{ padding: "2px 6px", fontSize: 11 }} onClick={() => remove(i)} title="Quitar"><Icon d={I.x} size={12} /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
        <div style={{ flex: 1 }}><Dropzone compact onDone={(m) => add(m.url)} label="Agregar imagen" /></div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setLib(true)}>Biblioteca</button>
      </div>
      {lib && <Library onPick={(m) => { add(m.url); setLib(false); }} onClose={() => setLib(false)} />}
    </div>
  );
}

function Library({ onPick, onClose }: { onPick: (m: MediaItem) => void; onClose: () => void }) {
  const [items, setItems] = useState<MediaItem[] | null>(null);
  useEffect(() => { api<MediaItem[]>("/media?kind=image").then(setItems).catch(() => setItems([])); }, []);
  return (
    <Modal title="Biblioteca de imágenes" onClose={onClose} wide>
      <Dropzone onDone={onPick} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))", gap: 10, marginTop: 14 }}>
        {items === null ? <span className="spinner" /> : items.length === 0 ? <span className="muted">Todavía no hay imágenes.</span> : items.map((m) => (
          <button key={m.id} type="button" onClick={() => onPick(m)} title={m.filename} style={{ aspectRatio: "1", borderRadius: 10, border: "1px solid var(--hair-2)", cursor: "pointer", background: `center/cover no-repeat url("${m.url}")` }} />
        ))}
      </div>
    </Modal>
  );
}
