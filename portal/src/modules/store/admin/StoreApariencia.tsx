/* Mi Tienda -> Apariencia: nombre, frase, logo, colores, tipografia y WhatsApp. Con el guardado unico (O4-02): se
   escribe en un borrador y se publica con «Guardar y publicar». O4-06 le agrega el tema y la vista previa en vivo. */
import { Card, Field, Loading } from "../../../ui/components";
import { ImageField } from "../../../ui/MediaPicker";
import { SaveBar } from "./SaveBar";
import { ColorInput, isHex } from "./fields";
import { useDraft } from "./useDraft";

type Ap = { name: string; tagline: string; logo_url: string | null; primary: string; secondary: string; font: string; whatsapp: string };
export const AP_LABELS: Record<string, string> = { name: "Nombre", tagline: "Frase", logo_url: "Logo", primary: "Color principal", secondary: "Color secundario", font: "Tipografía", whatsapp: "WhatsApp" };

export default function StoreApariencia() {
  const d = useDraft<Ap>({
    key: "apariencia:store",
    url: "/store",
    read: (r) => ({ name: String(r.name ?? ""), tagline: String(r.tagline ?? ""), logo_url: (r.logo_url as string) || null, primary: String(r.primary ?? "#e2233a"), secondary: String(r.secondary ?? "#15131a"), font: String(r.font ?? "Arial"), whatsapp: String(r.whatsapp ?? "") }),
  });
  if (d.error) return <Card title="Apariencia"><p className="mt-lead">{d.error}</p></Card>;
  if (!d.draft) return <Loading />;
  const a = d.draft;
  return (
    <>
      <Card title="Identidad de la tienda">
        <div className="grid-2">
          <Field label="Nombre de la tienda"><input className="input" value={a.name} maxLength={120} onChange={(e) => d.set({ name: e.target.value })} /></Field>
          <Field label="Frase"><input className="input" value={a.tagline} maxLength={200} onChange={(e) => d.set({ tagline: e.target.value })} /></Field>
          <Field label="Color principal"><ColorInput label="Color principal" value={a.primary} onChange={(v) => d.set({ primary: v })} /></Field>
          <Field label="Color secundario"><ColorInput label="Color secundario" value={a.secondary} onChange={(v) => d.set({ secondary: v })} /></Field>
          <Field label="Tipografía"><select className="select" value={a.font} onChange={(e) => d.set({ font: e.target.value })}>{["Arial"].map((f) => <option key={f}>{f}</option>)}</select></Field>
          <Field label="Logo"><ImageField value={a.logo_url} label="Logo" onChange={(url) => d.set({ logo_url: url })} /></Field>
          <Field label="WhatsApp" hint="Con código de país, por ejemplo +506 8888 8888."><input className="input" value={a.whatsapp} maxLength={40} onChange={(e) => d.set({ whatsapp: e.target.value })} /></Field>
        </div>
      </Card>
      <SaveBar drafts={[d]} labels={AP_LABELS} validate={() => (!isHex(a.primary) || !isHex(a.secondary) ? "Escriba los colores como #RRGGBB, por ejemplo #E2233A." : !a.name.trim() ? "La tienda necesita un nombre." : null)} />
    </>
  );
}
