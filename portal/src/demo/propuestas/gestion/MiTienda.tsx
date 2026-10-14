/* Detras de escena · Mi Tienda (ya construido, datos de ejemplo). Imita store/Store.tsx, StoreShowcaseAdmin.tsx y
   CheckoutSettings.tsx con sus rotulos reales. Todo se edita en memoria (y en sessionStorage de esta visita):
   - Personalizacion: la mini cabecera cambia al instante con los colores.
   - Vitrina: «Ver en la tienda» escribe sessionStorage["crimson-demo:vitrina"] y la portada REAL de la demo muestra el
     titular (lo lee mockApi /showcase). «Restablecer» borra la clave. */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { notifyDemo } from "../../mockApi";
import { CLAVES, MarcoMaqueta, Pantalla, SelloPropuesta, almacen, colones } from "./comun";
import { BLOQUES, DESTACADOS, KITS, LUGARES, TARIFAS, TIENDA, type Kit } from "./datos";
import { IC, Ico, SubnavDE } from "./ui";
import "../../../modules/store/orders.css";
import "./gestion.css";

const PESTANAS = [
  ["personalizacion", "Personalización"], ["vitrina", "Vitrina"], ["pagos", "Pagos y entrega"], ["envios", "Envíos"], ["paginas", "Páginas y bloques"],
] as const;
type Pest = (typeof PESTANAS)[number][0];
const CLAVE = "crimson-demo:ges:mitienda";
type Pagina = { id: number; title: string; slug: string; published: boolean; blocks: string[] };
type Datos = {
  v: 1; cfg: typeof TIENDA; destacados: string[]; kits: Kit[];
  pagos: { sinpe: boolean; transferencia: boolean; tarjeta: boolean; efectivo: boolean; auto: boolean; retiro: string };
  tarifas: { name: string; amount: number; active: boolean }[]; paginas: Pagina[];
};
const INICIAL = (): Datos => ({
  v: 1, cfg: { ...TIENDA }, destacados: [...DESTACADOS], kits: KITS.map((k) => ({ ...k })),
  pagos: { sinpe: true, transferencia: true, tarjeta: true, efectivo: true, auto: true, retiro: "Alajuela, Costa Rica · coordinamos la hora por WhatsApp" },
  tarifas: TARIFAS.map((t) => ({ name: t.name, amount: t.amount, active: true })),
  paginas: [
    { id: 1, title: "Inicio", slug: "inicio", published: true, blocks: ["hero", "products", "cta"] },
    { id: 2, title: "Instalaciones", slug: "instalaciones", published: false, blocks: ["image_text", "gallery", "form"] },
  ],
});
function cargar(): Datos {
  try { const v = JSON.parse(almacen.sesion.get(CLAVE) || "null"); if (v && v.v === 1 && v.cfg && Array.isArray(v.paginas)) return v as Datos; } catch { /* corrupto */ }
  return INICIAL();
}
const HEX = /^#[0-9a-f]{6}$/i;
/* luminancia WCAG para elegir texto blanco o tinta sobre el color que elija Crimson */
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
const LUM_TINTA = lum("#15131a");
const sobre = (hex: string) => {
  if (!HEX.test(hex)) return "#ffffff";
  const L = lum(hex);
  return (L + 0.05) / (LUM_TINTA + 0.05) > 1.05 / (L + 0.05) ? "#15131a" : "#ffffff";
};

