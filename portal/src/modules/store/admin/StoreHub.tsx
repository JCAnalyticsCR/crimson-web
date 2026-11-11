/* Mi Tienda por secciones (O4-01): menu lateral con nombre propio para cada parte de la tienda y una direccion por
   seccion (/mi-tienda/apariencia, /mi-tienda/catalogo...), con rutas anidadas como Contabilidad. Recargar deja al
   administrador donde estaba. Se mueven los editores que ya existian; los que no existian nacen aqui (admin/).

   Bandera settings.store.panel_v2: apagada, /mi-tienda vuelve a ser el panel de una sola pagina (Store.tsx, intacto)
   para volver atras sin desplegar. Las rutas viejas (/inventario?tab=web, /productos) siguen igual.

   Menu: 220 px en escritorio; bajo 1100 px, fila de fichas deslizable; bajo 480 px, un selector. Nada se anima salvo
   lo que se toca. */
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "../../../lib/api";
import { useSession } from "../../../app/session";
import { I, Icon, Loading } from "../../../ui/components";
import Guard from "../../../app/Guard";
import type { StoreCfg } from "../Store";
import { BASE, GROUPS, SECTIONS, sectionFor, sectionUrl, visibleSections, type SectionKey } from "./sections";
import "./storeAdmin.css";

const Store = lazy(() => import("../Store"));
const TestShop = lazy(() => import("../TestShop"));
const StoreShowcaseAdmin = lazy(() => import("../StoreShowcaseAdmin"));
const StoreResumen = lazy(() => import("./StoreResumen"));
const StoreApariencia = lazy(() => import("./StoreApariencia"));
const Publicacion = lazy(() => import("./Publicacion"));
const Paginas = lazy(() => import("./StorePaginas"));
const PagosEnvios = lazy(() => import("./PagosEnvios"));
const CatalogoWeb = lazy(() => import("./CatalogoWeb"));
const Categorias = lazy(() => import("./CategoriasEditor"));
const SeoSection = lazy(() => import("./StoreSeo"));
const Portada = lazy(() => import("./PortadaEditor"));
const Pendiente = lazy(() => import("./Pendiente"));

type Hub = {
  /** null mientras carga; undefined si la cuenta no puede leer /store (solo edita el catalogo) */
  cfg: StoreCfg | null | undefined;
  setCfg: (c: StoreCfg) => void;
  reload: () => void;
};
const HubCtx = createContext<Hub>({ cfg: undefined, setCfg: () => {}, reload: () => {} });
/** La configuracion de la tienda que ya cargo Mi Tienda (una sola lectura para todas las secciones). */
export const useHub = () => useContext(HubCtx);

export default function StoreHub() {
  const { allows, me } = useSession();
  const loc = useLocation();
  const navigate = useNavigate();
  const [cfg, setCfg] = useState<StoreCfg | null | undefined>(allows("settings.ver") ? null : undefined);
  const reload = useCallback(() => {
    if (!allows("settings.ver")) { setCfg(undefined); return; }
    api<StoreCfg>("/store").then(setCfg).catch(() => setCfg(undefined));
  }, [allows]);
  useEffect(reload, [reload]);
  const hub = useMemo(() => ({ cfg, setCfg, reload }), [cfg, reload]);

  const isAdmin = me?.role === "admin";
  const visibles = visibleSections(allows, isAdmin);
  const cur = sectionFor(loc.pathname);

  if (cfg === null) return <Loading />;
  // bandera apagada: el panel de una sola pagina de siempre (y su Tienda de pruebas)
  if (cfg && cfg.panel_v2 === false) {
    return (
      <Suspense fallback={<Loading />}>
        {loc.pathname.startsWith(`${BASE}/pruebas`) ? <Guard need="settings.configurar"><TestShop /></Guard> : <Store onPanelV2={reload} />}
      </Suspense>
    );
  }

  const el = (key: SectionKey, node: ReactNode) => {
    const s = SECTIONS.find((x) => x.key === key)!;
    if (s.adminOnly && !isAdmin) return <Navigate to={BASE} replace />;
    return <Guard need={s.need}>{node}</Guard>;
  };
  const tiendaUrl = cfg ? (cfg.domain ? `https://${cfg.domain.replace(/^https?:\/\//, "")}` : `/tienda/${cfg.slug}`) : null;

  return (
    <HubCtx.Provider value={hub}>
      <div className="page-head">
        <div><div className="meta">10 · Mi Tienda{cur && cur.key !== "resumen" ? ` · ${cur.label}` : ""}</div><h1 className="h1">{cur && cur.key !== "resumen" ? cur.label : "Mi Tienda"}</h1></div>
        <div className="page-head__actions">
          {cfg && (
            <NavLink to={`${BASE}/publicacion`} className={`badge ${cfg.published ? "badge--ok" : "badge--warn"}`} style={{ textDecoration: "none" }}>
              {cfg.published ? "Publicada" : "Sin publicar"}
            </NavLink>
          )}
          {/* O4-06: «Ver sitio» y «Ver vitrina» eran dos botones al mismo lugar */}
          {tiendaUrl && <a className="btn btn--ghost btn--sm" href={tiendaUrl} target="_blank" rel="noopener"><Icon d={I.store} />Ver tienda</a>}
        </div>
      </div>

      <div className="sh-hub">
        <nav className="sh-hub__nav" aria-label="Secciones de Mi Tienda">
          {GROUPS.map((g) => {
            const items = visibles.filter((s) => s.group === g);
            if (!items.length) return null;
            return (
              <div key={g} className="sh-hub__group">
                <span className="sh-hub__glabel">{g === "Aparte" ? "Publicación" : g}</span>
                {items.map((s) => (
                  <NavLink key={s.key} to={sectionUrl(s)} end={s.key === "resumen"} title={s.hint}
                    className={({ isActive }) => `sh-hub__link${isActive || cur?.key === s.key ? " is-active" : ""}${s.group === "Aparte" ? " sh-hub__link--aparte" : ""}`}>
                    {s.label}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <label className="sh-hub__select">
          <span className="meta">Sección</span>
          <select className="select" value={cur ? sectionUrl(cur) : BASE} onChange={(e) => navigate(e.target.value)} aria-label="Sección de Mi Tienda">
            {visibles.map((s) => <option key={s.key} value={sectionUrl(s)}>{s.label}</option>)}
          </select>
        </label>

        <div className="sh-hub__body">
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route index element={el("resumen", <StoreResumen />)} />
              <Route path="apariencia" element={el("apariencia", <StoreApariencia />)} />
              <Route path="portada" element={el("portada", <Portada />)} />
              <Route path="kits" element={el("kits", <StoreShowcaseAdmin only={["Destacados y kits"]} />)} />
              <Route path="paginas" element={el("paginas", <Paginas />)} />
              <Route path="catalogo/*" element={el("catalogo", <CatalogoWeb />)} />
              <Route path="categorias" element={el("categorias", <Categorias />)} />
              <Route path="contenido-por-aprobar" element={el("contenido", <Pendiente seccion="contenido" />)} />
              <Route path="pagos-envios" element={el("pagos-envios", <PagosEnvios />)} />
              <Route path="seo" element={el("seo", <SeoSection />)} />
              <Route path="publicacion" element={el("publicacion", <Publicacion />)} />
              <Route path="pruebas" element={el("pruebas", <TestShop />)} />
              <Route path="*" element={<Navigate to={BASE} replace />} />
            </Routes>
          </Suspense>
        </div>
      </div>
    </HubCtx.Provider>
  );
}
