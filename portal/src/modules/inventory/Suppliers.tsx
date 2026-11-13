/* Productos e inventario → 1. Catálogos de proveedores (auditoría de inventarios, 10/11).
   Lo que cada proveedor nos puede vender: NO es existencia de Crimson. Vincular una oferta no agrega unidades.
   - Catálogos: artículos, costo y moneda, existencia reportada con su fecha, vínculo y estado de la sincronización.
   - Pendientes de incorporación: artículos del proveedor que todavía no son productos de Crimson (antes «Por completar»,
     que vivía en Inventarios y parecía una falta del stock propio). */
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useSession } from "../../app/session";
import SupplierCatalog from "./SupplierCatalog";
import PorCompletar from "./PorCompletar";
import { useCompletarCount } from "./useCompletarCount";

export default function Suppliers() {
  const { allows } = useSession();
  const [params, setParams] = useSearchParams();
  const puedeIncorporar = allows("catalog.editar");
  const tab = params.get("tab") === "incorporar" && puedeIncorporar ? "incorporar" : "catalogos";
  const completar = useCompletarCount();
  const [nLive, setNLive] = useState<number | null>(null);
  const n = nLive ?? completar;
  const setTab = (k: string) => { const p = new URLSearchParams(params); if (k === "catalogos") p.delete("tab"); else p.set("tab", k); setParams(p); };
  return (
    <>
      <div className="page-head">
        <div><div className="meta">Productos e inventario · 1</div><h1 className="h1">Catálogos de proveedores</h1></div>
        <div className="page-head__actions">
          <Link className="btn btn--ghost btn--sm" to="/productos">Mis productos</Link>
          {allows("inventory.ver") && <Link className="btn btn--ghost btn--sm" to="/inventario">Bodegas y existencias</Link>}
        </div>
      </div>
      <div className="tabs" style={{ alignSelf: "flex-start" }} role="tablist">
        <button role="tab" aria-selected={tab === "catalogos"} className={tab === "catalogos" ? "is-active" : ""} onClick={() => setTab("catalogos")}>Catálogos</button>
        {puedeIncorporar && <button role="tab" aria-selected={tab === "incorporar"} className={tab === "incorporar" ? "is-active" : ""} onClick={() => setTab("incorporar")}>{n ? `Pendientes de incorporación · ${n}` : "Pendientes de incorporación"}</button>}
      </div>
      {tab === "catalogos" && <SupplierCatalog />}
      {tab === "incorporar" && <PorCompletar onCount={setNLive} />}
    </>
  );
}
