/* Tienda FALSA solo para el banco de pruebas de la capa VIP: reproduce las rutas y las clases que la guia
   senala en la vitrina real (.sh-head, .sh-search, #vt-title, .vt-install, .vt-q, .ck-steps, .vt-stickybuy),
   con los estilos reales de la tienda. No es la demo: la demo la arma main-demo.tsx con la vitrina de verdad. */
import { useState } from "react";
import { Link, Route, Routes, useLocation, useSearchParams } from "react-router-dom";
import "../../../modules/store/shop.css";
import "../../../modules/store/vitrina.css";

const products = [
  { slug: "camara-domo-ds-2cd1143-12", name: "Cámara domo IP 4 MP DS-2CD1143", price: "₡38 900", stock: 7 },
  { slug: "camara-bala-ds-2cd1043-13", name: "Cámara bala IP 4 MP DS-2CD1043", price: "₡36 500", stock: 3 },
  { slug: "nvr-8-canales-14", name: "Grabador NVR 8 canales PoE", price: "₡89 900", stock: 5 },
  { slug: "disco-duro-2tb-15", name: "Disco duro 2 TB videovigilancia", price: "₡54 000", stock: 12 },
];

function Grid() {
  return (
    <div className="sh-grid">
      {products.map((p) => (
        <div key={p.slug} className="sh-card">
          <Link className="sh-card__link" to={`/p/${p.slug}`}>
            <div className="sh-card__img"><span className="sh-noimg">Foto</span></div>
            <div className="sh-card__body"><span className="sh-card__name">{p.name}</span><span className="sh-meta">{p.stock} en bodega</span></div>
          </Link>
          <div className="sh-card__body"><div className="sh-card__foot"><span className="sh-price">{p.price}</span></div></div>
        </div>
      ))}
    </div>
  );
}

function Product() {
  const slug = useLocation().pathname.split("/p/")[1] || "";
  const p = products.find((x) => x.slug === slug) || products[0];
  return (
    <div className="sh-wrap" style={{ paddingBlock: 24 }}>
      <h1 style={{ fontFamily: "var(--serif)", margin: "0 0 12px" }}>{p.name}</h1>
      <div className="sh-card__img" style={{ maxWidth: 420, background: "#fff", border: "1px solid var(--hair)" }}><span className="sh-noimg">Galería</span></div>
      <p className="sh-price" style={{ margin: "16px 0" }}>{p.price}</p>
      <button className="sh-btn sh-btn--crimson sh-btn--block" style={{ maxWidth: 420 }}>Agregar al carrito</button>
      <Link className="vt-install" to="/asesoria" style={{ marginTop: 14, maxWidth: 420 }}>
        <span><b>¿Lo instalamos nosotros?</b> <small>Técnicos de Crimson · te cotizamos la instalación sin compromiso</small></span>
      </Link>
      <div style={{ height: 600 }} />
      <div className="vt-stickybuy"><span className="sh-price">{p.price}</span><button className="sh-btn sh-btn--crimson">Agregar al carrito</button></div>
    </div>
  );
}

function Other() {
  return <div className="sh-wrap" style={{ paddingBlock: 24 }}><p>Ruta {useLocation().pathname}</p></div>;
}

export default function FakeStore() {
  const [sp] = useSearchParams();
  const v = sp.get("v");
  const [cart] = useState(1);
  return (
    <div className="shop">
      <header className="sh-head">
        <div className="sh-wrap sh-head__row">
          <Link className="sh-logo" to="/"><b>Crimson</b><span className="sh-meta">Tienda</span></Link>
          <div className="sh-search"><input type="search" placeholder="Buscar productos" defaultValue={sp.get("q") || ""} aria-label="Buscar" /></div>
          <div className="sh-head__actions"><Link className="sh-btn sh-btn--ghost sh-btn--sm" to="/?v=pagar">Carrito ({cart})</Link></div>
        </div>
        <div className="sh-wrap sh-head__search-m"><div className="sh-search"><input type="search" placeholder="Buscar productos" aria-label="Buscar" /></div></div>
      </header>
      <main id="contenido" tabIndex={-1} style={{ outline: "none", paddingBottom: 40 }}>
        {v === "pagar" ? (
          <div className="sh-wrap" style={{ paddingBlock: 24 }}><ol className="ck-steps" style={{ margin: 0 }}><li>Datos</li><li>Entrega</li><li>Pago: SINPE Móvil · Tarjeta</li></ol><div style={{ height: 300 }} /></div>
        ) : v === "mi-pedido" ? (
          <div className="sh-wrap" style={{ paddingBlock: 24 }}><form><label>Número de pedido <input /></label></form></div>
        ) : (
          <Routes>
            <Route index element={<section className="vt-hero"><div className="sh-wrap" style={{ paddingBlock: 40, position: "relative", zIndex: 1 }}><h1 id="vt-title" style={{ fontFamily: "var(--serif)", fontSize: 44, margin: 0 }}>Equipo profesional, <em style={{ color: "var(--crimson)", fontStyle: "normal" }}>listo para instalar</em>.</h1></div></section>} />
            <Route path="catalogo" element={<div className="sh-wrap" style={{ paddingBlock: 24 }}><Grid /></div>} />
            <Route path="buscar" element={<div className="sh-wrap" style={{ paddingBlock: 24 }}><p className="sh-meta">Resultados para “{sp.get("q")}”</p><Grid /></div>} />
            <Route path="p/:ref" element={<Product />} />
            <Route path="arma-tu-sistema" element={<div className="sh-wrap" style={{ paddingBlock: 24 }}><form className="vt-q"><p>¿Dónde lo vas a instalar?</p><p>¿Cuántas cámaras?</p></form></div>} />
            <Route path="*" element={<Other />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
