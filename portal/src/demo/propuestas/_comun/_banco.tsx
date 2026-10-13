// _banco.tsx — SOLO desarrollo (vite build de la demo toma unicamente src/demo/index.html).
// Muestra las piezas de _comun con datos de ejemplo, como referencia para los equipos:
//   cd portal && npx vite --port 5240  y abrir localhost:5240/src/demo/propuestas/_comun/_banco.html
import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import "../../../ui/base.css";
import "../../../ui/access.css";
import "../../../ui/field.css";
import "../../demo.css";
import { installMockApi } from "../../mockApi";
import { BarraMonitor, BotonMeInteresa, HiloCarmesi, MarcoMaqueta, Pantalla, SelloPropuesta, colones, colones2, desdeAhora, fecha, hora, porcentaje, useProducto, usePaso, type Paso } from "./index";

installMockApi();

const PASOS: readonly Paso[] = [
  { id: "tienda", titulo: "En la tienda", vista: "cliente" },
  { id: "correo", titulo: "El correo", vista: "cliente" },
  { id: "celular", titulo: "El celular", vista: "cliente" },
  { id: "pdf", titulo: "El documento", vista: "cliente" },
  { id: "crimson", titulo: "Así le llega a Crimson", vista: "crimson" },
];

function Muestra() {
  const [i] = usePaso(PASOS.map((p) => p.id));
  const p53 = useProducto(53);
  return (
    <MarcoMaqueta id="A1" titulo="Banco de pruebas de _comun" etapa={1}
      bajada="Todas las piezas comunes con datos de ejemplo: marco, pasos con ?paso=, pantallas, sellos, «Me interesa» y el hilo carmesí."
      hoy="Lo que ya existe en la tienda, con borde sólido verde." conPropuesta="Lo que cambiaría con la propuesta, con borde punteado carmesí."
      pasos={PASOS} aparte={<><p>El aparte vive en la columna lateral desde 1024 px y en «Qué pasa aquí» en móvil.</p><p><Link to="/rentabilidad">Ver cuánto suma en ¿Es rentable? →</Link></p></>}>
      {i === 0 && (
        <Pantalla superficie="tienda" direccion="tienda.crimsoncr.com/p/camara-ip-hikvision-4mp-acusense-bullet-ip67-53">
          <p><b>{p53?.name || "Cargando…"}</b> · {p53 ? colones(p53.display_price) : "…"} · {p53?.availability.label}</p>
          <p style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <SelloPropuesta /> <SelloPropuesta id="A1" /> <SelloPropuesta id="A5" tamano="chico" /> <SelloPropuesta estado="existente" /> <SelloPropuesta estado="ejemplo" />
          </p>
          <p style={{ marginTop: 8 }}>{colones(238607.36)} · {colones2(238607.36)} · {porcentaje(0.346)} · {fecha(new Date())} · {fecha(new Date(), "larga")} · {fecha(desdeAhora(10), "dia")} · {hora(new Date())}</p>
          <p style={{ marginTop: 8, display: "flex", gap: 8 }}><BotonMeInteresa id="A1" /><BotonMeInteresa id="A5" variante="chip" /></p>
        </Pantalla>
      )}
      {i === 1 && <Pantalla superficie="correo"><p>De: Tienda Crimson · Para: maria@example.com</p><p><b>María, tu pedido ORD-000128 ya tiene su cuenta en Crimson</b></p></Pantalla>}
      {i === 2 && <Pantalla superficie="celular" rotulo="Mensaje al celular: WhatsApp o SMS, a definir"><p style={{ background: "#fff", padding: 10, borderRadius: 12 }}>Hola, María: el técnico de Crimson te visita el {fecha(desdeAhora(7), "larga")}.</p></Pantalla>}
      {i === 3 && <Pantalla superficie="pdf"><p><b>COTIZACIÓN COT-2026-0417 · Versión 1</b></p><p>Total {colones2(1195786.98)}</p></Pantalla>}
      {i === 4 && (
        <>
          <Pantalla superficie="admin" seccion="Levantamientos" estado="existente"><p><b>LEV-2026-0213 · Pendiente de visita</b></p><p>Costo ₡113 100 → precio ₡174 000 · margen sobre venta {porcentaje(0.35)}</p></Pantalla>
          <HiloCarmesi aqui="crimson" />
        </>
      )}
    </MarcoMaqueta>
  );
}

function Oscuro() {
  return (
    <div className="pp" style={{ background: "#15131a", color: "#f4f1ee", minHeight: "100dvh" }}>
      <BarraMonitor cam="08" rotulo="Lo que viene" derecha={<SelloPropuesta tono="oscuro" tamano="chico" />} />
      <div style={{ padding: 24, display: "flex", gap: 12, flexWrap: "wrap" }}>
        <SelloPropuesta tono="oscuro" /><SelloPropuesta tono="oscuro" id="B2" /><SelloPropuesta tono="oscuro" estado="existente" /><BotonMeInteresa id="B2" tono="oscuro" />
        <Link to="/propuestas/banco" style={{ color: "#ff8e9c" }}>Volver al banco</Link>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <div className="demo-ribbon"><span className="demo-ribbon__txt"><b>Vista previa</b><span className="demo-ribbon__largo"> · banco de pruebas de _comun</span></span></div>
      <Suspense fallback={null}>
        <Routes>
          <Route path="/propuestas/banco" element={<Muestra />} />
          <Route path="/oscuro" element={<Oscuro />} />
          <Route path="*" element={<Navigate to="/propuestas/banco" replace />} />
        </Routes>
      </Suspense>
    </HashRouter>
  </StrictMode>,
);
