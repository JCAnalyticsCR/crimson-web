/* Productos e inventario → 2. Mis productos (auditoría de inventarios, 10/11).
   El catálogo único de Crimson y lo que responde «¿qué vende Crimson y de dónde lo obtiene?»:
   - Catálogo: la lista y la ficha de siempre (Productos & Servicios).
   - Disponibilidad: resumen comercial por producto (propio libre / reservado y proveedor con su fecha), antes en
     Inventarios → Existencias.
   - Publicación en la tienda: publicado / visible / comprable y su motivo, antes Inventarios → Inventario web.
   Las existencias físicas por bodega están en Bodegas y existencias. */
import { useNavigate, useSearchParams } from "react-router-dom";
import { useSession } from "../../app/session";
import Products from "./Products";
import Disponibilidad from "../inventory/Disponibilidad";
import WebInventory from "../inventory/WebInventory";

export const VISTAS = [["catalogo", "Catálogo"], ["disponibilidad", "Disponibilidad"], ["publicacion", "Publicación en la tienda"]] as const;

export function ProductTabs({ vista }: { vista: string }) {
  const { allows } = useSession();
  const [params, setParams] = useSearchParams();
  const ok = (k: string) => k === "catalogo" || (k === "disponibilidad" && allows("inventory.ver")) || (k === "publicacion" && allows("catalog.precios"));
  const shown = VISTAS.filter(([k]) => ok(k));
  if (shown.length < 2) return null;
  const set = (k: string) => { const p = new URLSearchParams(); if (k !== "catalogo") p.set("vista", k); else { const q = params.get("q"); if (q) p.set("q", q); } setParams(p); };
  return (
    <div className="tabs" style={{ alignSelf: "flex-start" }} role="tablist" aria-label="Vista de mis productos">
      {shown.map(([k, l]) => <button key={k} role="tab" aria-selected={vista === k} className={vista === k ? "is-active" : ""} onClick={() => set(k)}>{l}</button>)}
    </div>
  );
}

export default function ProductsHub() {
  const { allows } = useSession();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const pedida = params.get("vista") || "catalogo";
  const vista = (pedida === "disponibilidad" && allows("inventory.ver")) || (pedida === "publicacion" && allows("catalog.precios")) ? pedida : "catalogo";
  if (vista === "catalogo") return <Products tabs={<ProductTabs vista="catalogo" />} />;
  return (
    <>
      <div className="page-head">
        <div><div className="meta">Productos e inventario · 2</div><h1 className="h1">Mis productos</h1></div>
      </div>
      <ProductTabs vista={vista} />
      {vista === "disponibilidad" && <Disponibilidad onKardex={(p) => nav(`/inventario?tab=movimientos&pid=${p.id}&pcode=${encodeURIComponent(p.code)}&pname=${encodeURIComponent(p.name)}`)} />}
      {vista === "publicacion" && <WebInventory />}
    </>
  );
}
