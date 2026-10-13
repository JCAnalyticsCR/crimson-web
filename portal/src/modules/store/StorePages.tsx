/* Paginas propias de la vitrina: asesoria personalizada (crea una oportunidad en el CRM), terminos, privacidad,
   envios, garantias, preguntas frecuentes, comparador y paginas de bloques. Sin coreografia: son pantallas de
   lectura y de decision. */
import { cloneElement, isValidElement, useEffect, useId, useMemo, useState, type FormEvent, type ReactElement, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Blocks, type Block, type PubProduct } from "./Blocks";
import { KIT_STORAGE } from "./KitBuilder";
import { AvailBadge, Crumbs, EmptyHelp, IC, Ico, PriceTag, Viewfinder, useAvailability, useHead, usePath, useStore, waLink, type VProd } from "./storeKit";

const SOLUTIONS: [string, string][] = [
  ["cctv", "Videovigilancia"], ["acceso", "Control de acceso"], ["redes", "Redes y WiFi"], ["ups", "Respaldo eléctrico (UPS)"],
  ["cableado", "Cableado estructurado"], ["asistencia", "Control de asistencia"], ["anpr", "Lectura de placas"], ["integral", "Proyecto integral"], ["otro", "Otro"],
];
// mismas opciones que el formulario de crimsoncr.com
const ENVS: [string, string][] = [["casa", "Casa"], ["negocio", "Negocio"], ["condominio", "Condominio"], ["oficina", "Oficina"], ["bodega", "Bodega o planta"], ["otro", "Otro"]];
const TIMES: [string, string][] = [["inmediato", "Inmediato"], ["semanas", "Próximas semanas"], ["evaluacion", "En evaluación"], ["futuro", "A futuro"]];
export const INFO: [string, string][] = [
  ["envios", "Envíos y devoluciones"], ["garantias", "Garantías"], ["preguntas-frecuentes", "Preguntas frecuentes"], ["terminos", "Términos y condiciones"], ["privacidad", "Política de privacidad"],
];

function Field({ label, hint, children, wide }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  const id = useId();
  const el = isValidElement(children) ? cloneElement(children as ReactElement<{ id?: string; "aria-describedby"?: string }>, { id, "aria-describedby": hint ? `${id}-h` : undefined }) : children;
  return <div className={`sh-field${wide ? " vt-field--wide" : ""}`}><label htmlFor={id}>{label}</label>{el}{hint && <small id={`${id}-h`} className="vt-hint">{hint}</small>}</div>;
}

type Item = { product_id: number; quantity: number; name: string };

