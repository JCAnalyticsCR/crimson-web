/* Mi Tienda -> Portada: titular, bajada, banners y sellos de confianza en un solo borrador (O4-02): nada se guarda al
   salir de un campo ni con botones por bloque; todo sale junto con «Guardar y publicar» (y se puede deshacer).
   Editar el titular y un banner en la misma sesion no pierde nada: es un solo guardado. */
import { useState } from "react";
import { Card, Field, I, Icon, Loading } from "../../../ui/components";
import { ImageField } from "../../../ui/MediaPicker";
import { SaveBar } from "./SaveBar";
import { moveItem } from "./fields";
import { useDraft } from "./useDraft";

export type Banner = { id?: string; image: string; title: string; text: string; link: string; cta: string; position: number; active: boolean };
export type Trust = { icon: string; title: string; text: string };
export type Seccion = { key: string; visible: boolean; title: string };
type Portada = { headline: string; subline: string; banners: Banner[]; trust: Trust[]; sections: Seccion[] };
/* O4-07: nombres de las secciones de la portada (mismo orden por defecto que el API, services/store_showcase) */
const SECCIONES: Record<string, string> = {
  hero: "Portada principal", sellos: "Sellos de confianza", banners: "Banners", kits: "Kits por necesidad", categorias: "Categorías",
  seleccion: "Selección (destacados)", configurador: "Armá tu sistema", recientes: "Recién agregados", marcas: "Marcas", asesoria: "Asesoría",
  listos: "Listos para entregar", coleccion: "Colección",
};
export const PORTADA_LABELS: Record<string, string> = { headline: "Titular", subline: "Bajada", banners: "Banners", trust: "Sellos", sections: "Secciones de la portada" };
const ICONOS: [string, string][] = [["shield", "Escudo"], ["tool", "Herramienta"], ["truck", "Camión"], ["doc", "Documento"], ["chat", "Conversación"], ["clock", "Reloj"]];
/* O1-04 d: sin imagen o con el titulo vacio o «Nuevo banner», la tienda no lo pinta (y el API lo guarda inactivo) */
const incompleto = (b: Banner) => !b.image.trim() || ["", "nuevo banner"].includes(b.title.trim().toLowerCase());

