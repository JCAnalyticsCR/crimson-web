/* Completar CABYS en bloque. Produccion tiene ~218 productos sin CABYS (y sin CABYS no se factura).
   El sistema SUGIERE un codigo por producto (diccionario del rubro + buscador de Hacienda); una persona confirma
   uno a uno, por grupo o "aceptar seleccionadas". Nada se guarda sin ese clic. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Empty, I, Icon, Loading } from "../../ui/components";
import { Lookup, type LookupItem } from "../../ui/Lookup";
import "./cabys.css";

type Pend = {
  id: number; code: string; name: string; brand: string | null; model: string | null; item_type: string; category_id: number | null; category: string | null;
  own_stock: string | number; supplier_stock: number | null; tax_rate: number | null;
};
type Group = { id: number | null; name: string; count: number; with_stock: number };
type Data = { total: number; with_stock: number; items: Pend[]; categories: Group[] };
type Cab = { code: string; description: string; tax_rate: number | null; categories?: string[]; tax_configured?: boolean };
type Sug = { product_id: number; suggestion: Cab | null; alternatives: Cab[]; term: string | null; tried?: string[]; error: string | null };
type Result = { product_id: number; ok: boolean; code: string; description?: string; tax_applied?: boolean; warning?: string; error?: string };

const CHUNK = 10; // tandas chicas: las primeras sugerencias aparecen rapido aunque Hacienda tarde
const gkey = (id: number | null) => String(id ?? 0);
const iva = (c: Cab) => (c.tax_rate == null ? "IVA ?" : `IVA ${c.tax_rate}%`);

export default function CabysBulk() {
  const { toast } = useSession();
  const [data, setData] = useState<Data | null>(null);
  const [sugs, setSugs] = useState<Record<number, Sug>>({});
  const [choice, setChoice] = useState<Record<number, Cab>>({});
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [errs, setErrs] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [applyTax, setApplyTax] = useState(true);
  const [q, setQ] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [down, setDown] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  /* Una sola tanda viva a la vez: salir de la pantalla o pedir "Sugerir faltantes" corta la anterior. */
  const run = useRef(0);
  useEffect(() => () => { run.current += 1; }, []);

  /* Sugerencias por tandas, en el orden de la pantalla (lo que tiene existencias primero). */
  const suggestAll = useCallback(async (ids: number[]) => {
    const me = ++run.current;
    const alive = { get current() { return run.current === me; } };
    setDown(null);
    setProgress({ done: 0, total: ids.length });
    for (let i = 0; i < ids.length && alive.current; i += CHUNK) {
      const part = ids.slice(i, i + CHUNK);
      try {
        const r = await api<{ items: Sug[] }>("/cabys/suggest", { method: "POST", json: { product_ids: part } });
        if (!alive.current) return;
        setSugs((s) => ({ ...s, ...Object.fromEntries(r.items.map((x) => [x.product_id, x])) }));
        setChoice((c) => {
          const n = { ...c };
          for (const x of r.items) if (x.suggestion && !n[x.product_id]) n[x.product_id] = x.suggestion;
          return n;
        });
        const caida = r.items.find((x) => x.error && /Hacienda/.test(x.error));
        if (caida) { setDown(caida.error); break; }
      } catch (e) {
        setDown(e instanceof Error ? e.message : "No se pudieron pedir las sugerencias");
        break;
      }
      setProgress({ done: Math.min(i + CHUNK, ids.length), total: ids.length });
    }
    if (alive.current) setProgress(null);
  }, []);

  const load = useCallback(async () => {
    const d = await api<Data>("/cabys/pending");
    setData(d);
    return d;
  }, []);
  const toastRef = useRef(toast);
  toastRef.current = toast; // toast cambia de identidad en cada render de la sesion: no debe relanzar la carga
  useEffect(() => {
    let off = false;
    load().then((d) => { if (!off) suggestAll(d.items.map((x) => x.id)); }).catch((e) => toastRef.current(e instanceof Error ? e.message : "Error", "bad"));
    return () => { off = true; };
  }, [load, suggestAll]);

  const items = useMemo(() => {
    const t = q.trim().toLowerCase();
    const all = data?.items ?? [];
    return t ? all.filter((x) => `${x.name} ${x.code} ${x.brand || ""} ${x.model || ""} ${x.category || ""}`.toLowerCase().includes(t)) : all;
  }, [data, q]);
  const groups = useMemo(() => {
    const by = new Map<string, Pend[]>();
    for (const x of items) by.set(gkey(x.category_id), [...(by.get(gkey(x.category_id)) || []), x]);
    return (data?.categories ?? []).filter((g) => by.has(gkey(g.id))).map((g) => ({ ...g, rows: by.get(gkey(g.id))! }));
  }, [items, data]);

  const toggle = (id: number, on?: boolean) => setSel((s) => { const n = new Set(s); if (on ?? !n.has(id)) n.add(id); else n.delete(id); return n; });
  const pick = (id: number, c: Cab) => { setChoice((x) => ({ ...x, [id]: c })); setErrs((e) => { const n = { ...e }; delete n[id]; return n; }); toggle(id, true); };

  /* Guardar lo confirmado. La API valida cada codigo (13 digitos y que exista); lo invalido vuelve con su motivo. */
  const accept = async (ids: number[]) => {
    const list = ids.filter((id) => choice[id]).map((id) => ({ product_id: id, code: choice[id].code }));
    if (!list.length) return toast("Nada para guardar: elegí un CABYS primero", "bad");
    setSaving(true);
    try {
      const r = await api<{ saved: number; errors: number; results: Result[] }>("/cabys/assign", { method: "POST", json: { items: list, apply_tax: applyTax } });
      const ok = new Set(r.results.filter((x) => x.ok).map((x) => x.product_id));
      setData((d) => (d ? { ...d, total: d.total - ok.size, items: d.items.filter((x) => !ok.has(x.id)) } : d));
      setSel((s) => new Set([...s].filter((id) => !ok.has(id))));
      setErrs((e) => ({ ...e, ...Object.fromEntries(r.results.filter((x) => !x.ok).map((x) => [x.product_id, x.error || "No se guardó"])) }));
      const avisos = r.results.filter((x) => x.warning).length;
      if (r.saved) toast(`${r.saved} CABYS guardado${r.saved !== 1 ? "s" : ""}${r.errors ? ` · ${r.errors} con error (marcados en rojo)` : ""}${avisos ? ` · ${avisos} sin IVA configurado` : ""}`, r.errors ? "bad" : "ok");
      else toast(`No se guardó ninguno: ${r.results[0]?.error || "revisá los códigos"}`, "bad");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setSaving(false); }
  };

  /* Buscar otro: por descripcion o por los 13 digitos. */
  const hits = useRef<Record<string, Cab>>({});
  const searchCabys = useCallback(async (text: string): Promise<LookupItem[]> => {
    const t = text.trim();
    if (t.length < 3) return [];
    const byCode = /^\d{13}$/.test(t);
    try {
      const rows = await api<Cab[]>(`/cabys?${byCode ? `codigo=${t}` : `q=${encodeURIComponent(t)}&top=20`}`);
      rows.forEach((r) => { hits.current[r.code] = r; });
      if (!rows.length) return [{ id: -1, label: "Sin resultados en el CABYS", hint: "Probá con otra palabra (cámara, cable, fuente…)" }];
      return rows.map((r) => ({ id: Number(r.code), label: r.description, hint: `${r.code} · ${iva(r)}` }));
    } catch (e) {
      return [{ id: -1, label: e instanceof Error ? e.message : "Hacienda no respondió", hint: "Intentá de nuevo" }];
    }
  }, []);

  const selectedReady = [...sel].filter((id) => choice[id]);
  const conSug = items.filter((x) => choice[x.id]).length;

  if (!data) return <Loading label="Buscando productos sin CABYS…" />;

  return (
    <>
      <div className="page-head">
        <div><div className="meta">05 · Catálogo · <Link to="/productos">Productos</Link></div><h1 className="h1">Completar CABYS</h1></div>
        <div className="page-head__actions">
          <button className="btn btn--ghost btn--sm" disabled={!!progress} onClick={() => suggestAll(items.filter((x) => !sugs[x.id] || sugs[x.id].error).map((x) => x.id))}><Icon d={I.refresh} />Sugerir faltantes</button>
        </div>
      </div>

      <div className="cabys-summary">
        <div><b>{data.total}</b><span>sin CABYS</span></div>
        <div><b>{data.items.filter((x) => Number(x.own_stock) > 0).length}</b><span>con existencias</span></div>
        <div><b>{conSug}</b><span>con sugerencia</span></div>
        <p className="muted">La sugerencia sale del nombre y la categoría (sin marca ni modelo) buscada en el CABYS de Hacienda. <b>Revisala antes de aceptar</b>: el código y el IVA van a la factura electrónica.</p>
      </div>

      {progress && <div className="status-line cabys-progress"><span className="spinner" />Buscando sugerencias en Hacienda… {progress.done}/{progress.total}</div>}
      {down && <div className="cabys-down" role="alert"><b>{down}</b><button className="btn btn--soft btn--sm" onClick={() => suggestAll(items.filter((x) => !sugs[x.id] || sugs[x.id].error).map((x) => x.id))}>Reintentar</button></div>}

      <div className="card cabys-tools">
        <div className="search"><Icon d={I.search} size={16} /><input placeholder="Filtrar por nombre, marca, modelo o categoría…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <label className="cabys-check"><input type="checkbox" checked={applyTax} onChange={(e) => setApplyTax(e.target.checked)} />Aplicar el IVA del CABYS al producto</label>
      </div>

      {data.total === 0 ? <Empty title="Todos los productos tienen CABYS" hint="Ya se puede facturar todo el catálogo." action={<Link className="btn btn--soft btn--sm" to="/productos">Volver a productos</Link>} />
        : groups.length === 0 ? <Empty hint="Nada con ese filtro." />
        : groups.map((g) => {
          const k = gkey(g.id);
          const open = !closed.has(k);
          const ready = g.rows.filter((x) => choice[x.id]).map((x) => x.id);
          const allSel = ready.length > 0 && ready.every((id) => sel.has(id));
          return (
            <section className="card cabys-group" key={k}>
              <header className="cabys-group__head">
                <button className="cabys-group__toggle" aria-expanded={open} onClick={() => setClosed((c) => { const n = new Set(c); if (n.has(k)) n.delete(k); else n.add(k); return n; })}>
                  <span className={`cabys-caret${open ? " is-open" : ""}`}>›</span>
                  <b>{g.name}</b><span className="muted">{g.rows.length} producto{g.rows.length !== 1 ? "s" : ""}{g.with_stock ? ` · ${g.with_stock} con existencias` : ""}</span>
                </button>
                <div className="cabys-group__act">
                  <label className="cabys-check"><input type="checkbox" disabled={!ready.length} checked={allSel} onChange={(e) => setSel((s) => { const n = new Set(s); ready.forEach((id) => (e.target.checked ? n.add(id) : n.delete(id))); return n; })} />Seleccionar sugeridas ({ready.length})</label>
                  <button className="btn btn--soft btn--sm" disabled={saving || !ready.length} onClick={() => accept(ready)} title="Guarda la sugerencia elegida de cada producto del grupo">Aceptar grupo</button>
                </div>
              </header>
              {open && <div className="cabys-rows">{g.rows.map((x) => {
                const s = sugs[x.id];
                const c = choice[x.id];
                const opts = s ? [s.suggestion, ...s.alternatives].filter(Boolean) as Cab[] : [];
                if (c && !opts.some((o) => o.code === c.code)) opts.unshift(c);
                const stock = Number(x.own_stock) || 0;
                return (
                  <div className={`cabys-row${errs[x.id] ? " is-err" : ""}${sel.has(x.id) ? " is-sel" : ""}`} key={x.id}>
                    <input type="checkbox" className="cabys-row__chk" aria-label={`Seleccionar ${x.name}`} disabled={!c} checked={sel.has(x.id)} onChange={() => toggle(x.id)} />
                    <div className="cabys-row__prod">
                      <b>{x.name}</b>
                      <div className="meta">{[x.code, x.brand, x.model].filter(Boolean).join(" · ")}</div>
                      <div className="cabys-row__tags">
                        {stock > 0 ? <span className="badge badge--ok">{stock} en bodega</span> : (x.supplier_stock ?? 0) > 0 ? <span className="badge badge--info">bajo pedido</span> : <span className="badge badge--muted">sin existencias</span>}
                        {x.item_type === "servicio" && <span className="badge badge--muted">servicio</span>}
                      </div>
                    </div>
                    <div className="cabys-row__sug">
                      {editing === x.id ? (
                        <div className="cabys-edit">
                          <Lookup value="" placeholder="Buscar en el CABYS (palabra o 13 dígitos)…" fetcher={searchCabys} onSelect={(it) => { if (!it || it.id < 0) return; const code = String(it.id).padStart(13, "0"); pick(x.id, hits.current[code] || { code, description: it.label, tax_rate: null }); setEditing(null); }} />
                          <button className="btn btn--ghost btn--sm" onClick={() => setEditing(null)}>Cancelar</button>
                        </div>
                      ) : c ? (
                        <>
                          <div className="cabys-code"><span className="mono">{c.code}</span><span className={`badge badge--${c.tax_configured === false ? "warn" : "muted"}`} title={c.tax_configured === false ? "Ese IVA no está configurado en Ajustes: el impuesto del producto no se cambiará" : "IVA que indica Hacienda para este código"}>{iva(c)}</span></div>
                          <div className="cabys-desc">{c.description}</div>
                          {opts.length > 1 && (
                            <select className="select select--sm" aria-label="Otras opciones" value={c.code} onChange={(e) => { const o = opts.find((y) => y.code === e.target.value); if (o) pick(x.id, o); }}>
                              {opts.map((o) => <option key={o.code} value={o.code}>{o.code} · {o.description.slice(0, 70)}</option>)}
                            </select>
                          )}
                          {s?.term && <div className="muted cabys-term">Buscado como «{s.term}»</div>}
                        </>
                      ) : s ? <div className="muted cabys-none">{s.error || "Sin sugerencia"}{s.tried?.length ? ` (probado: ${s.tried.slice(0, 3).join(", ")})` : ""}</div>
                        : <div className="muted cabys-none"><span className="spinner" /> Buscando…</div>}
                      {errs[x.id] && <div className="cabys-err">{errs[x.id]}</div>}
                    </div>
                    <div className="cabys-row__act">
                      <button className="btn btn--crimson btn--sm" disabled={!c || saving} onClick={() => accept([x.id])}><Icon d={I.check} size={14} />Aceptar</button>
                      <button className="btn btn--ghost btn--sm" onClick={() => setEditing(editing === x.id ? null : x.id)}><Icon d={I.search} size={14} />Buscar otro</button>
                    </div>
                  </div>
                );
              })}</div>}
            </section>
          );
        })}

      {sel.size > 0 && (
        <div className="cabys-bar" role="region" aria-label="Selección">
          <span><b>{selectedReady.length}</b> seleccionada{selectedReady.length !== 1 ? "s" : ""}</span>
          <button className="btn btn--ghost btn--sm" onClick={() => setSel(new Set())}>Limpiar</button>
          <button className="btn btn--crimson" disabled={saving || !selectedReady.length} onClick={() => accept(selectedReady)}><Icon d={I.check} />Aceptar seleccionadas</button>
        </div>
      )}
    </>
  );
}
