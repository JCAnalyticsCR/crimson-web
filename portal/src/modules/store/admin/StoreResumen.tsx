/* Mi Tienda -> Resumen (O4-03), solo lectura: cuanto se ve y se puede pagar hoy, y la lista «Lista para vender» con
   lo que falta, agrupado y con el enlace a donde se corrige (la seccion o el catalogo ya filtrado). Los numeros salen
   de GET /store/health (los mismos del panel Inventario web y del snapshot de la tienda). Ningun estado se dice solo
   con color: cada punto dice «Listo» o «Falta». */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { Card, Loading } from "../../../ui/components";
import { sectionUrl, visibleSections } from "./sections";

export type Health = {
  indicadores: { publicados: number; visibles: number; pagables: number; solicitables: number; pedidos_30: number; pagados_30: number };
  avance: { listos: number; total: number };
  items: { grupo: string; key: string; ok: boolean; texto: string; to: string; n: number | null }[];
  promesas: { cancelados_30: number };
  contadores: null | Record<string, unknown>;
};

const GRUPOS = ["Vender hoy", "Confianza y legalidad", "Verse bien", "Que la encuentren"];
const fmt = (n: number) => n.toLocaleString("es-CR");

export default function StoreResumen() {
  const { allows, me } = useSession();
  const [h, setH] = useState<Health | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Health>("/store/health").then(setH).catch((e) => setErr(e.message)); }, []);
  const secs = visibleSections(allows, me?.role === "admin").filter((s) => s.key !== "resumen");
  if (err) return <Card title="Resumen"><p className="sh-lead">No se pudo leer el resumen: {err}</p></Card>;
  if (!h) return <Loading />;
  const i = h.indicadores;
  const pct = h.avance.total ? Math.round((h.avance.listos / h.avance.total) * 100) : 0;
  const tiles: [string, number, string][] = [
    ["Publicados", i.publicados, "Con «Mostrar en la tienda»"],
    ["Visibles en la tienda", i.visibles, "Lo que su cliente ve hoy"],
    ["Se pueden pagar hoy", i.pagables, "En bodega o bajo pedido con dato vigente"],
    ["Se pueden solicitar", i.solicitables, "El cliente escribe por WhatsApp"],
    ["Pedidos de los últimos 30 días", i.pedidos_30, `${fmt(i.pagados_30)} pagados`],
  ];
  return (
    <>
      <div className="rs-tiles" role="list" aria-label="Indicadores de la tienda">
        {tiles.map(([k, v, d]) => (
          <div key={k} className="rs-tile" role="listitem"><span className="rs-tile__k">{k}</span><b className="rs-tile__v">{fmt(v)}</b><span className="rs-tile__d">{d}</span></div>
        ))}
      </div>

      <Card title="Lista para vender" extra={<span className="meta" style={{ textTransform: "none" }}>{h.avance.listos} de {h.avance.total} listos</span>}>
        <div className="sh-stack">
          <div className="rs-bar" role="progressbar" aria-valuemin={0} aria-valuemax={h.avance.total} aria-valuenow={h.avance.listos} aria-label={`Lista para vender: ${h.avance.listos} de ${h.avance.total}`}><i style={{ width: `${pct}%` }} /></div>
          {GRUPOS.map((g) => {
            const xs = h.items.filter((x) => x.grupo === g);
            if (!xs.length) return null;
            const faltan = xs.filter((x) => !x.ok).length;
            return (
              <section key={g} className="rs-group" aria-label={g}>
                <h3 className="rs-group__t">{g} <span className="meta" style={{ textTransform: "none" }}>{faltan ? `${faltan} por corregir` : "todo listo"}</span></h3>
                <ul className="rs-list">
                  {/* primero lo que falta */}
                  {[...xs.filter((x) => !x.ok), ...xs.filter((x) => x.ok)].map((x) => (
                    <li key={x.key} className={`rs-item ${x.ok ? "is-ok" : "is-bad"}`}>
                      <span className={`badge ${x.ok ? "badge--ok" : "badge--warn"}`}>{x.ok ? "Listo" : "Falta"}</span>
                      <span className="rs-item__t">{x.texto}</span>
                      {!x.ok && <Link className="btn btn--ghost btn--sm" to={x.to}>Corregir</Link>}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </Card>

      <Card title="Promesas cumplidas">
        <div className="sh-stack">
          <p className="sh-lead">Pedidos cancelados en los últimos 30 días: <b>{fmt(h.promesas.cancelados_30)}</b>.</p>
          {/* O2-12 (Ola 2): fichas más vistas, clics de WhatsApp y búsquedas sin resultado, con contadores propios y anónimos */}
          {!h.contadores && <p className="sh-lead">Las fichas más vistas, los productos con más consultas por WhatsApp y las búsquedas sin resultado aparecerán aquí cuando la tienda empiece a contarlos (sin herramientas de terceros).</p>}
        </div>
      </Card>

      <div className="sh-index" aria-label="Secciones">
        {secs.map((s) => <Link key={s.key} to={sectionUrl(s)}><b>{s.label}</b><span>{s.hint}</span></Link>)}
      </div>
    </>
  );
}
