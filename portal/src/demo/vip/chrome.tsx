/* Piezas de "centro de monitoreo" compartidas por el acceso y la bienvenida: rotulo CAM, punto REC y timecode.
   Decoracion pura: todo va aria-hidden; la informacion real vive en los titulos. */
import { useEffect, useState, type ReactNode } from "react";

/** Hora local HH:MM:SS, 1 actualizacion por segundo (texto, no movimiento: sigue igual con reduced-motion). */
export function useClock(): string {
  const fmt = () => new Date().toLocaleTimeString("es-CR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const [t, setT] = useState(fmt);
  useEffect(() => {
    const id = window.setInterval(() => setT(fmt()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return t;
}

export function MonitorBar({ cam, label, right }: { cam: string; label: string; right?: ReactNode }) {
  const t = useClock();
  const d = new Date().toLocaleDateString("es-CR", { day: "2-digit", month: "2-digit", year: "numeric" });
  return (
    <div className="vip-bar">
      <span className="vip-bar__l" aria-hidden="true"><i className="vip-rec" />CAM {cam}<span className="vip-bar__sep">·</span>{label}</span>
      <span className="vip-bar__r">
        <span className="vip-tc" aria-hidden="true"><span className="vip-tc__d">{d}<span className="vip-bar__sep">·</span></span>{t}</span>
        {right}
      </span>
    </div>
  );
}

/** Escuadras de encuadre en las cuatro esquinas (el marco del monitor). */
export const Corners = () => (
  <span className="vip-corners" aria-hidden="true"><i /><i /><i /><i /></span>
);

export const Wordmark = () => (
  <span className="vip-mark"><b>Crimson</b><i aria-hidden="true" /><span>Tienda</span></span>
);
