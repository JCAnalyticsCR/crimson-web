/* Vehiculos y combustible (seccion 12 / AC21): reportes, cargas, flota, importacion y otros costos. */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { I, Icon } from "../../ui/components";
import "./vehicles.css";
import CostsView from "./CostsView";
import FleetView, { VehicleForm, blankVehicle } from "./FleetView";
import ImportView from "./ImportView";
import LoadsView, { LoadForm, blankLoad } from "./LoadsView";
import ReportView from "./ReportView";
import type { Lookups, Vehicle } from "./types";

const TABS = [["reportes", "Reportes"], ["cargas", "Cargas"], ["vehiculos", "Vehículos"], ["importar", "Importar"], ["costos", "Otros costos"]] as const;

export default function Vehicles() {
  const { allows } = useSession();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "reportes";
  const [lookups, setLookups] = useState<Lookups | null>(null);
  const [vehicles, setVehicles] = useState<Vehicle[] | null>(null);
  const [loadForm, setLoadForm] = useState<ReturnType<typeof blankLoad> | null>(null);
  const [vehForm, setVehForm] = useState<ReturnType<typeof blankVehicle> | null>(null);
  const [refresh, setRefresh] = useState(0);
  const reload = () => { api<Lookups>("/vehicles/lookups").then(setLookups); api<Vehicle[]>("/vehicles").then(setVehicles); setRefresh((r) => r + 1); };
  useEffect(reload, []);
  const tabs = TABS.filter(([k]) => k !== "importar" || allows("vehicles.crear"));
  return (
    <>
      <div className="page-head">
        <div><div className="meta">14 · Administración</div><h1 className="h1">Vehículos y combustible</h1></div>
        <div className="page-head__actions">
          {allows("vehicles.crear") && <button className="btn btn--ghost" onClick={() => setVehForm(blankVehicle())}><Icon d={I.plus} />Vehículo</button>}
          {allows("vehicles.crear") && <button className="btn btn--crimson" onClick={() => setLoadForm(blankLoad())} disabled={!lookups?.vehicles.length} title={lookups?.vehicles.length ? undefined : "Primero agregá un vehículo"}><Icon d={I.plus} />Registrar carga</button>}
        </div>
      </div>
      <div className="veh-tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "is-active" : ""} onClick={() => setParams({ tab: k })}>{l}</button>)}</div>
      {tab === "reportes" && <ReportView lookups={lookups} refresh={refresh} />}
      {tab === "cargas" && <LoadsView lookups={lookups} refresh={refresh} onEdit={setLoadForm} />}
      {tab === "vehiculos" && <FleetView vehicles={vehicles} onEdit={setVehForm} />}
      {tab === "importar" && <ImportView onDone={() => { reload(); setParams({ tab: "cargas" }); }} />}
      {tab === "costos" && <CostsView lookups={lookups} />}
      {loadForm && <LoadForm lookups={lookups} initial={loadForm} onClose={() => setLoadForm(null)} onSaved={() => { setLoadForm(null); reload(); }} />}
      {vehForm && <VehicleForm initial={vehForm} onClose={() => setVehForm(null)} onSaved={() => { setVehForm(null); reload(); }} />}
    </>
  );
}
