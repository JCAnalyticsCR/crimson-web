/* Mi Tienda -> Pagos y envios (O4-01): tarifas, formas de pago y entrega, y datos del comercio en una sola seccion. */
import { BusinessCard, ShippingRates, useStoreCfg } from "../Store";
import CheckoutSettings from "../CheckoutSettings";
import { Loading } from "../../../ui/components";

export default function PagosEnvios() {
  const { cfg, saveCfg } = useStoreCfg();
  if (!cfg) return <Loading />;
  return (
    <>
      <ShippingRates rates={cfg.shipping_rates} onSave={(r) => saveCfg({ shipping_rates: r })} />
      <CheckoutSettings rates={cfg.shipping_rates} onRates={(r) => saveCfg({ shipping_rates: r })} />
      <BusinessCard value={cfg.business || {}} onSave={(b) => saveCfg({ business: b })} />
    </>
  );
}