export function AdvisoryPage() {
  const ctx = useStore();
  const to = usePath();
  const [sp] = useSearchParams();
  const [f, setF] = useState({
    name: "", party: "particular", company: "", phone: "", email: "", solution: sp.get("solucion") && SOLUTIONS.some(([k]) => k === sp.get("solucion")) ? sp.get("solucion")! : "cctv",
    description: "", location: "", environment: "", timeline: "", install: sp.get("instalar") === "1", website: "",
  });
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ message: string; fields: Record<string, string> } | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const origin = sp.get("origen") || (sp.get("producto") ? `ficha:${sp.get("producto")}` : "asesoria");

  useHead({ title: `Solicitud de asesoría personalizada · ${ctx.store.name}`, description: "Contanos qué necesitás proteger o conectar y un asesor te contacta con una propuesta a la medida.", path: "/asesoria" });

  // equipo adjunto: desde la ficha (?producto=&cantidad=) o desde "Arma tu sistema" (sessionStorage)
  useEffect(() => {
    const pid = Number(sp.get("producto"));
    if (pid) {
      const qty = Math.max(1, Math.min(999, Number(sp.get("cantidad")) || 1));
      ctx.api<VProd>(`/item/x-${pid}`).then((p) => {
        setItems([{ product_id: p.id, quantity: qty, name: p.name }]);
        setF((x) => ({ ...x, description: x.description || `${sp.get("instalar") === "1" ? "Quiero cotizar la instalación de" : "Quiero cotizar"} ${qty} × ${p.name}.` }));
      }).catch(() => undefined);
      return;
    }
    if (sp.get("origen") === "arma-tu-sistema") {
      try {
        const k = JSON.parse(sessionStorage.getItem(KIT_STORAGE) || "null") as { items: Item[]; text: string } | null;
        if (k) { setItems(k.items.slice(0, 40)); setF((x) => ({ ...x, description: x.description || k.text })); }
      } catch { /* sin kit guardado */ }
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const fe = (k: string) => err?.fields[k];
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const local: Record<string, string> = {};
    if (f.name.trim().length < 2) local.name = "Indicá tu nombre";
    if (!f.phone.trim() && !f.email.trim()) local.phone = "Indicá un teléfono o un correo";
    if (f.party === "empresa" && !f.company.trim()) local.company = "Indicá el nombre de la empresa";
    if (f.description.trim().length < 10) local.description = "Contanos un poco más (al menos 10 caracteres)";
    if (Object.keys(local).length) { setErr({ message: "Revisá los datos marcados.", fields: local }); return; }
    setBusy(true); setErr(null);
    try {
      const r = await fetch(`/api/public/store/${ctx.slug}/asesoria`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...f, environment: f.environment || null, timeline: f.timeline || null, company: f.party === "empresa" ? f.company : f.company || null, items: items.map(({ product_id, quantity }) => ({ product_id, quantity })), origin: origin.toLowerCase().replace(/[^a-z0-9:_/-]/g, "").slice(0, 60) || null }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.status === 429) throw { message: "Recibimos varias solicitudes desde esta conexión. Escribinos por WhatsApp y te atendemos de inmediato.", fields: {} };
      if (!r.ok) {
        const d = j.detail;
        const fields: Record<string, string> = {};
        (d?.errors || []).forEach((x: { campo: string; mensaje: string }) => { fields[x.campo.split(".").pop() || "general"] = x.mensaje; });
        throw { message: typeof d === "string" ? d : d?.message || "No se pudo enviar. Intentá de nuevo.", fields };
      }
      setOk(j.message || "Recibimos tu solicitud.");
      try { sessionStorage.removeItem(KIT_STORAGE); } catch { /* nada */ }
      window.scrollTo({ top: 0 });
    } catch (x) {
      const m = x as { message?: string; fields?: Record<string, string> };
      setErr({ message: m.message || "Sin conexión. Intentá de nuevo.", fields: m.fields || {} });
    } finally { setBusy(false); }
  };

  if (ok) {
    return (
      <div className="sh-wrap"><div className="sh-done" role="status">
        <span className="sh-eyebrow">Solicitud recibida</span>
        <h1>¡Gracias, {f.name.split(" ")[0]}!</h1>
        <p className="sh-note" style={{ fontSize: 15, margin: 0 }}>{ok}</p>
        <div className="vt-empty__actions">
          {ctx.store.whatsapp && <a className="sh-btn sh-btn--wa" href={waLink(ctx.store.whatsapp, `Hola ${ctx.store.name}, acabo de enviar una solicitud de asesoría a nombre de ${f.name}.`)} target="_blank" rel="noopener noreferrer"><Ico d={IC.wa} />Escribir por WhatsApp</a>}
          <Link className="sh-btn sh-btn--ghost" to={to("/")}>Volver a la tienda</Link>
        </div>
      </div></div>
    );
  }

  return (
    <div className="sh-wrap vt-advpage">
      <Crumbs items={[{ name: "Tienda", path: "/" }, { name: "Asesoría personalizada" }]} />
      <div className="vt-advpage__grid">
        <header className="vt-advpage__intro">
          <span className="sh-eyebrow">Asesoría sin costo</span>
          <h1>Solicitud de asesoría personalizada</h1>
          <p>Contanos qué necesitás proteger o conectar. Un asesor de Crimson te contacta, visita si hace falta y te entrega una propuesta con equipo, instalación y soporte.</p>
          <ul className="vt-assure">
            <li><Ico d={IC.clock} /><span>Respuesta en horario hábil</span></li>
            <li><Ico d={IC.tool} /><span>Levantamiento técnico en sitio</span></li>
            <li><Ico d={IC.doc} /><span>Propuesta por escrito, sin compromiso</span></li>
          </ul>
        </header>
        <form className="vt-advform" onSubmit={submit} noValidate>
          {items.length > 0 && (
            <fieldset className="sh-fieldset"><legend>Equipo de interés</legend>
              <ul className="vt-items">{items.map((it) => <li key={it.product_id}><span>{it.quantity} × {it.name}</span><button type="button" className="sh-rm" onClick={() => setItems(items.filter((x) => x.product_id !== it.product_id))}>Quitar</button></li>)}</ul>
            </fieldset>
          )}
          <fieldset className="sh-fieldset"><legend>Tus datos</legend>
            <div className="vt-seg" role="radiogroup" aria-label="Tipo de cliente">
              <label className="vt-seg__opt"><input type="radio" name="party" checked={f.party === "particular"} onChange={() => setF({ ...f, party: "particular" })} /><span>Particular</span></label>
              <label className="vt-seg__opt"><input type="radio" name="party" checked={f.party === "empresa"} onChange={() => setF({ ...f, party: "empresa" })} /><span>Empresa</span></label>
            </div>
            <div className="sh-2">
              <Field label="Nombre completo"><input autoComplete="name" value={f.name} onChange={set("name")} aria-invalid={!!fe("name")} required /></Field>
              {f.party === "empresa" && <Field label="Empresa"><input autoComplete="organization" value={f.company} onChange={set("company")} aria-invalid={!!fe("company")} /></Field>}
              <Field label="Teléfono / WhatsApp"><input type="tel" autoComplete="tel" value={f.phone} onChange={set("phone")} aria-invalid={!!fe("phone")} /></Field>
              <Field label="Correo"><input type="email" autoComplete="email" value={f.email} onChange={set("email")} aria-invalid={!!fe("email")} /></Field>
            </div>
            {(fe("name") || fe("company") || fe("phone") || fe("email")) && <p className="sh-err">{fe("name") || fe("company") || fe("phone") || fe("email")}</p>}
          </fieldset>
          <fieldset className="sh-fieldset"><legend>Tu proyecto</legend>
            <div className="vt-solutions" role="radiogroup" aria-label="Tipo de solución">
              {SOLUTIONS.map(([k, l]) => <label key={k} className="sh-chip vt-solution"><input type="radio" name="solution" checked={f.solution === k} onChange={() => setF({ ...f, solution: k })} /><span>{l}</span></label>)}
            </div>
            <Field label="¿Qué necesitás?" hint="Por ejemplo: 6 cámaras para el local y la bodega, ver desde el celular, grabación de un mes."><textarea value={f.description} onChange={set("description")} aria-invalid={!!fe("description")} rows={5} maxLength={3000} /></Field>
            {fe("description") && <p className="sh-err">{fe("description")}</p>}
            <div className="sh-2">
              <Field label="¿Para qué entorno?"><select value={f.environment} onChange={set("environment")}><option value="">Seleccionar</option>{ENVS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
              <Field label="¿En qué plazo?"><select value={f.timeline} onChange={set("timeline")}><option value="">Seleccionar</option>{TIMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            </div>
            <Field label="Ubicación" hint="Provincia, cantón o dirección aproximada."><input autoComplete="address-level2" value={f.location} onChange={set("location")} maxLength={200} /></Field>
            <label className="vt-check vt-check--box"><input type="checkbox" checked={f.install} onChange={(e) => setF({ ...f, install: e.target.checked })} /><span>Quiero que Crimson haga la instalación</span></label>
            {/* honeypot: invisible para personas */}
            <div className="vt-hp" aria-hidden="true"><label>Sitio web<input tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} /></label></div>
          </fieldset>
          {err && <p className="sh-err" role="alert">{err.message}</p>}
          <button className="sh-btn sh-btn--crimson sh-btn--block" type="submit" disabled={busy}>{busy ? "Enviando…" : "Enviar solicitud"}</button>
          <p className="sh-note">Usamos tus datos solo para responder esta solicitud. <Link to={to("/info/privacidad")}>Política de privacidad</Link>.</p>
        </form>
      </div>
    </div>
  );
}

type Info = { kind: string; title: string; body: string; faq: { q: string; a: string }[] };
/** parrafos separados por linea en blanco; una primera linea corta sin punto final se lee como subtitulo */
function Prose({ text }: { text: string }) {
  const blocks = text.replace(/\r/g, "").split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return (
    <div className="vt-prose">
      {blocks.map((b, i) => {
        const [first, ...rest] = b.split("\n");
        const heading = rest.length > 0 && first.length < 60 && !/[.:;,]$/.test(first);
        return heading ? <section key={i}><h2>{first}</h2><p>{rest.join("\n")}</p></section> : <p key={i}>{b}</p>;
      })}
    </div>
  );
}

export function InfoPage() {
  const ctx = useStore();
  const to = usePath();
  const { kind = "" } = useParams();
  const [d, setD] = useState<Info | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => { setD(null); setMissing(false); ctx.api<Info>(`/info/${kind}`).then(setD).catch(() => setMissing(true)); }, [kind, ctx]);
  const title = INFO.find(([k]) => k === kind)?.[1] || "Información";
  useHead({ title: `${title} · ${ctx.store.name}`, description: `${title} de la tienda ${ctx.store.name}.`, path: `/info/${kind}`, noindex: missing });
  return (
    <div className="sh-wrap vt-infopage">
      <Crumbs items={[{ name: "Tienda", path: "/" }, { name: title }]} />
      <div className="vt-infopage__grid">
        <nav className="vt-infonav" aria-label="Información de la tienda">
          {INFO.map(([k, l]) => <Link key={k} to={to(`/info/${k}`)} aria-current={k === kind ? "page" : undefined}>{l}</Link>)}
          <Link to={to("/asesoria")}>Asesoría personalizada</Link>
        </nav>
        <article className="vt-infopage__body">
          <h1>{title}</h1>
          {missing ? <EmptyHelp title="Este contenido todavía no está publicado." text="Escribinos y te respondemos cualquier duda." />
            : !d ? <div className="sh-skel" style={{ height: 240 }} />
              : d.faq.length ? (
                <div className="vt-faq">{d.faq.map((x) => <details key={x.q}><summary>{x.q}</summary><p>{x.a}</p></details>)}</div>
              ) : <Prose text={d.body} />}
        </article>
      </div>
    </div>
  );
}

