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
import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";

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

export default function StoreHost() {
  const [slug, setSlug] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    fetch(`/api/public/store-domain?host=${encodeURIComponent(location.hostname)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((j) => setSlug(j.slug))
      .catch(() => setErr(true));
  }, []);
  if (err) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", background: "#f6f2ee", color: "#5a5560", fontFamily: "Arial, Helvetica, sans-serif" }}>Tienda no disponible en este momento.</div>;
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
