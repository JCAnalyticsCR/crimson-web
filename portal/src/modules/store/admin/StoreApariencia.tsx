/* Mi Tienda -> Apariencia (O4-06): lo que se elige aqui SI cambia la tienda. Nombre, frase, logo y WhatsApp, y el
   tema: «Clásico» (Arial, el de hoy) o «Crimson» (Archivo, solo con la aprobacion del dueno, O2-04), color de acento,
   radio de marcos y botones y fondo de pagina (crema o blanco; reemplaza al «color secundario», que no se usaba).
   - Se guarda en settings.store.theme y la tienda lo aplica como variables del contenedor .shop (theme.ts).
   - Vista previa en vivo con los componentes reales de la tienda (la tarjeta de producto y el boton), en 375, 768 y
     escritorio; en tablet y telefono se abre a pantalla completa. Nada se guarda hasta «Guardar y publicar».
   - Verificador de contraste del acento con texto blanco: si no llega a 4,5 avisa antes de guardar. */
import { useState } from "react";
import { Card, Field, Loading } from "../../../ui/components";
import { ImageField } from "../../../ui/MediaPicker";
import { THEME_DEFAULT, accentOk, contrast, type Theme } from "../theme";
import { SaveBar } from "./SaveBar";
import { ColorInput, isHex } from "./fields";
import { PreviewPane } from "./PreviewPane";
import { useDraft } from "./useDraft";

type Ap = { name: string; tagline: string; logo_url: string | null; whatsapp: string; primary: string; theme: Required<Theme> };
export const AP_LABELS: Record<string, string> = { name: "Nombre", tagline: "Frase", logo_url: "Logo", whatsapp: "WhatsApp", primary: "Color de acento", theme: "Apariencia" };
/* el tema Crimson (Archivo) espera la decision del dueno (plan O2-04): no se ofrece hasta que la apruebe */
const ARCHIVO_APROBADO = false;

export default function StoreApariencia() {
  const d = useDraft<Ap>({
    key: "apariencia:store",
    url: "/store",
    read: (r) => {
      const t = (r.theme as Theme | null) || {};
      const primary = String(r.primary ?? THEME_DEFAULT.accent);
      return {
        name: String(r.name ?? ""), tagline: String(r.tagline ?? ""), logo_url: (r.logo_url as string) || null, whatsapp: String(r.whatsapp ?? ""), primary,
        theme: { preset: t.preset || "clasico", accent: t.accent || primary, radius: typeof t.radius === "number" ? t.radius : THEME_DEFAULT.radius, background: t.background || "crema" },
      };
    },
  });
  const [ver, setVer] = useState(false);
  if (d.error) return <Card title="Apariencia"><p className="mt-lead">{d.error}</p></Card>;
  if (!d.draft) return <Loading />;
  const a = d.draft;
  const th = a.theme;
  const setTh = (p: Partial<Ap["theme"]>) => d.set({ theme: { ...th, ...p }, ...(p.accent ? { primary: p.accent } : {}) });
  const ratio = isHex(th.accent) ? contrast(th.accent, "#ffffff") : 0;
  return (
    <>
      <div className="ap-grid">
        <div className="mt-stack">
          <Card title="Identidad de la tienda">
            <div className="grid-2">
              <Field label="Nombre de la tienda"><input className="input" value={a.name} maxLength={120} onChange={(e) => d.set({ name: e.target.value })} /></Field>
              <Field label="Frase"><input className="input" value={a.tagline} maxLength={200} onChange={(e) => d.set({ tagline: e.target.value })} /></Field>
              <Field label="Logo"><ImageField value={a.logo_url} label="Logo" onChange={(url) => d.set({ logo_url: url })} /></Field>
              <Field label="WhatsApp" hint="Con código de país, por ejemplo +506 8888 8888."><input className="input" value={a.whatsapp} maxLength={40} onChange={(e) => d.set({ whatsapp: e.target.value })} /></Field>
            </div>
          </Card>
          <Card title="Tema">
            <div className="mt-stack">
              <Field label="Tema">
                <select className="select" value={th.preset} onChange={(e) => setTh({ preset: e.target.value as Theme["preset"] & string })}>
                  <option value="clasico">Clásico (Arial, el de hoy)</option>
                  <option value="crimson" disabled={!ARCHIVO_APROBADO}>Crimson (Archivo){ARCHIVO_APROBADO ? "" : " · espera su aprobación"}</option>
                </select>
              </Field>
              <Field label="Color de acento" hint="Botones, enlaces y el precio. Lleva texto blanco encima."><ColorInput label="Color de acento" value={th.accent} onChange={(v) => setTh({ accent: v })} /></Field>
              <p className={ratio >= 4.5 ? "meta" : "mt-warn"} style={{ textTransform: "none", margin: 0 }} role={ratio >= 4.5 ? undefined : "alert"}>
                Contraste con texto blanco: {ratio.toLocaleString("es-CR")} a 1 {ratio >= 4.5 ? "(se lee bien)" : "(menos de 4,5: el texto de los botones cuesta leerlo; elija un tono más oscuro)"}
              </p>
              <Field label="Bordes" hint="Radio de marcos y botones."><select className="select" value={th.radius} onChange={(e) => setTh({ radius: Number(e.target.value) })}>{[[0, "Rectos"], [4, "Apenas redondeados"], [6, "Redondeados (el de hoy)"], [10, "Más redondeados"], [16, "Muy redondeados"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
              <Field label="Fondo de página"><select className="select" value={th.background} onChange={(e) => setTh({ background: e.target.value as "crema" | "blanco" })}><option value="crema">Crema (el de hoy)</option><option value="blanco">Blanco</option></select></Field>
            </div>
          </Card>
        </div>
        <div className="ap-preview">
          <PreviewPane theme={th} name={a.name} tagline={a.tagline} logo={a.logo_url} full={ver} onFull={setVer} />
        </div>
      </div>
      <SaveBar drafts={[d]} labels={AP_LABELS} validate={() => {
        if (!isHex(th.accent)) return "Escriba el color como #RRGGBB, por ejemplo #E2233A.";
        if (!a.name.trim()) return "La tienda necesita un nombre.";
        if (!accentOk(th.accent) && !confirm(`El color ${th.accent} tiene poco contraste con el texto blanco (${ratio} a 1). ¿Guardarlo igual?`)) return "Elija un acento más oscuro o confirme.";
        return null;
      }} />
    </>
  );
}
