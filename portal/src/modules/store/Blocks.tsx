/* Render de bloques: el MISMO componente se usa en el editor (vista previa) y en el sitio publico. */
import { useState, type ReactNode } from "react";

export type Block = { type: string; [k: string]: unknown };
export type PubVariant = { id: number; name: string; price: string | number; options?: Record<string, string> };
export type PubProduct = { id: number; name: string; price: string | number; currency: string; image: string | null; description: string | null; images?: string[]; variants?: PubVariant[] };

const money = (v: string | number, cur = "CRC") => new Intl.NumberFormat("es-CR", { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(Number(v));

export function Blocks({ blocks, primary, products, onCta, onProduct, slug }: { blocks: Block[]; primary: string; products: PubProduct[]; onCta?: (to: string) => void; onProduct?: (id: number) => void; slug?: string }) {
  return <>{blocks.map((b, i) => <BlockView key={i} b={b} primary={primary} products={products} onCta={onCta} onProduct={onProduct} slug={slug} />)}</>;
}

function Section({ children, style }: { children: ReactNode; style?: React.CSSProperties }) {
  return <section style={{ padding: "48px 24px", maxWidth: 1100, margin: "0 auto", ...style }}>{children}</section>;
}

function BlockView({ b, primary, products, onCta, onProduct, slug }: { b: Block; primary: string; products: PubProduct[]; onCta?: (to: string) => void; onProduct?: (id: number) => void; slug?: string }) {
  const s = (k: string) => (b[k] as string) ?? "";
  switch (b.type) {
    case "hero": {
      const cta = b.cta as { label?: string; to?: string } | undefined;
      return (
        <section style={{ background: "#15131a", color: "#fff", padding: "72px 24px", position: "relative", overflow: "hidden" }}>
          <div style={{ maxWidth: 1100, margin: "0 auto" }}>
            <h1 style={{ fontFamily: "var(--display)", fontSize: "clamp(32px,5vw,58px)", lineHeight: 1.02, letterSpacing: "-0.03em", maxWidth: "16ch", margin: 0 }}>{s("title")}</h1>
            {s("text") && <p style={{ marginTop: 16, opacity: 0.8, fontSize: 17, maxWidth: "46ch" }}>{s("text")}</p>}
            {cta?.label && <button onClick={() => onCta?.(cta.to || "")} style={{ marginTop: 26, background: primary, color: "#fff", border: 0, borderRadius: 10, padding: "13px 22px", fontWeight: 700, cursor: "pointer" }}>{cta.label}</button>}
          </div>
          <div style={{ position: "absolute", inset: 0, background: `radial-gradient(60% 80% at 80% 20%, ${primary}22, transparent 70%)`, pointerEvents: "none" }} />
        </section>
      );
    }
    case "text":
      return <Section><h2 style={{ fontFamily: "var(--display)", fontSize: 30, letterSpacing: "-0.02em" }}>{s("title")}</h2><p style={{ marginTop: 12, color: "var(--text-2)", lineHeight: 1.6, maxWidth: "70ch", whiteSpace: "pre-wrap" }}>{s("text")}</p></Section>;
    case "image_text":
      return (
        <Section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 32, alignItems: "center" }}>
          <div style={{ order: b.reverse ? 2 : 1 }}>{s("image") ? <img src={s("image")} alt="" style={{ borderRadius: 14, width: "100%" }} /> : <div style={{ aspectRatio: "4/3", background: "var(--bg-2)", borderRadius: 14, display: "grid", placeItems: "center", color: "var(--text-3)" }}>Imagen</div>}</div>
          <div style={{ order: b.reverse ? 1 : 2 }}><h2 style={{ fontFamily: "var(--display)", fontSize: 28, letterSpacing: "-0.02em" }}>{s("title")}</h2><p style={{ marginTop: 10, color: "var(--text-2)", lineHeight: 1.6 }}>{s("text")}</p></div>
        </Section>
      );
    case "cta":
      return (
        <Section style={{ display: "flex", gap: 20, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", borderTop: "1px solid var(--hair)", borderBottom: "1px solid var(--hair)" }}>
          <h2 style={{ fontFamily: "var(--display)", fontSize: 28, letterSpacing: "-0.02em" }}>{s("title")}</h2>
          <button onClick={() => onCta?.(s("to"))} style={{ background: primary, color: "#fff", border: 0, borderRadius: 10, padding: "13px 22px", fontWeight: 700, cursor: "pointer" }}>{s("label") || "Escribinos"}</button>
        </Section>
      );
    case "columns":
      return (
        <Section style={{ display: "grid", gridTemplateColumns: `repeat(auto-fit, minmax(${(b.cols as unknown[]).length > 3 ? 200 : 240}px, 1fr))`, gap: 18 }}>
          {(b.cols as { title: string; text: string }[]).map((c, i) => (
            <div key={i} style={{ padding: 20, border: "1px solid var(--hair)", borderRadius: 14 }}>
              <div style={{ width: 34, height: 3, background: primary, marginBottom: 12 }} />
              <h3 style={{ fontFamily: "var(--display)", fontSize: 18 }}>{c.title}</h3>
              <p style={{ marginTop: 6, color: "var(--text-2)", fontSize: 14, lineHeight: 1.5 }}>{c.text}</p>
            </div>
          ))}
        </Section>
      );
    case "products": {
      const list = products.slice(0, Number(b.limit) || 6);
      return (
        <Section>
          <h2 style={{ fontFamily: "var(--display)", fontSize: 28, letterSpacing: "-0.02em", marginBottom: 18 }}>{s("title") || "Productos"}</h2>
          {list.length === 0 ? <p className="muted">Marcá productos con “Mostrar en sitio web” para verlos aquí.</p> : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 16 }}>
              {list.map((p) => (
                <button key={p.id} onClick={() => onProduct?.(p.id)} style={{ textAlign: "left", border: "1px solid var(--hair)", borderRadius: 14, overflow: "hidden", background: "var(--surface)", cursor: "pointer", padding: 0 }}>
                  <div style={{ aspectRatio: "4/3", background: "var(--bg-2)", display: "grid", placeItems: "center" }}>{p.image ? <img src={p.image} alt={p.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <span style={{ color: "var(--text-3)", fontSize: 12 }}>Sin imagen</span>}</div>
                  <div style={{ padding: 12 }}><b style={{ fontSize: 14, display: "block" }}>{p.name}</b><span style={{ color: primary, fontWeight: 700, fontFamily: "var(--mono)" }}>{money(p.price, p.currency)}</span></div>
                </button>
              ))}
            </div>
          )}
        </Section>
      );
    }
    case "gallery":
      return (
        <Section>
          <h2 style={{ fontFamily: "var(--display)", fontSize: 28, letterSpacing: "-0.02em", marginBottom: 18 }}>{s("title")}</h2>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
            {((b.images as string[]) || []).map((src, i) => <img key={i} src={src} alt="" style={{ width: "100%", aspectRatio: "4/3", objectFit: "cover", borderRadius: 12 }} />)}
            {((b.images as string[]) || []).length === 0 && <div style={{ aspectRatio: "4/3", background: "var(--bg-2)", borderRadius: 12, display: "grid", placeItems: "center", color: "var(--text-3)" }}>Agregá imágenes a la galería</div>}
          </div>
        </Section>
      );
    case "form":
      return <ContactForm b={b} primary={primary} slug={slug} />;
    default:
      return <Section><p className="muted">Bloque “{b.type}” no soportado.</p></Section>;
  }
}

