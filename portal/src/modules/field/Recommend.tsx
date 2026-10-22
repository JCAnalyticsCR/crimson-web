/* Recomendar equipos (Andres, LEV-2026-0008): para cada linea generica de la sugerencia ("Cámara Bullet 4 MP",
   "NVR 8 canales") hasta 3 productos reales del catalogo propio o del proveedor, con el porque de cada uno.
   Nada se agrega solo: el usuario toca "Usar este". El tecnico ve que equipo es y si hay existencias; el costo
   solo quien lo ve (la API ya no lo manda a los demas). Pensado primero para el celular (una columna, botones
   grandes) y en escritorio las opciones lado a lado. */
import { useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useMediaUrl } from "../../lib/media";
import { useSession } from "../../app/session";
import { I, Icon, Modal } from "../../ui/components";
import "./recommend.css";

export type Candidate = {
  key: string; source: "propio" | "proveedor"; product_id: number | null; supplier_item_id: number | null; supplier: string | null;
  code: string | null; name: string; model: string | null; brand: string | null; image: string | null;
  stock_propio: number | string; stock_proveedor: number | string | null;
  availability: "bodega" | "bodega_parcial" | "proveedor" | "sin_existencias"; availability_label: string;
  /* existencia del proveedor: cuando se actualizo y si esta vigente (la misma regla de la tienda) */
  updated_at?: string | null; supplier_updated_at?: string | null; supplier_age?: string | null; supplier_changed_age?: string | null; supplier_fresh?: boolean | null;
  quantity: number | string; unit: string; per_unit: number | string; line_unit: string; quantity_note: string | null;
  meets: boolean; matches: string[]; fails: string[]; misses: string[]; unknown: string[];
  criteria: { label: string; points: number; ok: boolean | null; hard?: boolean }[];
  score: number; recommended: boolean; selected: boolean; reason: string;
  /* solo con permiso de costos / precios */
  cost?: number | string | null; currency?: string | null; cost_crc?: number | string | null; sale_price?: number | string | null; price_estimated?: boolean;
};
export type RecoMeta = {
  searched: boolean; category?: string; label?: string; needs?: string[]; found?: number; compliant?: number; note?: string | null;
  need: number | string; unit: string;
  current: { item_id: number; product_id: number | null; supplier_item_id: number | null; name: string; quantity: number | string; unit: string } | null;
};
/* Lo que agrega ?candidates=true a cada linea de /suggest (lo de siempre queda igual). */
export type RecoFields = { candidates?: Candidate[]; recommend?: RecoMeta; origin?: string; apply_generic?: boolean };
export type RecoLine = RecoFields & {
  name: string; quantity: number | string; unit: string; kind: string; reason: string; action: string;
  item_id: number | null; matched_name?: string; missing?: string[];
};

const AVAIL_TONE: Record<Candidate["availability"], string> = { bodega: "ok", bodega_parcial: "info", proveedor: "warn", sin_existencias: "muted" };
const n = (v: unknown) => { const x = Number(String(v ?? "").replace(",", ".")); return Number.isFinite(x) ? x : 0; };
const qtyText = (v: unknown) => { const x = n(v); return Number.isInteger(x) ? String(x) : x.toLocaleString("es-CR", { maximumFractionDigits: 3 }); };

/* Lineas que llevan un equipo del catalogo: equipos primero, despues materiales. */
export const recoLines = (lines: RecoLine[]) =>
  lines.filter((l) => l.recommend?.searched).sort((a, b) => (a.kind === "equipo" ? 0 : 1) - (b.kind === "equipo" ? 0 : 1));

