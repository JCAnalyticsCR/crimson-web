// _banco.tsx — solo desarrollo: vite build de la demo toma únicamente src/demo/index.html
import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import "../../../ui/base.css";
import "../../../ui/access.css";
import "../../../ui/field.css";
import "../../demo.css";
import { installMockApi } from "../../mockApi";
import { rutas } from "./index";
import { verificarModelo } from "./modelo";

installMockApi();
// como en la demo: el cuerpo deja el alto de la franja (aquí sin franja) para que lo pegajoso quede igual
document.documentElement.classList.add("demo-on");

/** Tabla «pasa / no pasa» de los vectores de la sección 6.7. */
function Verificacion() {
  const v = verificarModelo();
  const celda = { padding: "6px 10px", borderBottom: "1px solid rgba(21,19,26,.1)", textAlign: "left" as const, fontVariantNumeric: "tabular-nums" as const };
  return (
    <main style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 16px", fontFamily: "Manrope, system-ui, sans-serif", color: "#15131a" }}>
      <h1 style={{ fontSize: 26, fontWeight: 700 }}>Modelo de rentabilidad · vectores de prueba</h1>
      <p style={{ margin: "8px 0 16px", fontWeight: 700, color: v.ok ? "#157a41" : "#c41a2f" }}>
        {v.ok ? "✓" : "✕"} {v.pasan} de {v.total} pasan (tolerancia ±₡1). <Link to="/rentabilidad" style={{ textDecoration: "underline" }}>Volver a la página</Link>
      </p>
      <div role="region" tabIndex={0} aria-label="Vectores" style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13.5, background: "#fff" }}>
          <thead><tr>{["Resultado", "Escenario", "Prueba", "Esperado", "Obtenido"].map((t) => <th key={t} style={celda}>{t}</th>)}</tr></thead>
          <tbody>
            {v.filas.map((f, i) => (
              <tr key={i} style={{ background: f.pasa ? undefined : "#fdecee" }}>
                <td style={{ ...celda, fontWeight: 700, color: f.pasa ? "#157a41" : "#c41a2f" }}>{f.pasa ? "pasa" : "no pasa"}</td>
                <td style={celda}>{f.escenario}</td>
                <td style={celda}>{f.nombre}</td>
                <td style={celda}>{String(f.esperado)}</td>
                <td style={celda}>{String(f.obtenido)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <Suspense fallback={null}>
        <Routes>
          {rutas.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}
          <Route path="/verificar" element={<Verificacion />} />
          <Route path="*" element={<Navigate to={rutas[0].path} replace />} />
        </Routes>
      </Suspense>
    </HashRouter>
  </StrictMode>,
);
