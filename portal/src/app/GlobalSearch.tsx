/* Buscador global (Ctrl+K): facturas, cotizaciones, clientes, productos y ordenes; navegacion con teclado. */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { I, Icon } from "../ui/components";

type Hit = { kind: string; id: number; title: string; sub: string; to: string };

export default function GlobalSearch() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);
  const [idx, setIdx] = useState(0);
  const box = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const k = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); document.getElementById("gsearch")?.focus(); } };
    const out = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", k); window.addEventListener("mousedown", out);
    return () => { window.removeEventListener("keydown", k); window.removeEventListener("mousedown", out); };
  }, []);
  useEffect(() => {
    if (!q.trim()) { setHits([]); return; }
    const t = setTimeout(() => api<Hit[]>(`/search?q=${encodeURIComponent(q.trim())}`).then((r) => { setHits(r); setIdx(0); setOpen(true); }).catch(() => {}), 180);
    return () => clearTimeout(t);
  }, [q]);

  const go = (h: Hit) => { setOpen(false); setQ(""); nav(h.to); };
  return (
    <form ref={box} className="search" style={{ position: "relative" }} onSubmit={(e) => { e.preventDefault(); if (hits[idx]) go(hits[idx]); }}>
      <Icon d={I.search} size={16} />
      <input id="gsearch" placeholder="Buscar facturas, cotizaciones, clientes, productos…" value={q} autoComplete="off"
        onChange={(e) => setQ(e.target.value)} onFocus={() => hits.length && setOpen(true)}
        onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, hits.length - 1)); } if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); } if (e.key === "Escape") setOpen(false); }} />
      <kbd>Ctrl K</kbd>
      {open && q && (
        <div className="card" style={{ position: "absolute", top: 44, left: 0, right: 0, zIndex: 30, padding: 6, maxHeight: 380, overflow: "auto", boxShadow: "var(--shadow-lg)" }}>
          {hits.length === 0 ? <div className="muted" style={{ padding: 12, fontSize: 13 }}>Sin resultados para “{q}”.</div> : hits.map((h, i) => (
            <button key={`${h.kind}-${h.id}-${i}`} type="button" onMouseEnter={() => setIdx(i)} onClick={() => go(h)}
              style={{ display: "grid", gridTemplateColumns: "92px 1fr", gap: 10, width: "100%", textAlign: "left", padding: "9px 10px", borderRadius: 8, border: 0, cursor: "pointer", background: i === idx ? "var(--crimson-soft)" : "transparent", color: "var(--text)" }}>
              <span className="meta" style={{ alignSelf: "center" }}>{h.kind}</span>
              <span><b style={{ fontSize: 13.5 }}>{h.title}</b><span className="muted" style={{ display: "block", fontSize: 12 }}>{h.sub}</span></span>
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
