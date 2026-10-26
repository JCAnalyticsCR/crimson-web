/* Busqueda sin resultados en mi catalogo: lo que el proveedor si tiene con ese codigo o nombre y todavia no es un
   producto de Crimson ("Sin vincular"), para agregarlo ahi mismo. Pedido 26/10: un cliente le mando un codigo a Andres,
   estaba en Eurocomp con existencia y en Productos no salia nada. Crear exige crear productos y ver costos (el precio
   sale del costo del proveedor); quien no puede, ve que existe y a quien pedirlo. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";
import { AddModal, type Detail, type Item } from "./SupplierCatalog";

export default function EnProveedor({ q, onCreated }: { q: string; onCreated: (code: string) => void }) {
  const { allows, toast } = useSession();
  const [rows, setRows] = useState<Item[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const puedeCrear = allows("catalog.crear") && allows("catalog.costos");
  useEffect(() => {
    if (q.length < 3) { setRows([]); return; }
    let vivo = true;
    const t = setTimeout(() => {
      api<{ items: Item[] }>(`/supplier-catalog/items?q=${encodeURIComponent(q)}&linked=no&limit=5`)
        .then((r) => { if (vivo) setRows(r.items || []); })
        .catch(() => { if (vivo) setRows([]); }); // sin permiso para el catalogo del proveedor: no se muestra nada
    }, 300);
    return () => { vivo = false; clearTimeout(t); };
  }, [q]);
  if (!rows.length) return null;
  const abrir = async (it: Item) => {
    try { setDetail(await api<Detail>(`/supplier-catalog/items/${it.id}`)); }
    catch (e) { toast(e instanceof Error ? e.message : "No se pudo abrir", "bad"); }
  };
  return (
    <div className="en-prov">
      <p className="en-prov__head"><b>No está en tu catálogo, pero el proveedor lo tiene</b><span className="muted">{puedeCrear ? "Agregalo y queda listo para cotizar, facturar y publicar." : "Pedile a administración que lo agregue al catálogo."}</span></p>
      <ul>{rows.map((it) => (
        <li key={it.id}>
          <span className="en-prov__main">
            <b>{it.name}</b>
            <span className="meta">{[it.supplier, it.code, it.model].filter(Boolean).join(" · ")}</span>
          </span>
          <span className={`en-prov__stock${Number(it.stock) > 0 ? " is-ok" : ""}`}>{Number(it.stock) > 0 ? `${Number(it.stock)} disponible${Number(it.stock) !== 1 ? "s" : ""}` : "Sin existencia"}</span>
          {puedeCrear && it.active && <button type="button" className="btn btn--crimson btn--sm" onClick={() => abrir(it)}><Icon d={I.plus} size={14} />Agregar a mi catálogo</button>}
        </li>
      ))}</ul>
      {detail && <AddModal item={detail} onClose={() => setDetail(null)} onDone={() => { const code = detail.proposal?.code || detail.code; setDetail(null); setRows((r) => r.filter((x) => x.id !== detail.id)); onCreated(code); }} />}
    </div>
  );
}
