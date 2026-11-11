/* Mi Tienda -> Publicacion y dominio (O4-01): en bloque aparte y siempre con confirmacion (O1-11b): despublicar,
   dominio, tipo de tienda y moneda dicen el efecto antes de hacerlo. Publicar es del administrador (settings.configurar
   solo lo tiene el rol administrador). La bandera para volver al panel de una pagina vive aqui. */
import { useRef } from "react";
import { Card, Field, Loading } from "../../../ui/components";
import { usePublishAsk, useStoreCfg } from "../Store";
import { useHub } from "./StoreHub";

export default function Publicacion() {
  const { cfg, saveCfg } = useStoreCfg();
  const hub = useHub();
  const pub = usePublishAsk(cfg, (patch) => saveCfg(patch).then((ok) => { if (ok) hub.reload(); return ok; }));
  const domainRef = useRef<HTMLInputElement>(null);
  if (!cfg) return <Loading />;
  const publicUrl = `/tienda/${cfg.slug}`;
  return (
    <>
      <Card title="Publicación">
        <div className="mt-stack">
          <p className="mt-lead">{cfg.published ? "La tienda está publicada: sus clientes la ven y pueden comprar." : "La tienda no está publicada: sus clientes no la ven."}</p>
          <div><button className={`btn btn--sm ${cfg.published ? "btn--soft" : "btn--crimson"}`} onClick={pub.askPublish}>{cfg.published ? "Despublicar" : "Publicar tienda"}</button></div>
        </div>
      </Card>
      <Card title="Dominio, tipo de tienda y moneda">
        <div className="mt-stack">
          <div className="grid-2">
            <Field label="Tipo de tienda" hint="Catálogo muestra productos; Tienda agrega carrito y checkout."><select className="select" value={cfg.kind} onChange={(e) => pub.askKind(e.target.value)}><option value="catalogo">Catálogo</option><option value="tienda">Tienda (carrito)</option></select></Field>
            <Field label="Moneda de la tienda" hint="Precios exhibidos y pedidos. Lo que esté en otra moneda se convierte con el tipo de cambio del día."><select className="select" value={cfg.currency || "CRC"} onChange={(e) => pub.askCurrency(e.target.value)}><option value="CRC">Colones (₡)</option><option value="USD">Dólares ($)</option></select></Field>
          </div>
          <Field label="Dominio" hint={`Gratis: ${location.host}${publicUrl} · propio: apuntar un CNAME al portal`}><input ref={domainRef} className="input" defaultValue={cfg.domain || ""} placeholder="tienda.ejemplo.com" onBlur={(e) => pub.askDomain(e.target.value, () => { if (domainRef.current) domainRef.current.value = cfg.domain || ""; })} /></Field>
        </div>
      </Card>
      <Card title="Panel">
        <div className="mt-stack">
          <p className="mt-lead">Si algo de Mi Tienda por secciones no le funciona, puede volver al panel anterior de una sola página. Nada de la tienda cambia.</p>
          <div><button className="btn btn--ghost btn--sm" onClick={() => saveCfg({ panel_v2: false }).then((ok) => { if (ok) hub.reload(); })}>Volver al panel anterior</button></div>
        </div>
      </Card>
      {pub.modal}
    </>
  );
}
