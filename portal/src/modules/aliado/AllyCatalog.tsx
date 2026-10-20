/* Catálogo con precio de aliado (18:39-19:16): todo el catálogo, "Su precio" destacado y el precio público sugerido,
   con y sin IVA, y la disponibilidad. "Precio a consultar" cuando Crimson no tiene uno. Nunca el costo ni el %. */
import { useEffect, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useMediaUrls } from "../../lib/media";
import { Empty, Modal } from "../../ui/components";
import { errorText } from "./shared";

type Prod = {
  id: number; code: string; name: string; brand: string | null; model: string | null; item_type: string; unit: string; category: string | null; description: string | null;
  datasheet_url: string | null; tax_rate: string | null; currency: string; precio_publico: string; precio_publico_con_iva: string | null; precio_aliado: string | null;
  precio_aliado_con_iva: string | null; precio_aliado_estado: "ok" | "a_consultar" | "igual_al_publico"; disponibilidad: string; disponibilidad_detalle?: { state: string; en_bodega?: number | string | null; bajo_pedido_cantidad?: number | string | null; actualizado_al?: string | null } | null; image?: string | null; images?: { url: string; main: boolean }[];
};

function Precio({ p }: { p: Prod }) {
  if (p.precio_aliado === null) return <span className="badge badge--muted">Precio a consultar con Crimson</span>;
  return (
    <div>
      <div className="meta">Su precio</div>
      <div className="al-cat__price">{fmtMoney(p.precio_aliado, p.currency)} <span className="meta">+ IVA</span></div>
      <div className="meta">{fmtMoney(p.precio_aliado_con_iva, p.currency)} con IVA · público sugerido {fmtMoney(p.precio_publico, p.currency)}</div>
    </div>
  );
}

function Ficha({ id, onClose }: { id: number; onClose: () => void }) {
  const [p, setP] = useState<Prod | null>(null);
  useEffect(() => { api<Prod>(`/aliado/catalogo/${id}`).then(setP).catch(() => setP(null)); }, [id]);
  const imgs = useMediaUrls((p?.images || []).map((x) => x.url));
  return (
    <Modal title={p?.name || "Producto"} onClose={onClose}>
      {!p ? <div className="spinner" /> : (
        <div className="cp-form">
          <div style={{ display: "flex", gap: 8, overflowX: "auto" }}>{imgs.map((u, i) => u && <img key={i} src={u} alt="" style={{ height: 120, borderRadius: 10 }} />)}</div>
          <div className="meta">{p.code}{p.brand ? ` · ${p.brand}` : ""}{p.model ? ` · ${p.model}` : ""}{p.category ? ` · ${p.category}` : ""}</div>
          <Precio p={p} />
          <div className="meta">{p.disponibilidad}{p.disponibilidad_detalle?.actualizado_al && Number(p.disponibilidad_detalle.bajo_pedido_cantidad ?? 0) > 0 ? ` · bajo pedido actualizado ${new Date(p.disponibilidad_detalle.actualizado_al).toLocaleString("es-CR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}</div>
          {p.description && <p style={{ whiteSpace: "pre-wrap" }}>{p.description}</p>}
          {p.datasheet_url && <a href={p.datasheet_url} target="_blank" rel="noopener noreferrer">Ficha técnica</a>}
        </div>
      )}
    </Modal>
  );
}

function Tarjeta({ p, onOpen }: { p: Prod; onOpen: () => void }) {
  const [img] = useMediaUrls(p.image ? [p.image] : []);
  return (
    <button type="button" className="al-cat__it" style={{ textAlign: "left", cursor: "pointer" }} onClick={onOpen}>
      <div className="al-cat__img" style={img ? { backgroundImage: `url("${img}")` } : undefined} />
      <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        <b style={{ overflowWrap: "anywhere" }}>{p.name}</b>
        <span className="meta">{p.code}{p.brand ? ` · ${p.brand}` : ""}</span>
        <Precio p={p} />
        <span className="meta">{p.disponibilidad}</span>
      </div>
    </button>
  );
}

export default function AllyCatalog() {
  const [meta, setMeta] = useState<{ categories: { id: number; name: string }[]; brands: string[] }>({ categories: [], brands: [] });
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [brand, setBrand] = useState("");
  const [rows, setRows] = useState<Prod[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<typeof meta>("/aliado/catalogo/meta").then(setMeta).catch(() => {}); }, []);
  const url = (cursor?: number | null) => `/aliado/catalogo?limit=30&q=${encodeURIComponent(q)}${cat ? `&category_id=${cat}` : ""}${brand ? `&brand=${encodeURIComponent(brand)}` : ""}${cursor ? `&cursor=${cursor}` : ""}`;
  useEffect(() => {
    const t = setTimeout(() => api<{ items: Prod[]; next_cursor: number | null }>(url()).then((r) => { setRows(r.items); setNext(r.next_cursor); }).catch((e) => setErr(errorText(e))), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, cat, brand]);
  const mas = () => api<{ items: Prod[]; next_cursor: number | null }>(url(next)).then((r) => { setRows([...(rows || []), ...r.items]); setNext(r.next_cursor); });
  return (
    <>
      <div className="cp-h"><h1 className="h1">Catálogo</h1></div>
      <div className="cp-grid cp-grid--3" style={{ marginBottom: 12 }}>
        <input className="input" placeholder="Buscar producto, código o modelo" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="select" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">Todas las categorías</option>{meta.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        <select className="select" value={brand} onChange={(e) => setBrand(e.target.value)}><option value="">Todas las marcas</option>{meta.brands.map((b) => <option key={b}>{b}</option>)}</select>
      </div>
      {err && <p className="muted">{err}</p>}
      {!rows ? <div className="spinner" /> : rows.length ? (
        <>
          <div className="al-cat">{rows.map((p) => <Tarjeta key={p.id} p={p} onOpen={() => setOpen(p.id)} />)}</div>
          {next && <button className="btn btn--soft" style={{ marginTop: 12 }} onClick={mas}>Más resultados</button>}
        </>
      ) : <Empty title="Sin productos con ese filtro" />}
      {open && <Ficha id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