export function RecommendModal({ surveyId, lines, focus, onClose, onApplied }: {
  surveyId: number; lines: RecoLine[]; focus?: string | null; onClose: () => void; onApplied: () => Promise<void> | void;
}) {
  const { toast, allows } = useSession();
  const [qty, setQty] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const verCostos = allows("catalog.costos");
  const verPrecios = allows("catalog.precios");
  const shown = recoLines(lines).filter((l) => !focus || l.name === focus);
  const equipos = shown.filter((l) => l.kind === "equipo");
  const materiales = shown.filter((l) => l.kind !== "equipo");

  const use = async (line: RecoLine, c: Candidate) => {
    const k = `${line.name}|${c.key}`;
    const q = n(qty[k] ?? c.quantity);
    if (q <= 0) { toast("Escribí la cantidad antes de usar este equipo", "bad"); return; }
    setBusy(k);
    try {
      await api(`/surveys/${surveyId}/suggest/apply-product`, {
        method: "POST",
        json: {
          line: line.name, quantity: q, product_id: c.product_id, supplier_item_id: c.product_id ? null : c.supplier_item_id, code: c.code,
          item_id: line.recommend?.current?.item_id ?? line.item_id ?? null,
        },
      });
      toast(`${c.name} × ${qtyText(q)} quedó en el levantamiento`);
      await onApplied();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(null); }
  };

  const block = (title: string, ls: RecoLine[]) => ls.length > 0 && (
    <div className="reco-group" key={title}>
      {!focus && <div className="reco-group__title">{title}</div>}
      {ls.map((l) => <LineBlock key={l.name} line={l} qty={qty} setQty={setQty} busy={busy} onUse={use} verCostos={verCostos} verPrecios={verPrecios} />)}
    </div>
  );

  return (
    <Modal title={focus ? `Equipos para «${focus}»` : "Equipos recomendados"} onClose={onClose} wide foot={<button className="btn btn--ghost" onClick={onClose}>Cerrar</button>}>
      <p className="reco-intro">
        Hasta 3 equipos del catálogo por línea, según lo levantado en los puntos. Primero los que cumplen todo, después los que hay en bodega,
        luego con el proveedor y al final sin existencias. <b>Nada se agrega hasta que tocás «Usar este».</b>
      </p>
      {shown.length === 0 && <p className="muted" style={{ margin: 0, fontSize: 13 }}>No hay líneas con equipo del catálogo para recomendar. Agregá puntos (cámaras, puertas…) y volvé a intentar.</p>}
      {block("Equipos", equipos)}
      {block("Materiales", materiales)}
    </Modal>
  );
}

function LineBlock({ line, qty, setQty, busy, onUse, verCostos, verPrecios }: {
  line: RecoLine; qty: Record<string, string>; setQty: (q: Record<string, string>) => void; busy: string | null;
  onUse: (l: RecoLine, c: Candidate) => void; verCostos: boolean; verPrecios: boolean;
}) {
  const m = line.recommend!;
  const cands = line.candidates || [];
  const replaces = !m.current && line.item_id ? line.matched_name || line.name : null;
  return (
    <section className="reco-line">
      <header className="reco-line__head">
        <div className="reco-line__title">
          <b>{line.name}</b>
          <span className="reco-line__need">{n(line.quantity) > 0 ? `${qtyText(line.quantity)} ${line.unit}` : "cantidad por definir"}</span>
        </div>
        {(m.needs || []).length > 0 && <div className="reco-needs">{m.needs!.map((x) => <span key={x} className="reco-chip">{x}</span>)}</div>}
      </header>
      {m.current && <p className="reco-line__current"><Icon d={I.check} size={14} /> En uso: <b>{m.current.name}</b> × {qtyText(m.current.quantity)} {m.current.unit}</p>}
      {replaces && <p className="reco-line__hint">Ya está cargada como «{replaces}»: el equipo que elijas reemplaza esa línea.</p>}
      {line.missing && line.missing.length > 0 && <p className="reco-line__hint">Falta: {line.missing.join("; ")}</p>}
      {m.note && <p className={`reco-line__note${cands.length ? "" : " is-empty"}`}>{m.note}</p>}
      {cands.length > 0 && (
        <div className="reco-cards">
          {cands.map((c) => {
            const k = `${line.name}|${c.key}`;
            return <CandidateCard key={c.key} c={c} value={qty[k] ?? (n(c.quantity) > 0 ? String(n(c.quantity)) : "")} onQty={(v) => setQty({ ...qty, [k]: v })}
              busy={busy === k} disabled={!!busy} onUse={() => onUse(line, c)} verCostos={verCostos} verPrecios={verPrecios} />;
          })}
        </div>
      )}
    </section>
  );
}

