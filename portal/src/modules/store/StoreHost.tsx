/* Tienda en dominio propio (p. ej. tienda.crimsoncr.com con CNAME al portal). El dominio se resuelve contra
   Mi Tienda -> Dominio, y las direcciones viejas de Fygaro se traducen a las nuevas para que los enlaces ya
   compartidos (redes, WhatsApp, Google) sigan llevando al mismo producto:
     /products/<uuid>/            -> /p/<nombre>-<id>  (mapa que deja el importador)
     /products/category/<id>/     -> /c/<categoria>
     /products/search/?keywords=x -> /buscar?q=x
     /products/                   -> /catalogo
     /terms  /policies            -> /info/terminos  /info/privacidad
     /page/solicitud-de-asesoria… -> /asesoria
   En produccion nginx ya responde estas con un 301 del API (/public/seo/legacy) antes de llegar aqui; esto
   cubre el servidor de desarrollo y cualquier enlace que se escape. */
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { readBoot } from "./boot";
import { relevoDelServidor } from "./ssrBody";

const PublicStore = lazy(() => import("./PublicStore"));

/** Hosts que muestran la tienda en la raiz. VITE_STORE_HOSTS="tienda.crimsoncr.com,otra.com"; tienda.* por convencion. */
export function isStoreHost(host = location.hostname): boolean {
  const list = String(import.meta.env.VITE_STORE_HOSTS || "").split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
  const h = host.toLowerCase();
  return list.includes(h) || h.startsWith("tienda.");
}

const Spin = () => <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", background: "#f6f2ee" }}><span className="spinner" /></div>;

function Legacy({ slug, kind }: { slug: string; kind: "product" | "category" }) {
  const { ref } = useParams();
  const [to, setTo] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/public/store/${slug}/legacy/${kind}/${encodeURIComponent(ref || "")}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setTo(j?.product_id ? `/p/producto-${j.product_id}` : j?.category_id ? `/c/${j.category_id}` : j?.search ? `/buscar?q=${encodeURIComponent(j.search)}` : "/catalogo"))
      .catch(() => setTo("/catalogo"));
  }, [slug, kind, ref]);
  return to ? <Navigate to={to} replace /> : <Spin />;
}

function Search() {
  const s = new URLSearchParams(useLocation().search);
  const kw = s.get("keywords");
  if (kw) return <Navigate to={`/buscar?q=${encodeURIComponent(kw)}`} replace />;
  const c = s.get("category");
  return c ? <Navigate to={`/products/category/${encodeURIComponent(c)}/`} replace /> : <Navigate to="/catalogo" replace />;
}

function Page() {
  const { ref = "" } = useParams();
  return <Navigate to={ref.includes("asesoria") ? "/asesoria" : "/"} replace />;
}

/** espera antes del reintento automatico cuando falla la primera llamada (O1-18) */
export const REINTENTO_MS = 2000;

/** «No pudimos cargar la tienda.» con salida: el foco va directo a «Reintentar» (role alert lo anuncia). */
function NoCargo({ onRetry }: { onRetry: () => void }) {
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => btn.current?.focus(), []);
  return (
    <div role="alert" style={{ minHeight: "100dvh", display: "grid", placeItems: "center", alignContent: "center", gap: 16, padding: 16, background: "#f6f2ee", color: "#3d3842", fontFamily: "Arial, Helvetica, sans-serif", textAlign: "center" }}>
      <p style={{ margin: 0, fontSize: 18 }}>No pudimos cargar la tienda.</p>
      <button ref={btn} type="button" onClick={onRetry} style={{ minHeight: 44, padding: "0 24px", borderRadius: 999, border: 0, background: "#15131a", color: "#fff", font: "700 16px Arial, Helvetica, sans-serif", cursor: "pointer" }}>Reintentar</button>
    </div>
  );
}

export default function StoreHost() {
  /* O2-01a: con el bloque de arranque del HTML (boot.ts) el slug ya esta: no se espera /store-domain. El servidor lo
     resolvio con el mismo dominio y la misma regla, asi que no hace falta volver a preguntarlo. */
  const [slug, setSlug] = useState<string | null>(() => readBoot()?.slug ?? null);
  const [err, setErr] = useState(false);
  const [intento, setIntento] = useState(0);
  // O2-01b: si el servidor mando el contenido en el HTML (#sh-ssr), la vitrina lo reemplaza cuando ya pinto lo suyo
  useEffect(() => relevoDelServidor(), []);
  useEffect(() => {
    if (slug) return;
    // O1-18: un tropiezo de red no deja la tienda en blanco: un reintento automatico a los 2 s y, si vuelve a fallar,
    // un aviso con «Reintentar» (que repite la misma secuencia)
    let alive = true;
    let t: number | undefined;
    const pedir = () => fetch(`/api/public/store-domain?host=${encodeURIComponent(location.hostname)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))));
    pedir()
      .catch(() => new Promise((res) => { t = window.setTimeout(res, REINTENTO_MS); }).then(pedir))
      .then((j) => { if (alive) setSlug(j.slug); })
      .catch(() => { if (alive) setErr(true); });
    return () => { alive = false; window.clearTimeout(t); };
  }, [intento]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <NoCargo onRetry={() => { setErr(false); setIntento((n) => n + 1); }} />;
  if (!slug) return <Spin />;
  return (
    <Suspense fallback={<Spin />}>
      <Routes>
        <Route path="/products/search/*" element={<Search />} />
        <Route path="/products/category/:ref/*" element={<Legacy slug={slug} kind="category" />} />
        <Route path="/products" element={<Navigate to="/catalogo" replace />} />
        <Route path="/products/:ref/*" element={<Legacy slug={slug} kind="product" />} />
        <Route path="/terms/*" element={<Navigate to="/info/terminos" replace />} />
        <Route path="/policies/*" element={<Navigate to="/info/privacidad" replace />} />
        <Route path="/page/:ref/*" element={<Page />} />
        <Route path="/*" element={<PublicStore slug={slug} hostMode />} />
      </Routes>
    </Suspense>
  );
}
