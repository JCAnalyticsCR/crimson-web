// _banco.tsx — solo desarrollo: vite build de la demo toma únicamente src/demo/index.html
import { StrictMode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";
import "../../../ui/base.css"; import "../../../ui/access.css"; import "../../../ui/field.css"; import "../../demo.css";
import { DEMO_EVENT, installMockApi, type DemoNotice } from "../../mockApi";
import { rutas } from "./index";
installMockApi();
document.documentElement.classList.add("demo-on"); // la franja de la demo real (DemoShell) corre lo pegajoso

/* aviso minimo (en la demo real lo pinta DemoShell) */
function Aviso() {
  const [n, setN] = useState<DemoNotice | null>(null);
  useEffect(() => {
    const on = (e: Event) => setN((e as CustomEvent<DemoNotice>).detail);
    window.addEventListener(DEMO_EVENT, on);
    return () => window.removeEventListener(DEMO_EVENT, on);
  }, []);
  if (!n) return null;
  return (
    <div className="demo-notice" role="dialog" aria-modal="true" aria-labelledby="banco-aviso" onClick={() => setN(null)}>
      <div className="demo-notice__card" onClick={(e) => e.stopPropagation()}>
        <span className="demo-notice__eyebrow">Vista previa · demostración</span>
        <h2 id="banco-aviso">{n.title}</h2><p>{n.text}</p>
        <button type="button" className="demo-notice__btn" autoFocus onClick={() => setN(null)}>Entendido</button>
      </div>
    </div>
  );
}
const Fuera = () => (
  <div style={{ padding: 24, fontFamily: "Manrope, sans-serif" }}>
    <p>Ruta fuera del módulo de gestión (en la demo real es la tienda).</p>
    <ul>{rutas.map((r) => <li key={r.path}><Link to={r.path}>{r.path}</Link></li>)}</ul>
  </div>
);

createRoot(document.getElementById("root")!).render(<StrictMode><HashRouter><Suspense fallback={null}><Routes>
  {rutas.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}
  <Route path="/" element={<Fuera />} />
  <Route path="*" element={<Navigate to={rutas[0].path} replace />} />
</Routes></Suspense><div className="demo-ribbon" role="note"><b>Vista previa</b><span aria-hidden="true"> · </span><span>banco de pruebas de gestión</span></div><Aviso /></HashRouter></StrictMode>);