/* Formulario de contacto: en el sitio publico envia a /public/store/{slug}/contact (queda como nota en la ficha
   del cliente y llega por correo a los administradores). En el editor (sin slug) es solo vista previa. */
function ContactForm({ b, primary, slug }: { b: Block; primary: string; slug?: string }) {
  const fields = ((b.fields as string[]) || ["nombre", "correo", "mensaje"]);
  const [v, setV] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "sending" | "ok" | "error">("idle");
  const [msg, setMsg] = useState("");
  const send = async () => {
    if (!slug) return;
    setState("sending");
    const r = await fetch(`/api/public/store/${slug}/contact`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: v.nombre || "", email: v.correo || "", phone: v.telefono || null, message: v.mensaje || "" }) });
    if (r.ok) { setState("ok"); setV({}); } else { setState("error"); try { setMsg((await r.json()).detail); } catch { setMsg("No se pudo enviar"); } }
  };
  const ready = (v.nombre || "").trim().length > 1 && /.+@.+\..+/.test(v.correo || "") && (v.mensaje || "").trim().length > 2;
  return (
    <Section style={{ maxWidth: 620 }}>
      <h2 style={{ fontFamily: "var(--display)", fontSize: 28, letterSpacing: "-0.02em" }}>{(b.title as string) || "Contacto"}</h2>
      {state === "ok" ? <p style={{ marginTop: 14, padding: 16, borderRadius: 12, background: "var(--ok-soft)", color: "var(--ok)", fontWeight: 600 }}>¡Gracias! Recibimos tu mensaje y te contactamos pronto.</p> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 14 }}>
          {fields.map((f) => f === "mensaje"
            ? <textarea key={f} className="textarea" placeholder="Mensaje" value={v[f] || ""} onChange={(e) => setV({ ...v, [f]: e.target.value })} />
            : <input key={f} className="input" type={f === "correo" ? "email" : "text"} placeholder={f[0].toUpperCase() + f.slice(1)} value={v[f] || ""} onChange={(e) => setV({ ...v, [f]: e.target.value })} />)}
          {state === "error" && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }}>{msg}</p>}
          <button onClick={send} disabled={!slug || !ready || state === "sending"} style={{ background: primary, color: "#fff", border: 0, borderRadius: 10, padding: "12px 20px", fontWeight: 700, alignSelf: "flex-start", cursor: "pointer", opacity: !slug || !ready ? 0.6 : 1 }}>{state === "sending" ? "Enviando…" : "Enviar"}</button>
        </div>
      )}
    </Section>
  );
}
