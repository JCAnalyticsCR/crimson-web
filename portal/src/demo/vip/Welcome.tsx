/* Bienvenida personal (una vez por sesion). Pantalla de DESEO: aqui el movimiento si vende, pero
   - en movil no hay intro: titular, tarjetas y "Entrar a la tienda" (barra fija abajo) usables desde el primer segundo;
   - en escritorio las tarjetas entran con un stagger corto por CSS (DUR.drawer), nada bloquea;
   - con reduced-motion todo aparece quieto. */
import { useEffect, useRef } from "react";
import { Corners, MonitorBar, Wordmark } from "./chrome";

export type Feature = { cam: string; tag: string; title: string; body: string; cta: string; path: string };

export const features = (productPath: string): Feature[] => [
  {
    cam: "01", tag: "Inventario", title: "Inventario real, con disponibilidad",
    body: "Cada producto muestra si hay en bodega y cuántas unidades quedan. Sale del mismo inventario que usa Crimson, sin cargar existencias a mano en otra plataforma.",
    cta: "Ver el catálogo", path: "/catalogo",
  },
  {
    cam: "02", tag: "Configurador", title: "“Arma tu sistema”",
    body: "El cliente responde cuatro preguntas y recibe un kit con productos reales del catálogo, con precio y disponibilidad, listo para el carrito.",
    cta: "Armar un sistema", path: "/arma-tu-sistema",
  },
  {
    cam: "03", tag: "Instalación", title: "“¿Lo instalamos nosotros?”",
    body: "En cada ficha, un acceso directo para cotizar la instalación con el producto ya identificado: la venta del equipo abre la venta del servicio.",
    cta: "Ver una ficha", path: productPath,
  },
  {
    cam: "04", tag: "Pago", title: "SINPE con comprobante, tarjeta y factura electrónica",
    body: "El cliente paga por SINPE Móvil y sube el comprobante, o paga con tarjeta. La factura electrónica sale sola, sin pasos manuales.",
    cta: "Probar una compra", path: "/catalogo",
  },
  {
    cam: "05", tag: "Pedidos", title: "“Mi pedido” y gestión de pedidos",
    body: "El cliente consulta el estado de su pedido con un enlace; Crimson lo gestiona desde su propio panel: estados, pagos y despacho en un solo lugar.",
    cta: "Ver “Mi pedido”", path: "/?v=mi-pedido",
  },
  {
    cam: "06", tag: "Google", title: "Posicionamiento en Google y enlaces con foto",
    body: "Cada producto y categoría tiene su propia dirección, título y descripción. Al compartirlo por WhatsApp o redes, el enlace se ve con su foto.",
    cta: "Ver la portada", path: "/",
  },
];

export default function Welcome({ guestName, productPath, onEnter }: {
  guestName: string;
  productPath: string;
  onEnter: (path?: string, tour?: boolean) => void;
}) {
  const h1 = useRef<HTMLHeadingElement>(null);
  useEffect(() => { h1.current?.focus({ preventScroll: true }); }, []);
  const list = features(productPath);

  return (
    <div className="vip vip-welcome">
      <header className="vip-welcome__top">
        <div className="vip-grid" aria-hidden="true" />
        <MonitorBar cam="02" label="Bienvenida" right={
          <button type="button" className="vip-btn vip-btn--crimson vip-hide-m" onClick={() => onEnter()}>Entrar a la tienda <Arrow /></button>
        } />
        <div className="vip-welcome__hero">
          <Corners />
          <Wordmark />
          <h1 ref={h1} tabIndex={-1} className="vip-welcome__title">
            {guestName}, esta es la <em>tienda propia</em> de Crimson
          </h1>
          <p className="vip-welcome__lead">
            Hecha a la medida de cómo vende Crimson: inventario, instalación, pagos y facturación conectados.
            Esto es lo que la hace distinta a Fygaro.
          </p>
          <p className="vip-note" role="note">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.5" /></svg>
            <span><b>Vista previa con productos de ejemplo:</b> nada de lo que hagás aquí genera pedidos ni pagos.</span>
          </p>
          <dl className="vip-readout" aria-label="Estado de la vista previa">
            <div><dt>Invitado</dt><dd>{guestName}</dd></div>
            <div><dt>Modo</dt><dd>Vista previa</dd></div>
            <div><dt>Catálogo</dt><dd>Productos de ejemplo</dd></div>
            <div><dt>Pedidos y pagos</dt><dd>Simulados</dd></div>
            <div><dt>Factura electrónica</dt><dd>Simulada</dd></div>
          </dl>
        </div>
      </header>

      <main className="vip-welcome__body">
        <ol className="vip-cards" aria-label="Qué hace distinta a esta tienda">
          {list.map((f, i) => (
            <li key={f.cam} className="vip-card" style={{ ["--i" as string]: i }}>
              <button type="button" className="vip-card__link" onClick={() => onEnter(f.path)}>
                <span className="vip-card__bar" aria-hidden="true"><span><i className="vip-rec vip-rec--still" />CAM {f.cam}</span><span>{f.tag}</span></span>
                <span className="vip-card__body">
                  <b className="vip-card__title">{f.title}</b>
                  <span className="vip-card__text">{f.body}</span>
                  <span className="vip-card__cta">{f.cta} <Arrow /></span>
                </span>
              </button>
            </li>
          ))}
        </ol>

        <div className="vip-welcome__tour">
          <div>
            <b>¿Preferís que te lleve de la mano?</b>
            <span>Un recorrido de seis pasos por lo más importante. Lo podés cerrar cuando querás.</span>
          </div>
          <span className="vip-welcome__acts">
            <button type="button" className="vip-btn vip-btn--ghost" onClick={() => onEnter("/", true)}>Hacer el recorrido guiado</button>
            <button type="button" className="vip-btn vip-btn--crimson vip-hide-m" onClick={() => onEnter()}>Entrar a la tienda <Arrow /></button>
          </span>
        </div>

        <p className="vip-sign">Diseñado y desarrollado por JC Analytics</p>
      </main>

      {/* Movil: la salida principal anclada abajo, visible desde el primer segundo (respeta el home indicator). */}
      <div className="vip-enterbar">
        <button type="button" className="vip-btn vip-btn--crimson vip-btn--block" onClick={() => onEnter()}>Entrar a la tienda <Arrow /></button>
      </div>
    </div>
  );
}

export const Arrow = () => (
  <svg className="vip-arrow" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
);
