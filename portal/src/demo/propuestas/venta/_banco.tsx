// _banco.tsx — solo desarrollo: vite build de la demo toma únicamente src/demo/index.html
import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import "../../../ui/base.css"; import "../../../ui/access.css"; import "../../../ui/field.css"; import "../../demo.css";
import { installMockApi } from "../../mockApi";
import { rutas } from "./index";
installMockApi();
createRoot(document.getElementById("root")!).render(<StrictMode><HashRouter><Suspense fallback={null}><Routes>
  {rutas.map((r) => <Route key={r.path} path={r.path} element={r.element} />)}
  <Route path="*" element={<Navigate to={rutas[0].path} replace />} />
</Routes></Suspense></HashRouter></StrictMode>);
