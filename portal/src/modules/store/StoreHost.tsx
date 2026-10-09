/* Tienda en dominio propio (p. ej. tienda.crimsoncr.com con CNAME al portal). El dominio se resuelve contra
   Mi Tienda -> Dominio, y las direcciones viejas de Fygaro se traducen a las nuevas para que los enlaces ya
   compartidos (redes, WhatsApp, Google) sigan llevando al mismo producto:
     /products/<uuid>/            -> ?p=<id>     (mapa que deja el importador)
     /products/category/<id>/     -> ?c=<id>
     /products/search/?keywords=x -> ?q=x
     /terms  /policies            -> ?legal=terms|privacy
     /products/, /page/..., resto -> portada */
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
      .then((j) => setTo(j?.product_id ? `/?p=${j.product_id}` : j?.category_id ? `/?c=${j.category_id}` : "/"))
      .catch(() => setTo("/"));
  }, [slug, kind, ref]);
  return to ? <Navigate to={to} replace /> : <Spin />;
}

function Search() {
  const s = new URLSearchParams(useLocation().search);
  const kw = s.get("keywords");
  if (kw) return <Navigate to={`/?q=${encodeURIComponent(kw)}`} replace />;
  const c = s.get("category");
  return c ? <Navigate to={`/products/category/${encodeURIComponent(c)}/`} replace /> : <Navigate to="/" replace />;
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
  if (err) return <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", background: "#f6f2ee", color: "#5a5560", fontFamily: "Manrope, system-ui, sans-serif" }}>Tienda no disponible en este momento.</div>;
  if (!slug) return <Spin />;
  return (
    <Suspense fallback={<Spin />}>
      <Routes>
        <Route path="/" element={<PublicStore slug={slug} hostMode />} />
        <Route path="/products/search/*" element={<Search />} />
        <Route path="/products/category/:ref/*" element={<Legacy slug={slug} kind="category" />} />
        <Route path="/products/:ref/*" element={<Legacy slug={slug} kind="product" />} />
        <Route path="/terms/*" element={<Navigate to="/?legal=terms" replace />} />
        <Route path="/policies/*" element={<Navigate to="/?legal=privacy" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
