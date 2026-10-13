/* Detras de escena · indice. Las pantallas con las que Crimson maneja la tienda (ya construidas, con datos de
   ejemplo) y, aparte y marcado como propuesta, lo que vendria aqui (C1, C2, D2). */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { MarcoMaqueta, SelloPropuesta, leerPedidosDemo } from "./comun";
import { IC, Ico, SubnavDE } from "./ui";
import "./gestion.css";

const TARJETAS = [
  { to: "/detras-de-escena/pedidos", n: "11", sec: "Pedidos", t: "Pedidos de la tienda", d: "Comprobante privado, confirmar pago, factura automática, estados con aviso al cliente y despacho." },
  { to: "/detras-de-escena/inventario", n: "06", sec: "Inventarios", t: "Inventario web", d: "Lo publicado contra el inventario real, reservas por pedido y alertas. Nunca se vende dos veces la última unidad." },
  { to: "/detras-de-escena/mi-tienda", n: "10", sec: "Mi Tienda", t: "Mi Tienda", d: "Marca, vitrina, páginas, pagos y envíos: Crimson cambia la tienda sin programador. Cambiá el titular y miralo en la portada." },
];

export default function DetrasIndice() {
  const demo = useMemo(() => leerPedidosDemo()[0] || null, []);
  return (
    <MarcoMaqueta id="DE" estado="existente" titulo="Detrás de escena"
      bajada="Así maneja Crimson la tienda desde su plataforma. Estas pantallas ya existen; aquí las ves con datos de ejemplo. Tocá lo que querás: nada cambia nada real.">
      <SubnavDE actual="indice" />
      {demo && (
        <p className="ges ges-demo-aviso" role="status">
          <span>Tu pedido de prueba <b className="mono">{demo.number}</b> ya está en Pedidos.</span>
          <Link to={`/detras-de-escena/pedidos?pedido=${encodeURIComponent(demo.number)}`}>Verlo del lado de Crimson →</Link>
        </p>
      )}
      <ul className="ges ges-indice">
        {TARJETAS.map((c) => (
          <li key={c.to}>
            <Link to={c.to} className="ges-ind">
              <span className="ges-ind__bar" aria-hidden="true">● Plataforma Crimson · {c.n} · {c.sec}</span>
              <span className="ges-ind__sello"><SelloPropuesta estado="existente" tamano="chico" /></span>
              <span className="ges-ind__t">{c.t}</span>
              <span className="ges-ind__d">{c.d}</span>
              <span className="ges-ind__go">Abrir <Ico d={IC.arrow} /></span>
            </Link>
          </li>
        ))}
      </ul>

      <section className="ges ges-vendria" aria-labelledby="ges-vendria-t">
        <div className="ges-vendria__cab">
          <h3 id="ges-vendria-t">Lo que vendría aquí</h3>
          <SelloPropuesta />
        </div>
        <p className="ges-vendria__b">Ideas para la plataforma que no están incluidas todavía. Se ven aparte para que nunca se confundan con lo que ya funciona.</p>
        <ul>
          <li>
            <SelloPropuesta id="C1" tamano="chico" />
            <div><b>Tablero de la tienda</b><span className="ges-vendria__d">Visitas, lo más visto, carritos abandonados, conversión, ventas por canal y margen real por pedido.</span>
              <Link id="ges-enlace-c1" to="/propuestas/c1" className="ges-vendria__a">Ver la maqueta del tablero →</Link></div>
          </li>
          <li>
            <SelloPropuesta id="C2" tamano="chico" />
            <div><b>Alertas al celular</b><span className="ges-vendria__d">Aviso inmediato de pedido nuevo, comprobante subido y existencias bajas de lo más vendido.</span></div>
          </li>
          <li>
            <SelloPropuesta id="D2" tamano="chico" />
            <div><b>Orden de compra automática</b><span className="ges-vendria__d">Si un pedido queda «requiere compra», la orden al proveedor se arma sola para revisar y enviar.</span></div>
          </li>
        </ul>
      </section>
    </MarcoMaqueta>
  );
}