function CandidateCard({ c, value, onQty, busy, disabled, onUse, verCostos, verPrecios }: {
  c: Candidate; value: string; onQty: (v: string) => void; busy: boolean; disabled: boolean; onUse: () => void; verCostos: boolean; verPrecios: boolean;
}) {
  const img = useMediaUrl(c.image);
  const origen = c.source === "proveedor" ? `Proveedor${c.supplier ? ` ${c.supplier}` : ""}` : "Catálogo propio";
  const money = (verCostos && c.cost_crc != null) || (verPrecios && c.sale_price != null);
  return (
    <article className={`reco-card${c.recommended ? " is-rec" : ""}${c.selected ? " is-sel" : ""}${c.meets ? "" : " is-off"}`}>
      <div className="reco-card__top">
        <div className="reco-card__img">{img ? <img src={img} alt="" loading="lazy" /> : <Icon d={I.box} size={22} />}</div>
        <div className="reco-card__badges">
          {c.recommended && <span className="badge badge--ok">Recomendado</span>}
          {c.selected && <span className="badge badge--info">En uso</span>}
          {!c.meets && <span className="badge badge--warn">No cumple todo</span>}
          <span className={`badge badge--${AVAIL_TONE[c.availability]}`}>{c.availability_label}</span>
        </div>
      </div>
      <b className="reco-card__name">{c.name}</b>
      <div className="reco-card__meta">{[c.brand, c.model || c.code, origen].filter(Boolean).join(" · ")}</div>
      {Number(c.stock_proveedor ?? 0) > 0 && (
        <div className="reco-card__meta" style={{ color: c.supplier_fresh === false ? "var(--bad)" : undefined }}>
          {c.supplier || "Proveedor"}: {Number(c.stock_proveedor)} · {c.supplier_age && c.supplier_age !== "sin fecha" ? `consultado ${c.supplier_age}${c.supplier_changed_age ? ` · último cambio ${c.supplier_changed_age}` : ""}` : c.updated_at ? `lista del ${new Date(String(c.updated_at).length === 10 ? `${c.updated_at}T12:00:00` : String(c.updated_at)).toLocaleDateString("es-CR", { day: "2-digit", month: "short" })}` : "sin fecha"}{c.supplier_fresh === false ? " (dato viejo: confirmá con el proveedor)" : ""}
        </div>
      )}
      <div className="reco-card__chips">
        {c.matches.map((x) => <span key={`m${x}`} className="reco-chip is-ok">✓ {x}</span>)}
        {c.fails.map((x) => <span key={`f${x}`} className="reco-chip is-bad">✕ {x}</span>)}
        {c.misses.map((x) => <span key={`s${x}`} className="reco-chip is-warn">– {x}</span>)}
        {c.unknown.map((x) => <span key={`u${x}`} className="reco-chip is-unk">? {x}</span>)}
      </div>
      {money && (
        <div className="reco-card__money">
          {verCostos && c.cost_crc != null && <span>Costo <b>{fmtMoney(c.cost_crc)}</b></span>}
          {verPrecios && c.sale_price != null && <span>{c.price_estimated ? "Precio estimado" : "Precio"} <b>{fmtMoney(c.sale_price)}</b></span>}
        </div>
      )}
      <details className="reco-card__score">
        <summary>Por qué · puntaje {c.score}</summary>
        <p>{c.reason}</p>
        <ul>{c.criteria.map((x, i) => <li key={i} className={x.ok === false ? "is-bad" : x.ok === null ? "is-unk" : ""}><span>{x.label}</span><b>+{x.points}</b></li>)}</ul>
      </details>
      <div className="reco-card__use">
        <label className="reco-card__qty">
          <span>Cantidad</span>
          <input className="input input--mono" inputMode="decimal" value={value} placeholder="0" onChange={(e) => onQty(e.target.value)} />
          <span className="muted">{c.unit}</span>
        </label>
        {c.quantity_note && <small className="muted">{c.quantity_note}</small>}
        <button className={`btn ${c.selected ? "btn--soft" : "btn--crimson"}`} disabled={disabled || n(value) <= 0} onClick={onUse}>
          {busy ? "Guardando…" : c.selected ? "Volver a usar" : "Usar este"}
        </button>
      </div>
    </article>
  );
}

/* Resumen de una linea en la tabla de sugerencias: el recomendado (o el que esta en uso) y el boton para ver todos. */
export function RecoSummary({ line, onOpen }: { line: RecoLine; onOpen: () => void }) {
  const cands = line.candidates || [];
  const m = line.recommend;
  if (!m?.searched) return null;
  const rec = cands.find((c) => c.recommended);
  return (
    <div className="reco-sum">
      {m.current ? <span className="reco-sum__cur">En uso: {m.current.name}</span>
        : rec ? <span>Recomendado: <b>{rec.name}</b> · {rec.availability_label}</span>
          : <span className="muted">{m.note || "Sin equipos en el catálogo"}</span>}
      {cands.length > 0 && <button type="button" className="reco-sum__btn" onClick={onOpen}>Ver equipos ({cands.length})</button>}
    </div>
  );
}