type CmpItem = VProd & { specs: Record<string, string> };
const SPEC_ORDER = ["Tipo", "Marca", "Modelo", "Resolución", "Lente", "Visión nocturna", "Formato", "Protección", "PoE", "Alimentación", "Conexión", "Analítica", "Audio", "Zoom óptico", "Canales", "Capacidad", "Puertos"];

export function ComparePage() {
  const ctx = useStore();
  const to = usePath();
  const [d, setD] = useState<CmpItem[] | null>(null);
  const ids = ctx.compare;
  useHead({ title: `Comparar productos · ${ctx.store.name}`, noindex: true, path: "/comparar" });
  useEffect(() => {
    if (!ids.length) { setD([]); return; }
    ctx.api<{ items: CmpItem[] }>(`/catalog?ids=${ids.join(",")}`).then((r) => setD(r.items)).catch(() => setD([]));
  }, [ids.join(","), ctx]); // eslint-disable-line react-hooks/exhaustive-deps
  const avail = useAvailability(ids);
  const rows = useMemo(() => {
    const keys = new Set<string>();
    (d || []).forEach((p) => Object.keys(p.specs).forEach((k) => keys.add(k)));
    return [...keys].sort((a, b) => (SPEC_ORDER.indexOf(a) + 1 || 99) - (SPEC_ORDER.indexOf(b) + 1 || 99));
  }, [d]);
  return (
    <div className="sh-wrap vt-compare">
      <Crumbs items={[{ name: "Tienda", path: "/" }, { name: "Comparar" }]} />
      <header className="vt-cathead"><h1>Comparar productos</h1>{d && d.length > 1 && <p>Lo que cambia entre ellos va resaltado; lo que tienen en común queda atenuado.</p>}</header>
      {!d ? <div className="sh-skel" style={{ height: 300 }} /> : d.length === 0 ? (
        <EmptyHelp title="No hay productos para comparar." text="Marcá “Comparar” en hasta 3 productos del catálogo."><Link className="sh-btn" to={to("/catalogo")}>Ir al catálogo</Link></EmptyHelp>
      ) : (
        <div className="vt-cmpwrap" role="region" aria-label="Tabla comparativa" tabIndex={0}>
          <table className="vt-cmptable">
            <thead>
              <tr>
                <th scope="col"><span className="sh-meta">{d.length} de 3</span></th>
                {d.map((p) => (
                  <th key={p.id} scope="col">
                    <Link to={to(p.path)} className="vt-cmp__prod"><Viewfinder>{p.image ? <img src={p.image} alt="" loading="lazy" width={140} height={140} /> : <Ico d={IC.cam} />}</Viewfinder><span>{p.name}</span></Link>
                    <button className="sh-rm" onClick={() => ctx.toggleCompare(p.id)}>Quitar</button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr><th scope="row">Precio</th>{d.map((p) => <td key={p.id}><PriceTag p={p} /></td>)}</tr>
              <tr><th scope="row">Disponibilidad</th>{d.map((p) => <td key={p.id}><AvailBadge a={avail[p.id] || p.availability} /></td>)}</tr>
              {rows.map((k) => {
                // lo que es igual en todos se atenua: el ojo va directo a lo que cambia
                const same = d.length > 1 && d.every((p) => (p.specs[k] || "") === (d[0].specs[k] || ""));
                return <tr key={k} data-same={same || undefined}><th scope="row">{k}</th>{d.map((p) => <td key={p.id}>{p.specs[k] || "—"}</td>)}</tr>;
              })}
              <tr><th scope="row"><span className="sh-sr">Acciones</span></th>{d.map((p) => (
                <td key={p.id}>
                  {p.price_on_request || !Number(p.display_price) ? <Link className="sh-btn sh-btn--sm sh-btn--ghost" to={to(p.path)}>Consultar</Link>
                    : <button className="sh-btn sh-btn--sm sh-btn--crimson" onClick={() => ctx.addToCart(p, 1)}><Ico d={IC.bag} />Agregar</button>}
                </td>
              ))}</tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function BlockPage({ primary }: { primary: string }) {
  const ctx = useStore();
  const to = usePath();
  const nav = useNavigate();
  const { page = "" } = useParams();
  const [d, setD] = useState<{ title: string; blocks: Block[] } | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => { setMissing(false); ctx.api<{ title: string; blocks: Block[] }>(`/pages/${page}`).then(setD).catch(() => setMissing(true)); }, [page, ctx]);
  useHead(d ? { title: `${d.title} · ${ctx.store.name}`, path: `/pg/${page}` } : null);
  if (missing) return <NotFound />;
  if (!d) return <div className="sh-wrap"><div className="sh-skel" style={{ height: 300, marginTop: 24 }} /></div>;
  return <Blocks slug={ctx.slug} blocks={d.blocks} primary={primary} products={[] as PubProduct[]} onCta={(t) => nav(t === "productos" ? to("/catalogo") : to(`/pg/${t}`))} onProduct={(id) => nav(to(`/p/producto-${id}`))} />;
}

export function NotFound() {
  const ctx = useStore();
  const to = usePath();
  useHead({ title: `Página no encontrada · ${ctx.store.name}`, noindex: true });
  return <div className="sh-wrap"><EmptyHelp title="No encontramos esta página." text="Puede que el enlace sea viejo. Buscá el producto o mirá el catálogo."><Link className="sh-btn" to={to("/catalogo")}>Ver catálogo</Link></EmptyHelp></div>;
}
