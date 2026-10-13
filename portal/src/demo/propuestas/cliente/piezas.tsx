/* Piezas propias del equipo cliente, compartidas por B1 y B2: el armazon del portal del cliente (imita el
   portal real de modules/cliente: logo, quien entra y sus secciones), la foto de producto que sale del catalogo
   de la demo (useProducto -> mockApi, nada sale a la red salvo la imagen del snapshot) y un icono de trazo. */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { IC } from "../../../modules/store/storeKit";
import { useProducto } from "./comun";
import { MARIA } from "./datos";

/** Icono de trazo 2px con los mismos dibujos de la vitrina (IC). Propio para no heredar el tamano de .sh-icon. */
export const Ico = ({ d, size = 18 }: { d: string; size?: number }) => (
  <svg className="cli-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={d} /></svg>
);
export { IC };

export const LOGO = `${import.meta.env.BASE_URL}logo.png`;

export type SeccionPortal = "compras" | "equipos" | "soporte" | "plan";
const TABS: { k: SeccionPortal; t: string; to: string; ico: string }[] = [
  { k: "compras", t: "Mis compras", to: "/propuestas/b1?paso=compras", ico: IC.bag },
  { k: "equipos", t: "Mis equipos", to: "/propuestas/b1?paso=equipos", ico: IC.cctv },
  { k: "soporte", t: "Soporte", to: "/propuestas/b1?paso=falla", ico: IC.chat },
  { k: "plan", t: "Mi plan", to: "/propuestas/b2?paso=plan", ico: IC.shield },
];

/** Portal del cliente (maqueta): cabecera con la cuenta de Maria y las cuatro secciones de la propuesta. */
export function PortalCliente({ actual, children }: { actual: SeccionPortal; children: ReactNode }) {
  return (
    <div className="cli-portal">
      <header className="cli-portal__top">
        <img src={LOGO} alt="Crimson" width={104} height={24} decoding="async" />
        <div className="cli-portal__who"><b>{MARIA.nombre}</b><span>Portal de clientes · administradora</span></div>
        <span className="cli-portal__av" aria-hidden="true">{MARIA.iniciales}</span>
      </header>
      <nav className="cli-portal__tabs" aria-label="Secciones del portal de María (maqueta)">
        {TABS.map((t) => (
          <Link key={t.k} to={t.to} aria-current={t.k === actual ? "page" : undefined}>
            <Ico d={t.ico} size={16} /><span>{t.t}</span>
          </Link>
        ))}
      </nav>
      <div className="cli-portal__body">{children}</div>
    </div>
  );
}

/** Foto del producto real del catalogo de la demo; mientras llega (o si no hay), el contorno de una camara. */
export function FotoProducto({ id, tam = "md", alt = "" }: { id: number; tam?: "sm" | "md"; alt?: string }) {
  const p = useProducto(id);
  const px = tam === "sm" ? 44 : 72;
  return (
    <span className={`cli-foto cli-foto--${tam}`}>
      {p?.image
        ? <img src={p.image} alt={alt} width={px} height={px} loading="lazy" decoding="async" />
        : <Ico d={IC.cam} size={tam === "sm" ? 18 : 26} />}
    </span>
  );
}

/** Encabezado de seccion dentro de una pantalla de maqueta (rotulo mono + titulo). */
export function CabezaSeccion({ rotulo, titulo, children }: { rotulo: string; titulo: string; children?: ReactNode }) {
  return (
    <div className="cli-sec">
      <div><p className="cli-meta">{rotulo}</p><h3 className="cli-sec__h">{titulo}</h3></div>
      {children}
    </div>
  );
}
