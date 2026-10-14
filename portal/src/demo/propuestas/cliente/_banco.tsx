// _banco.tsx — solo desarrollo: vite build de la demo toma unicamente src/demo/index.html.
// Monta las rutas del modulo con la API simulada y un aviso minimo para ver lo que dispara notifyDemo.
import { StrictMode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import "../../../ui/base.css"; import "../../../ui/access.css"; import "../../../ui/field.css"; import "../../demo.css";
import { installMockApi } from "../../mockApi";
import { EVENTOS } from "./comun";
import { rutas } from "./index";

installMockApi();

// lo que dispara notifyDemo (EVENTOS.aviso); en la demo lo pinta DemoShell, aqui un aviso minimo
type AvisoDemo = { title: string; text: string };
function Aviso() {
  const [n, setN] = useState<AvisoDemo | null>(null);
  useEffect(() => {
    const on = (e: Event) => setN((e as CustomEvent<AvisoDemo>).detail);
    window.addEventListener(EVENTOS.aviso, on);
    return () => window.removeEventListener(EVENTOS.aviso, on);
  }, []);
  if (!n) return null;
  return (
    <div className="demo-notice" role="dialog" aria-modal="true" aria-labelledby="banco-aviso-t" onClick={() => setN(null)}>
      <div className="demo-notice__card" onClick={(e) => e.stopPropagation()}>
        <span className="demo-notice__eyebrow">Banco · aviso de demostración</span>
        <h2 id="banco-aviso-t">{n.title}</h2>
        <p>{n.text}</p>
        <button type="button" className="demo-notice__btn" autoFocus onClick={() => setN(null)}>Entendido</button>
      </div>
    </div>
  );
}

document.documentElement.classList.add("demo-on");
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <Suspense fallback={null}>
        <Routes>
          {rutas.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}
          <Route path="*" element={<Navigate to={rutas[0].path} replace />} />
        </Routes>
      </Suspense>
      <Aviso />
    </HashRouter>
  </StrictMode>,
);