function Campo({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field ges-campo"><span className="ges-lbl">{label}</span>{children}{hint && <small className="ges-hint">{hint}</small>}</label>;
}

export default function MiTienda() {
  const [d, setD] = useState<Datos>(cargar);
  useEffect(() => { almacen.sesion.set(CLAVE, JSON.stringify(d)); }, [d]);
  const [sp, setSp] = useSearchParams();
  const pest = (PESTANAS.find(([k]) => k === sp.get("pestana"))?.[0] || "personalizacion") as Pest;
  const tabsRef = useRef<HTMLDivElement>(null);
  const [aviso, setAviso] = useState("");
  const irPest = (k: Pest, foco = false) => {
    const n = new URLSearchParams(sp); n.set("pestana", k); setSp(n, { replace: true });
    if (foco) requestAnimationFrame(() => tabsRef.current?.querySelector<HTMLElement>(`#ges-mt-tab-${k}`)?.focus());
  };
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = PESTANAS.findIndex(([k]) => k === pest);
    const j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? PESTANAS.length - 1 : null;
    if (j === null) return;
    e.preventDefault();
    irPest(PESTANAS[(j + PESTANAS.length) % PESTANAS.length][0], true);
  };
  const set = <K extends keyof Datos>(k: K, v: Datos[K]) => setD((x) => ({ ...x, [k]: v }));
  const cfg = d.cfg;
  const setCfg = (patch: Partial<Datos["cfg"]>) => set("cfg", { ...cfg, ...patch });
  const guardar = (que: string) => notifyDemo({ title: "Vista previa", text: `En la plataforma real aquí se guarda ${que} y la tienda cambia sin programador. En la vista previa los cambios viven solo en esta visita.` });

  return (
    <MarcoMaqueta id="DE" estado="existente" titulo="Mi Tienda"
      bajada="Marca, vitrina, páginas, pagos y envíos: Crimson cambia la tienda sin programador. Cambiá el titular y miralo en la portada.">
      <SubnavDE actual="mi-tienda" />
      <Pantalla superficie="admin" seccion="Mi Tienda">
        <div className="ges ges-adm ges-mt">
          <div className="page-head">
            <div><div className="meta">10 · Mi Tienda</div><h2 className="h1">Mi Tienda</h2></div>
            <div className="page-head__actions">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => notifyDemo({ title: "Ver sitio", text: "En la plataforma real se abre la tienda publicada en tienda.crimsoncr.com. Aquí ya la estás viendo: tocá «← Tienda» arriba." })}><Ico d={IC.store} />Ver sitio</button>
              <button type="button" className="btn btn--soft btn--sm" onClick={() => notifyDemo({ title: "Publicar tienda", text: "En la plataforma real este botón publica o despublica la tienda. En la vista previa no cambia nada." })}>Publicar tienda</button>
            </div>
          </div>

          <div className="ges-mt-tabs" role="tablist" aria-label="Secciones de Mi Tienda" ref={tabsRef} onKeyDown={onTabKey}>
            {PESTANAS.map(([k, t]) => (
              <button key={k} type="button" role="tab" id={`ges-mt-tab-${k}`} aria-controls={`ges-mt-panel-${k}`} aria-selected={pest === k} tabIndex={pest === k ? 0 : -1}
                className={`btn btn--sm ${pest === k ? "btn--crimson" : "btn--ghost"}`} onClick={() => irPest(k)}>{t}</button>
            ))}
          </div>

          <div role="tabpanel" id={`ges-mt-panel-${pest}`} aria-labelledby={`ges-mt-tab-${pest}`} className="ges-mt-panel">
            {pest === "personalizacion" && <Personalizacion cfg={cfg} setCfg={setCfg} guardar={() => guardar("la personalización")} />}
            {pest === "vitrina" && <Vitrina d={d} set={set} guardar={guardar} setAviso={setAviso} />}
            {pest === "pagos" && <Pagos d={d} set={set} guardar={() => guardar("la configuración de pagos y entrega")} />}
            {pest === "envios" && <Envios d={d} set={set} guardar={() => guardar("las tarifas de envío")} />}
            {pest === "paginas" && <Paginas d={d} set={set} guardar={() => guardar("la página")} />}
          </div>
          <p className="ges-sr" role="status" aria-live="polite">{aviso}</p>
        </div>
      </Pantalla>
    </MarcoMaqueta>
  );
}