export default function PortadaEditor() {
  /* lo que hoy no se puede comprar de cada seccion (destacados y lo que arma cada kit), para avisarlo al lado */
  const [noVende, setNoVende] = useState<Record<string, number>>({});
  const [arrastra, setArrastra] = useState<number | null>(null);
  const d = useDraft<Portada>({
    key: "portada:showcase",
    url: "/store/showcase",
    read: (r) => {
      const vende = (st?: string | null) => st === "en_bodega" || st === "bajo_pedido";
      const fp = (r.featured_products as { state?: string | null }[]) || [];
      const kp = (r.kits_preview as { lines: { state: string | null }[] }[]) || [];
      setNoVende({ seleccion: fp.filter((x) => !vende(x.state)).length, kits: kp.reduce((a, k) => a + k.lines.filter((ln) => !vende(ln.state)).length, 0) });
      return { headline: String(r.headline ?? ""), subline: String(r.subline ?? ""), banners: (r.banners as Banner[]) || [], trust: (r.trust as Trust[]) || [], sections: (r.sections as Seccion[]) || [] };
    },
    // el orden de los banners es el de la lista
    body: (x, changed, version) => ({ ...Object.fromEntries(changed.map((k) => [k, k === "banners" ? x.banners.map((b, i) => ({ ...b, position: i + 1 })) : x[k]])), version }),
  });
  if (d.error) return <Card title="Portada"><p className="mt-lead">{d.error}</p></Card>;
  if (!d.draft) return <Loading />;
  const p = d.draft;
  const setB = (i: number, patch: Partial<Banner>) => d.set({ banners: p.banners.map((b, k) => (k === i ? { ...b, ...patch } : b)) });
  const setT = (i: number, patch: Partial<Trust>) => d.set({ trust: p.trust.map((t, k) => (k === i ? { ...t, ...patch } : t)) });
  return (
    <>
      <Card title="Titular">
        <div className="grid-2">
          <Field label="Titular de la portada" hint="Vacío = «Equipo profesional de seguridad, listo para instalar.»"><input className="input" value={p.headline} maxLength={120} onChange={(e) => d.set({ headline: e.target.value })} /></Field>
          <Field label="Bajada"><input className="input" value={p.subline} maxLength={300} onChange={(e) => d.set({ subline: e.target.value })} /></Field>
        </div>
      </Card>

      <Card title="Banners" extra={<button className="btn btn--ghost btn--sm" onClick={() => d.set({ banners: [...p.banners, { image: "", title: "", text: "", link: "/catalogo", cta: "Ver más", position: p.banners.length + 1, active: false }] })} disabled={p.banners.length >= 12}><Icon d={I.plus} />Banner</button>}>
        <div className="mt-stack" data-editor="banners">
          <p className="mt-lead">Se muestran hasta 3 activos, en este orden; el primero es el grande. Un banner nuevo nace inactivo.</p>
          {p.banners.length === 0 && <p className="mt-lead">Sin banners: la portada muestra el titular, las categorías y los destacados.</p>}
          {p.banners.map((b, i) => (
            <div key={b.id || `n${i}`} className="mt-box">
              <div className="mt-row" style={{ justifyContent: "space-between" }}>
                <b style={{ fontSize: 13 }}>Banner {i + 1}{!b.active && <span className="badge badge--muted" style={{ marginLeft: 6 }}>inactivo</span>}</b>
                <span className="mt-row">
                  <button className="btn btn--ghost btn--sm" onClick={() => d.set({ banners: moveItem(p.banners, i, -1) })} disabled={i === 0} aria-label={`Subir el banner ${i + 1}`}>↑</button>
                  <button className="btn btn--ghost btn--sm" onClick={() => d.set({ banners: moveItem(p.banners, i, 1) })} disabled={i === p.banners.length - 1} aria-label={`Bajar el banner ${i + 1}`}>↓</button>
                  <button className="btn btn--danger btn--sm" onClick={() => d.set({ banners: p.banners.filter((_, k) => k !== i) })}>Quitar</button>
                </span>
              </div>
              <div className="grid-2">
                <Field label="Título"><input className="input" value={b.title} maxLength={120} onChange={(e) => setB(i, { title: e.target.value })} /></Field>
                <Field label="Texto"><input className="input" value={b.text} maxLength={300} onChange={(e) => setB(i, { text: e.target.value })} /></Field>
                <Field label="Enlace" hint="/catalogo, /c/videovigilancia, /p/… o https://…"><input className="input" value={b.link} onChange={(e) => setB(i, { link: e.target.value })} /></Field>
                <Field label="Texto del botón"><input className="input" value={b.cta} maxLength={40} onChange={(e) => setB(i, { cta: e.target.value })} /></Field>
              </div>
              <div className="mt-row" style={{ alignItems: "end" }}>
                <div style={{ minWidth: 0, flex: "1 1 220px" }}><ImageField value={b.image || null} label="Imagen" onChange={(url) => setB(i, { image: url || "" })} /></div>
                <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center", minHeight: 44 }}><input type="checkbox" checked={b.active} onChange={(e) => setB(i, { active: e.target.checked })} />Activo</label>
              </div>
              {incompleto(b) && <p className="meta" role="note" style={{ margin: 0, color: "var(--warn)", textTransform: "none" }}>Este banner no se muestra hasta que tenga título e imagen.</p>}
            </div>
          ))}
        </div>
      </Card>

      <Card title="Sellos de confianza" extra={<button className="btn btn--ghost btn--sm" onClick={() => d.set({ trust: [...p.trust, { icon: "shield", title: "", text: "" }] })} disabled={p.trust.length >= 6}><Icon d={I.plus} />Sello</button>}>
        <div className="mt-stack">
          <p className="mt-lead">Cada sello lleva su dato (meses de garantía, horario de atención). «Soporte» sin horario no se publica.</p>
          {p.trust.map((t, i) => (
            <div key={i} className="mt-box trust-row">
              <Field label="Ícono"><select className="select" value={t.icon} onChange={(e) => setT(i, { icon: e.target.value })}>{ICONOS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
              <Field label="Título del sello"><input className="input" value={t.title} maxLength={60} onChange={(e) => setT(i, { title: e.target.value })} /></Field>
              <Field label="Dato del sello"><input className="input" value={t.text} maxLength={200} onChange={(e) => setT(i, { text: e.target.value })} /></Field>
              <button className="btn btn--danger btn--sm" style={{ justifySelf: "start" }} onClick={() => d.set({ trust: p.trust.filter((_, k) => k !== i) })}>Quitar</button>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Orden de la portada">
        <div className="mt-stack">
          <p className="mt-lead">Arrastre desde el asa o use las flechas (también con el teclado). Sin cambios, la portada queda como hoy. El título «Lo más pedido» solo se acepta con al menos 10 pedidos pagados en 90 días.</p>
          <ol className="pd-list">
            {p.sections.map((s, i) => (
              <li key={s.key} className={`mt-box pd-row${arrastra === i ? " is-drag" : ""}`}
                onDragOver={(e) => { if (arrastra !== null) e.preventDefault(); }}
                onDrop={(e) => { e.preventDefault(); if (arrastra === null || arrastra === i) return; const l = [...p.sections]; const [m] = l.splice(arrastra, 1); l.splice(i, 0, m); d.set({ sections: l }); setArrastra(null); }}>
                <span className="pd-handle" draggable onDragStart={() => setArrastra(i)} onDragEnd={() => setArrastra(null)} aria-hidden="true" title="Arrastrar">⋮⋮</span>
                <span className="pd-name"><b>{i + 1}. {SECCIONES[s.key] || s.key}</b>{!!noVende[s.key] && <span className="mt-warn" style={{ display: "block", fontWeight: 500 }}>{noVende[s.key] === 1 ? "1 producto de esta sección no se puede comprar hoy" : `${noVende[s.key]} productos de esta sección no se pueden comprar hoy`}</span>}</span>
                <input className="input pd-title" value={s.title} maxLength={80} placeholder="Título (vacío = el de siempre)" aria-label={`Título de ${SECCIONES[s.key] || s.key}`} onChange={(e) => d.set({ sections: p.sections.map((x, k) => (k === i ? { ...x, title: e.target.value } : x)) })} />
                <label className="pw-check"><input type="checkbox" checked={s.visible} onChange={(e) => d.set({ sections: p.sections.map((x, k) => (k === i ? { ...x, visible: e.target.checked } : x)) })} />Visible</label>
                <span className="mt-row">
                  <button className="btn btn--ghost btn--sm" onClick={() => d.set({ sections: moveItem(p.sections, i, -1) })} disabled={i === 0} aria-label={`Subir ${SECCIONES[s.key] || s.key}`}>↑</button>
                  <button className="btn btn--ghost btn--sm" onClick={() => d.set({ sections: moveItem(p.sections, i, 1) })} disabled={i === p.sections.length - 1} aria-label={`Bajar ${SECCIONES[s.key] || s.key}`}>↓</button>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </Card>
      <SaveBar drafts={[d]} labels={PORTADA_LABELS} validate={() => (p.trust.some((t) => !t.title.trim()) ? "Cada sello necesita su título (o quítelo)." : null)} />
    </>
  );
}
