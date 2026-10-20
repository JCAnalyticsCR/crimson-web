/* Bienvenida personal (una vez por sesion). Pantalla de DESEO: aqui el movimiento si vende, pero
   - en movil no hay intro: titular, tarjetas y "Entrar a la tienda" (barra fija abajo) usables desde el primer segundo;
   - en escritorio las tarjetas entran con un stagger corto por CSS (DUR.drawer), nada bloquea;
   - con reduced-motion todo aparece quieto. */
import { useEffect, useRef } from "react";
import { SelloPropuesta } from "../propuestas/_comun/sello";
import { PROPUESTAS } from "../propuestas/registro";
import { Corners, MonitorBar, Wordmark } from "./chrome";
import { tourSteps } from "./Tour";

const NUMEROS = ["cero", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce"];
const enPalabras = (n: number) => NUMEROS[n] || String(n);

export type Feature = { cam: string; tag: string; title: string; body: string; cta: string; path: string };

export const features = (productPath: string): Feature[] => [
  {
    cam: "01", tag: "Inventario", title: "Inventario real, con disponibilidad",
    body: "Cada producto muestra si está disponible o bajo pedido según el inventario del proveedor. Sale del mismo catálogo que usa Crimson, sin cargar productos a mano en otra plataforma. La disponibilidad refleja el estado al día de la copia del catálogo.",
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
    body: "El cliente paga por SINPE Móvil y sube el comprobante, o con tarjeta por ONVO (ya integrado; falta contratarlo). La factura electrónica sale sola al confirmarse el pago, en cuanto se contrate el proveedor de factura electrónica.",
    cta: "Probar una compra", path: "/catalogo",
  },
  {
    cam: "05", tag: "Pedidos", title: "“Mi pedido” y gestión de pedidos",
    body: "El cliente consulta el estado de su pedido con un enlace; Crimson lo gestiona desde su propio panel: estados, pagos y despacho en un solo lugar.",
    cta: "Ver un pedido de ejemplo", path: "/?pedido=ejemplo-enviado",
  },
  {
    cam: "06", tag: "Google", title: "Posicionamiento en Google y enlaces con foto",
    body: "Cada producto y categoría tiene su propia dirección, título y descripción. Al compartirlo por WhatsApp o redes, el enlace se ve con su foto.",
    cta: "Ver la portada", path: "/",
  },
  {
    cam: "07", tag: "Administración", title: "Detrás de escena",
    body: "Las pantallas con las que Crimson maneja la tienda: pedidos web con comprobante privado, inventario web con reservas y Mi Tienda. Navegables, con datos de ejemplo.",
    cta: "Ver cómo lo maneja Crimson", path: "/detras-de-escena",
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
  const pasos = enPalabras(tourSteps("").length);

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
            <span><b>Vista previa con el catálogo público de referencia:</b> nada de lo que hagás aquí genera pedidos ni pagos.</span>
          </p>
          <dl className="vip-readout" aria-label="Estado de la vista previa">
            <div><dt>Invitado</dt><dd>{guestName}</dd></div>
            <div><dt>Modo</dt><dd>Vista previa</dd></div>
            <div><dt>Catálogo</dt><dd>Público, de referencia</dd></div>
            <div><dt>Disponibilidad</dt><dd>Simulada</dd></div>
            <div><dt>Pedidos y pagos</dt><dd>Simulados</dd></div>
            <div><dt>Factura electrónica</dt><dd>Simulada</dd></div>
            <div><dt>Propuestas</dt><dd>{PROPUESTAS.length}, no incluidas aún</dd></div>
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

        {/* Y despues: propuestas (NINGUNA incluida aun) y los numeros. Tinta + borde punteado carmesi = propuesta. */}
        <section className="vip-next" aria-labelledby="vip-next-t">
          <h2 id="vip-next-t" className="vip-next__t">Y después, ¿qué?</h2>
          <ul className="vip-next__list">
            <li className="vip-next__item" style={{ ["--i" as string]: 7 }}>
              <button type="button" className="vip-next__card" onClick={() => onEnter("/propuestas")}>
                <span className="vip-next__bar" aria-hidden="true"><span><i className="vip-rec vip-rec--still" />CAM 08</span><span>Propuestas</span></span>
                <span className="vip-next__body">
                  <SelloPropuesta tamano="chico" tono="oscuro" />
                  <b className="vip-next__title">Diecisiete ideas para que cada venta abra la siguiente</b>
                  <span className="vip-next__text">Instalación con fecha y precio, portal del cliente, plan de mantenimiento, tablero y más. Todas marcadas como propuesta: nada de esto está incluido todavía.</span>
                  <span className="vip-next__cta">Ver lo que viene <Arrow /></span>
                </span>
              </button>
            </li>
            <li className="vip-next__item" style={{ ["--i" as string]: 8 }}>
              <button type="button" className="vip-next__card" onClick={() => onEnter("/rentabilidad")}>
                <span className="vip-next__bar" aria-hidden="true"><span><i className="vip-rec vip-rec--still" />CAM 09</span><span>Números</span></span>
                <span className="vip-next__body">
                  <b className="vip-next__title">Hacé las cuentas con tus números</b>
                  <span className="vip-next__text">Un modelo transparente: cada supuesto es un ejemplo que podés cambiar, las fórmulas están a la vista y el margen sobre venta de 35 % es el de los levantamientos de Crimson (en el equipo de tienda, un ejemplo que podés cambiar).</span>
                  <span className="vip-next__cta">Hacer las cuentas <Arrow /></span>
                </span>
              </button>
            </li>
          </ul>
        </section>

        <div className="vip-welcome__tour">
          <div>
            <b>¿Preferís que te lleve de la mano?</b>
            <span>Un recorrido de {pasos} pasos, de la vitrina a los números. Lo podés cerrar cuando querás.</span>
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