// ---------------------------------------------------------------- personalizacion
function Personalizacion({ cfg, setCfg, guardar }: { cfg: Datos["cfg"]; setCfg: (p: Partial<Datos["cfg"]>) => void; guardar: () => void }) {
  const [p, setP] = useState(cfg.primary);
  const [s, setS] = useState(cfg.secondary);
  useEffect(() => { setP(cfg.primary); setS(cfg.secondary); }, [cfg.primary, cfg.secondary]);
  const color = (v: string, local: (x: string) => void, k: "primary" | "secondary") => { local(v); if (HEX.test(v)) setCfg({ [k]: v.toLowerCase() }); };
  const fuente = cfg.font === "Bricolage Grotesque" ? '"Bricolage Grotesque", Manrope, sans-serif' : cfg.font === "Manrope" ? "Manrope, system-ui, sans-serif" : `"${cfg.font}", system-ui, sans-serif`;
  return (
    <div className="ges-mt-dos">
      <section className="card ges-card" aria-labelledby="ges-mt-p">
        <div className="card__head"><h3 id="ges-mt-p" className="ges-card-t">Configura el sitio a tu gusto</h3><button type="button" className="btn btn--crimson btn--sm" onClick={guardar}><Ico d={IC.check} />Guardar</button></div>
        <div className="card__body ges-form">
          <Campo label="Nombre de la tienda"><input className="input" value={cfg.name} onChange={(e) => setCfg({ name: e.target.value })} maxLength={60} /></Campo>
          <Campo label="Frase"><input className="input" value={cfg.tagline} onChange={(e) => setCfg({ tagline: e.target.value })} maxLength={120} /></Campo>
          <Campo label="Color principal">
            <span className="ges-color"><input type="color" value={HEX.test(p) ? p : cfg.primary} onChange={(e) => color(e.target.value, setP, "primary")} aria-label="Elegir color principal" /><input className="input input--mono ges-left" value={p} onChange={(e) => color(e.target.value, setP, "primary")} aria-invalid={!HEX.test(p) || undefined} spellCheck={false} /></span>
          </Campo>
          <Campo label="Color secundario">
            <span className="ges-color"><input type="color" value={HEX.test(s) ? s : cfg.secondary} onChange={(e) => color(e.target.value, setS, "secondary")} aria-label="Elegir color secundario" /><input className="input input--mono ges-left" value={s} onChange={(e) => color(e.target.value, setS, "secondary")} aria-invalid={!HEX.test(s) || undefined} spellCheck={false} /></span>
          </Campo>
          <Campo label="Tipo de tienda" hint="Catálogo muestra productos; Tienda agrega carrito y pago en línea."><select className="select" value={cfg.kind} onChange={(e) => setCfg({ kind: e.target.value })}><option value="catalogo">Catálogo</option><option value="tienda">Tienda (carrito)</option></select></Campo>
          <Campo label="Tipografía"><select className="select" value={cfg.font} onChange={(e) => setCfg({ font: e.target.value })}>{["Manrope", "Bricolage Grotesque", "Sora", "Space Grotesk"].map((f) => <option key={f}>{f}</option>)}</select></Campo>
          <Campo label="Moneda de la tienda"><select className="select" value={cfg.currency} onChange={(e) => setCfg({ currency: e.target.value })}><option value="CRC">Colones (₡)</option><option value="USD">Dólares ($)</option></select></Campo>
          <Campo label="Dominio" hint="Propio: apuntar un CNAME al portal."><input className="input" value={cfg.domain} onChange={(e) => setCfg({ domain: e.target.value })} /></Campo>
        </div>
      </section>
      <section className="ges-mini-wrap" aria-labelledby="ges-mt-prev">
        <h3 id="ges-mt-prev" className="meta">Vista previa al instante</h3>
        <div className="ges-mini" style={{ fontFamily: fuente }}>
          <div className="ges-mini__head" style={{ background: cfg.secondary, color: sobre(cfg.secondary) }}>
            <span className="ges-mini__logo"><b>{cfg.name || "Tienda"}</b><small>{cfg.tagline}</small></span>
            <span className="ges-mini__nav" aria-hidden="true"><span>Catálogo</span><span>Arma tu sistema</span></span>
            <span className="ges-mini__cart" style={{ background: cfg.primary, color: sobre(cfg.primary) }}>{cfg.kind === "tienda" ? "Carrito · 0" : "Cotizar"}</span>
          </div>
          <div className="ges-mini__hero">
            <span className="ges-mini__kick"><i style={{ background: cfg.primary }} aria-hidden="true" />En línea · {cfg.currency === "CRC" ? "precios en colones" : "precios en dólares"}</span>
            <p className="ges-mini__h">Equipo profesional de seguridad, listo para instalar.</p>
            <span className="ges-mini__btn" style={{ background: cfg.primary, color: sobre(cfg.primary) }}>Ver catálogo</span>
          </div>
        </div>
        <p className="ges-sm muted">Así se vería la cabecera de {cfg.domain || "la tienda"} con estos colores. Los cambios viven solo en esta visita.</p>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- vitrina
function Vitrina({ d, set, guardar, setAviso }: { d: Datos; set: <K extends keyof Datos>(k: K, v: Datos[K]) => void; guardar: (q: string) => void; setAviso: (s: string) => void }) {
  const nav = useNavigate();
  const guardado = useMemo(() => { try { return JSON.parse(almacen.sesion.get(CLAVES.vitrina) || "null") as { headline?: string; subline?: string } | null; } catch { return null; } }, []);
  const [titular, setTitular] = useState(guardado?.headline || "");
  const [bajada, setBajada] = useState(guardado?.subline || "");
  const [seo, setSeo] = useState("Cámaras, grabadores, redes y control de acceso en Costa Rica, con factura electrónica y técnicos locales.");
  const ver = () => {
    almacen.sesion.set(CLAVES.vitrina, JSON.stringify({ headline: titular.trim().slice(0, 120), subline: bajada.trim().slice(0, 300) }));
    nav("/");
  };
  const restablecer = () => { almacen.sesion.del(CLAVES.vitrina); setTitular(""); setBajada(""); setAviso("Se restableció el titular de la portada."); };
  const mover = (i: number, dir: -1 | 1) => {
    const b = [...d.destacados]; const j = i + dir;
    if (j < 0 || j >= b.length) return;
    [b[i], b[j]] = [b[j], b[i]]; set("destacados", b);
    setAviso(`${b[j]} quedó en el lugar ${j + 1}.`);
  };
  const kit = (i: number, p: Partial<Kit>) => set("kits", d.kits.map((k, n) => (n === i ? { ...k, ...p } : k)));
  return (
    <section className="card ges-card" aria-labelledby="ges-mt-v">
      <div className="card__head"><h3 id="ges-mt-v" className="ges-card-t">Vitrina de la tienda</h3></div>
      <div className="card__body ges-vit">
        <div className="ges-vit__portada">
          <div className="ges-form">
            <Campo label="Titular de la portada" hint="Vacío = “Equipo profesional de seguridad, listo para instalar.”"><input className="input" value={titular} maxLength={120} onChange={(e) => setTitular(e.target.value)} placeholder="Equipo profesional de seguridad, listo para instalar." /></Campo>
            <Campo label="Bajada"><input className="input" value={bajada} maxLength={300} onChange={(e) => setBajada(e.target.value)} placeholder="Cámaras, grabadores, redes y control de acceso…" /></Campo>
            <Campo label="Descripción para Google y WhatsApp (portada)"><input className="input" value={seo} maxLength={300} onChange={(e) => setSeo(e.target.value)} /></Campo>
            <p className="ges-nota-h15"><SelloPropuesta estado="existente" tamano="chico" /> Las direcciones viejas de Fygaro se redirigen con 301 y cada página lleva sus metas desde el servidor.</p>
          </div>
          <div className="ges-vit__prev" aria-label="Vista previa del encabezado de la portada" role="group">
            <span className="meta">Vista previa de la portada</span>
            <p className="ges-vit__h">{titular.trim() || <>Equipo profesional de seguridad, <em>listo para instalar</em>.</>}</p>
            <p className="ges-vit__s">{bajada.trim() || "Cámaras, grabadores, redes y control de acceso con precio final, factura electrónica y técnicos locales que responden."}</p>
          </div>
        </div>
        <div className="ges-vit__acc">
          <button type="button" className="btn btn--crimson" onClick={ver}>Ver en la tienda →</button>
          <button type="button" className="btn btn--ghost" onClick={restablecer}>Restablecer</button>
          <span className="ges-sm muted">«Ver en la tienda» abre la portada de esta vista previa con tu titular (solo en esta visita).</span>
        </div>

        <div className="ges-vit__bloque">
          <div className="ges-vit__bh"><h4>Productos destacados («Lo más pedido»)</h4><button type="button" className="btn btn--soft btn--sm" onClick={() => guardar("el orden de destacados")}><Ico d={IC.check} />Guardar destacados</button></div>
          <p className="ges-sm muted">Vacío = la tienda elige sola uno o dos por categoría, con foto y precio. Se muestran hasta 8.</p>
          <ol className="ges-dest">
            {d.destacados.map((n, i) => (
              <li key={n}>
                <span className="mono ges-dest__n" aria-hidden="true">{i + 1}</span>
                <span className="ges-dest__t">{n}</span>
                <button type="button" className="btn btn--ghost btn--sm ges-ico-btn" onClick={() => mover(i, -1)} disabled={i === 0} aria-label={`Subir ${n}`}><Ico d={IC.up} /></button>
                <button type="button" className="btn btn--ghost btn--sm ges-ico-btn" onClick={() => mover(i, 1)} disabled={i === d.destacados.length - 1} aria-label={`Bajar ${n}`}><Ico d={IC.down} /></button>
              </li>
            ))}
          </ol>
        </div>

        <div className="ges-vit__bloque">
          <div className="ges-vit__bh"><h4>Kits por necesidad</h4><button type="button" className="btn btn--soft btn--sm" onClick={() => guardar("los kits")}><Ico d={IC.check} />Guardar kits</button></div>
          <p className="ges-sm muted">Cada kit se arma con el configurador «Arma tu sistema»: los productos y el precio salen del catálogo vigente, nunca quedan desactualizados.</p>
          <div className="ges-kits">
            {d.kits.map((k, i) => (
              <fieldset key={k.id} className="ges-kit">
                <legend>{k.title}</legend>
                <p className="ges-sm muted">{k.text}</p>
                <div className="ges-kit__f">
                  <Campo label="Lugar"><select className="select" value={k.place} onChange={(e) => kit(i, { place: e.target.value })}>{Object.entries(LUGARES).map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></Campo>
                  <Campo label="Adentro"><input className="input input--mono" type="number" min={0} max={32} value={k.indoor} onChange={(e) => kit(i, { indoor: Math.max(0, Math.min(32, Number(e.target.value) || 0)) })} /></Campo>
                  <Campo label="Afuera"><input className="input input--mono" type="number" min={0} max={32} value={k.outdoor} onChange={(e) => kit(i, { outdoor: Math.max(0, Math.min(32, Number(e.target.value) || 0)) })} /></Campo>
                  <Campo label="Días"><select className="select" value={k.days} onChange={(e) => kit(i, { days: Number(e.target.value) })}>{[7, 15, 30, 60].map((x) => <option key={x} value={x}>{x}</option>)}</select></Campo>
                </div>
              </fieldset>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- pagos y entrega (CheckoutSettings.tsx)
function Pagos({ d, set, guardar }: { d: Datos; set: <K extends keyof Datos>(k: K, v: Datos[K]) => void; guardar: () => void }) {
  const p = d.pagos;
  const up = (patch: Partial<Datos["pagos"]>) => set("pagos", { ...p, ...patch });
  return (
    <section className="card ges-card" aria-labelledby="ges-mt-pg">
      <div className="card__head"><h3 id="ges-mt-pg" className="ges-card-t">Pagos y entrega</h3><button type="button" className="btn btn--crimson btn--sm" onClick={guardar}><Ico d={IC.check} />Guardar</button></div>
      <div className="card__body">
        <div className="ck-cfg">
          <div className="ck-cfg__m" data-on={String(p.sinpe)}>
            <label><input type="checkbox" checked={p.sinpe} onChange={(e) => up({ sinpe: e.target.checked })} />SINPE Móvil</label>
            <div className="ges-form ges-form--2">
              <Campo label="Número SINPE"><input className="input" readOnly value="" placeholder="(sin configurar en la vista previa)" /></Campo>
              <Campo label="A nombre de"><input className="input" readOnly value="Crimson (ejemplo)" /></Campo>
            </div>
          </div>
          <div className="ck-cfg__m" data-on={String(p.transferencia)}>
            <label><input type="checkbox" checked={p.transferencia} onChange={(e) => up({ transferencia: e.target.checked })} />Transferencia bancaria</label>
            <div className="ges-form ges-form--2">
              <Campo label="Cuenta IBAN"><input className="input input--mono ges-left" readOnly value="CR00 0000 0000 0000 0000 00" /></Campo>
              <Campo label="Banco"><input className="input" readOnly value="(ejemplo)" /></Campo>
            </div>
          </div>
          <div className="ck-cfg__m" data-on={String(p.tarjeta)}>
            <label><input type="checkbox" checked={p.tarjeta} onChange={(e) => up({ tarjeta: e.target.checked })} />Tarjeta (ONVO)</label>
            <p className="muted ges-sm">ONVO sin credenciales: en este entorno el pago con tarjeta es SIMULADO (de prueba) y la tienda lo dice.</p>
          </div>
          <div className="ck-cfg__m" data-on={String(p.efectivo)}>
            <label><input type="checkbox" checked={p.efectivo} onChange={(e) => up({ efectivo: e.target.checked })} />Efectivo contra entrega o al retirar</label>
            <Campo label="Texto para el cliente"><input className="input" readOnly value="Pago en efectivo al recibir o al retirar el pedido." /></Campo>
          </div>
          <div className="ges-form ges-form--2">
            <Campo label="Envío gratis desde (₡, con IVA)" hint="0 = nunca."><input className="input input--mono" readOnly value="0" /></Campo>
            <Campo label="Dirección de retiro"><input className="input" value={p.retiro} onChange={(e) => up({ retiro: e.target.value })} /></Campo>
          </div>
          <label className="ges-check ges-check--top"><input type="checkbox" checked={p.auto} onChange={(e) => up({ auto: e.target.checked })} />Emitir la factura electrónica automáticamente al confirmarse el pago (con cédula: FE; sin cédula: tiquete) y enviarla al cliente</label>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- envios
function Envios({ d, set, guardar }: { d: Datos; set: <K extends keyof Datos>(k: K, v: Datos[K]) => void; guardar: () => void }) {
  const up = (i: number, patch: Partial<Datos["tarifas"][number]>) => set("tarifas", d.tarifas.map((t, n) => (n === i ? { ...t, ...patch } : t)));
  return (
    <section className="card ges-card" aria-labelledby="ges-mt-en">
      <div className="card__head"><h3 id="ges-mt-en" className="ges-card-t">Tarifas de envío</h3><button type="button" className="btn btn--crimson btn--sm" onClick={guardar}><Ico d={IC.check} />Guardar</button></div>
      <div className="card__body ges-envios">
        {d.tarifas.map((t, i) => (
          <fieldset key={i} className="ges-envio">
            <legend className="ges-sr">{t.name}</legend>
            <Campo label="Nombre"><input className="input" value={t.name} onChange={(e) => up(i, { name: e.target.value })} /></Campo>
            <Campo label="Base ₡ (sin IVA)"><input className="input input--mono" inputMode="numeric" value={t.amount} onChange={(e) => up(i, { amount: Math.max(0, Number(e.target.value.replace(/\D/g, "")) || 0) })} /></Campo>
            <span className="ges-envio__z"><span className="meta">Zona</span>{TARIFAS[i]?.zone || "Según el nombre"}</span>
            <span className="ges-envio__iva"><span className="meta">Con IVA</span><span className="money">{colones(Math.round(t.amount * 1.13))}</span></span>
            <label className="ges-check"><input type="checkbox" checked={t.active} onChange={(e) => up(i, { active: e.target.checked })} />Activo</label>
          </fieldset>
        ))}
        <p className="muted ges-sm">Tarifa fija, o por peso estilo Correos de Costa Rica. Se factura como una línea con IVA 13 %, igual que en el pedido. Son las mismas tarifas que usa el pago de esta vista previa.</p>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- paginas y bloques
function Paginas({ d, set, guardar }: { d: Datos; set: <K extends keyof Datos>(k: K, v: Datos[K]) => void; guardar: () => void }) {
  const [selId, setSelId] = useState(d.paginas[0]?.id ?? 1);
  const [paleta, setPaleta] = useState(false);
  const sel = d.paginas.find((x) => x.id === selId) || d.paginas[0];
  const upd = (blocks: string[]) => set("paginas", d.paginas.map((x) => (x.id === sel.id ? { ...x, blocks } : x)));
  const mover = (i: number, dir: -1 | 1) => { const b = [...sel.blocks]; const j = i + dir; if (j < 0 || j >= b.length) return; [b[i], b[j]] = [b[j], b[i]]; upd(b); };
  return (
    <section className="card ges-card" aria-labelledby="ges-mt-pb">
      <div className="card__head"><h3 id="ges-mt-pb" className="ges-card-t">Páginas y bloques</h3><button type="button" className="btn btn--crimson btn--sm" onClick={() => notifyDemo({ title: "Nueva página", text: "En la plataforma real aquí se crea una página nueva con su dirección y se arma con bloques." })}>+ Nueva página</button></div>
      <div className="ges-pag">
        <ul className="ges-pag__lista" aria-label="Páginas">
          {d.paginas.map((x) => (
            <li key={x.id}>
              <button type="button" aria-pressed={x.id === sel.id} onClick={() => { setSelId(x.id); setPaleta(false); }}>
                <b>{x.title}</b><span className="meta ges-meta-n">/{x.slug}</span>{!x.published && <span className="badge badge--muted">borrador</span>}
              </button>
            </li>
          ))}
        </ul>
        <div className="ges-pag__ed">
          <div className="ges-pag__bar">
            <b>{sel.title}</b>
            <span className="ges-pag__acc">
              <button type="button" className="btn btn--soft btn--sm" aria-expanded={paleta} onClick={() => setPaleta((v) => !v)}>+ Bloque</button>
              <button type="button" className="btn btn--crimson btn--sm" onClick={guardar}><Ico d={IC.check} />Guardar</button>
            </span>
          </div>
          {paleta && (
            <div className="ges-paleta" role="group" aria-label="Agregar bloque">
              {Object.entries(BLOQUES).map(([k, t]) => (
                <button type="button" key={k} className="ges-paleta__b" onClick={() => { upd([...sel.blocks, k]); setPaleta(false); }}><b>{t}</b><span className="meta ges-meta-n">{k}</span></button>
              ))}
            </div>
          )}
          {sel.blocks.length === 0 ? <p className="muted ges-sm">Página vacía: agregá el primer bloque.</p> : (
            <ol className="ges-bloques">
              {sel.blocks.map((b, i) => (
                <li key={`${b}-${i}`}>
                  <span className="meta">{BLOQUES[b] || b}</span>
                  <span className="ges-bloques__acc">
                    <button type="button" className="btn btn--ghost btn--sm ges-ico-btn" onClick={() => mover(i, -1)} disabled={i === 0} aria-label={`Subir bloque ${BLOQUES[b]}`}><Ico d={IC.up} /></button>
                    <button type="button" className="btn btn--ghost btn--sm ges-ico-btn" onClick={() => mover(i, 1)} disabled={i === sel.blocks.length - 1} aria-label={`Bajar bloque ${BLOQUES[b]}`}><Ico d={IC.down} /></button>
                    <button type="button" className="btn btn--ghost btn--sm ges-ico-btn" onClick={() => upd(sel.blocks.filter((_, n) => n !== i))} aria-label={`Quitar bloque ${BLOQUES[b]}`}><Ico d={IC.x} /></button>
                  </span>
                </li>
              ))}
            </ol>
          )}
          <p className="muted ges-sm">Tipos de bloque: {Object.values(BLOQUES).join(" · ")}.</p>
        </div>
      </div>
    </section>
  );
}
