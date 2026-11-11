/* Barra de seleccion del catalogo (O4-05): «37 seleccionados · Publicar · Ocultar · Categoría · Marca · Fijar arriba
   · Precio web», con «Seleccionar los N del filtro». Cada accion abre la vista previa (BulkPreviewModal): nada se
   aplica sin ver el antes y el despues y sin confirmar. Publicar y ocultar, solo el administrador; el precio web, quien
   ve precios. Un precio se escribe FINAL con IVA («12.500»); el API guarda el neto con 5 decimales. */
import { useState } from "react";
import { useSession } from "../../../app/session";
import { parseCRC } from "../../../lib/parseCRC";
import { Field, Modal } from "../../../ui/components";
import { BulkPreviewModal, type BulkReq } from "./BulkPreviewModal";

type Opt = { id: number; name: string };

export function BulkBar({ count, total, all, filtro, ids, categories, onAll, onClear, onDone }: {
  count: number; total: number; all: boolean; filtro: Record<string, string>; ids: number[]; categories: Opt[];
  onAll: () => void; onClear: () => void; onDone: () => void;
}) {
  const { allows } = useSession();
  const [req, setReq] = useState<BulkReq | null>(null);
  const [pide, setPide] = useState<null | "categoria" | "marca" | "precio_web">(null);
  const [valor, setValor] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const n = all ? total : count;
  const base = all ? { filtro } : { ids };
  const go = (accion: string, v?: string | number | null) => setReq({ ...base, accion, valor: v ?? null });
  const pedir = () => {
    setErr(null);
    if (pide === "precio_web") {
      if (!valor.trim()) { go("precio_web", null); setPide(null); return; } // vacio = vuelve al precio de siempre
      const r = parseCRC(valor);
      if (!r.ok || r.value <= 0) { setErr(r.ok ? "Escriba un precio mayor que cero." : r.error); return; }
      go("precio_web", r.value);
    } else if (pide === "categoria") {
      if (!valor) { setErr("Elija la categoría."); return; }
      go("categoria", Number(valor));
    } else if (pide === "marca") {
      go("marca", valor.trim());
    }
    setPide(null);
  };
  if (!n) return null;
  return (
    <div className="bulk" role="region" aria-label="Edición en lote">
      <b>{n.toLocaleString("es-CR")} {n === 1 ? "seleccionado" : "seleccionados"}</b>
      {!all && total > count && <button className="btn btn--ghost btn--sm" onClick={onAll}>Seleccionar los {total.toLocaleString("es-CR")} del filtro</button>}
      <button className="btn btn--ghost btn--sm" onClick={onClear}>Quitar selección</button>
      <span className="bulk__acts">
        {allows("catalog.publicar") && <><button className="btn btn--soft btn--sm" onClick={() => go("publicar")}>Publicar</button><button className="btn btn--soft btn--sm" onClick={() => go("ocultar")}>Ocultar</button></>}
        <button className="btn btn--soft btn--sm" onClick={() => { setValor(""); setPide("categoria"); }}>Categoría</button>
        <button className="btn btn--soft btn--sm" onClick={() => { setValor(""); setPide("marca"); }}>Marca</button>
        <button className="btn btn--soft btn--sm" onClick={() => go("fijar")}>Fijar arriba</button>
        <button className="btn btn--soft btn--sm" onClick={() => go("soltar")}>Quitar de arriba</button>
        {allows("catalog.precios") && <button className="btn btn--soft btn--sm" onClick={() => { setValor(""); setPide("precio_web"); }}>Precio web</button>}
      </span>
      {pide && (
        <Modal title={pide === "categoria" ? "Cambiar la categoría" : pide === "marca" ? "Cambiar la marca" : "Precio web final con IVA"} onClose={() => setPide(null)} foot={<><button className="btn btn--ghost" onClick={() => setPide(null)}>Cancelar</button><button className="btn btn--crimson" onClick={pedir}>Ver vista previa</button></>}>
          <div className="mt-stack">
            {pide === "categoria" && <Field label="Categoría"><select className="select" value={valor} onChange={(e) => setValor(e.target.value)}><option value="">Elija…</option>{categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>}
            {pide === "marca" && <Field label="Marca" hint="Vacío = sin marca."><input className="input" value={valor} maxLength={60} onChange={(e) => setValor(e.target.value)} /></Field>}
            {pide === "precio_web" && (
              <>
                <Field label="Precio final con IVA (colones)" hint="Lo que paga el cliente, por ejemplo 12.500. Vacío = vuelve al precio de siempre. Los productos en otra moneda se saltan."><input className="input input--mono" inputMode="decimal" value={valor} onChange={(e) => setValor(e.target.value)} /></Field>
                <p className="mt-lead">La vista previa muestra el precio de cada producto antes y después, y el neto sin IVA que se guarda. Nada cambia hasta que confirme.</p>
              </>
            )}
            {err && <p role="alert" className="mt-warn">{err}</p>}
          </div>
        </Modal>
      )}
      {req && <BulkPreviewModal req={req} onClose={() => setReq(null)} onDone={() => { onDone(); }} />}
    </div>
  );
}
