/* Medicion (Google Analytics 4) SOLO con consentimiento explicito. Si Mi Tienda -> Vitrina no tiene un id de GA4,
   no aparece nada y no se descarga nada. Con id: un aviso pequeno; hasta que la persona acepta, cero scripts de
   terceros. La decision queda en este navegador. */
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { safeStore, usePath, useStore } from "./storeKit";

type GtagWin = Window & { dataLayer?: unknown[]; gtag?: (...a: unknown[]) => void };
const KEY = "crimson-shop:medicion";

function loadGa(id: string) {
  const w = window as GtagWin;
  if (w.gtag) return;
  w.dataLayer = w.dataLayer || [];
  w.gtag = function gtag() { w.dataLayer!.push(arguments); }; // eslint-disable-line prefer-rest-params
  w.gtag("js", new Date());
  w.gtag("config", id, { anonymize_ip: true, send_page_view: false });
  const s = document.createElement("script");
  s.async = true;
  s.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
  document.head.appendChild(s);
}

export default function Consent() {
  const ctx = useStore();
  const to = usePath();
  const loc = useLocation();
  const [ga, setGa] = useState<string | null>(null);
  const [choice, setChoice] = useState<string | null>(() => safeStore.get(KEY));
  useEffect(() => { ctx.api<{ analytics: { ga4_id: string | null } }>("/site").then((r) => setGa(r.analytics.ga4_id)).catch(() => setGa(null)); }, [ctx]);
  useEffect(() => { if (ga && choice === "si") loadGa(ga); }, [ga, choice]);
  useEffect(() => {
    const w = window as GtagWin;
    if (ga && choice === "si" && w.gtag) w.gtag("event", "page_view", { page_path: loc.pathname + loc.search, page_title: document.title });
  }, [loc.pathname, loc.search, ga, choice]);
  if (!ga || choice) return null;
  const decide = (v: "si" | "no") => { safeStore.set(KEY, v); setChoice(v); };
  return (
    <div className="vt-consent" role="region" aria-label="Medición de visitas">
      <p>¿Nos dejás medir las visitas con Google Analytics para mejorar la tienda? No usamos publicidad. <Link to={to("/info/privacidad")}>Privacidad</Link></p>
      <div>
        <button className="sh-btn sh-btn--sm sh-btn--ghost" onClick={() => decide("no")}>No, gracias</button>
        <button className="sh-btn sh-btn--sm" onClick={() => decide("si")}>Aceptar</button>
      </div>
    </div>
  );
}
