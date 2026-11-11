/* Mi Tienda -> Apariencia (O4-01): nombre, frase, logo, colores, tipografia y WhatsApp (movidos del panel de una
   pagina). O4-02 les pone el guardado unico y O4-06 la vista previa en vivo. */
import { Card, Field, Loading } from "../../../ui/components";
import { ImageField } from "../../../ui/MediaPicker";
import { ColorField, useStoreCfg, type StoreCfg } from "../Store";

export default function StoreApariencia() {
  const { cfg, saveCfg } = useStoreCfg();
  if (!cfg) return <Loading />;
  const saveText = (k: "name" | "tagline" | "whatsapp", v: string) => { if (v.trim() !== (cfg[k] || "").trim()) void saveCfg({ [k]: v } as Partial<StoreCfg>); };
  return (
    <Card title="Identidad de la tienda">
      <div className="grid-2">
        <Field label="Nombre de la tienda"><input className="input" defaultValue={cfg.name} onBlur={(e) => saveText("name", e.target.value)} /></Field>
        <Field label="Frase"><input className="input" defaultValue={cfg.tagline} onBlur={(e) => saveText("tagline", e.target.value)} /></Field>
        <Field label="Color principal"><ColorField value={cfg.primary} onSave={(v) => saveCfg({ primary: v })} /></Field>
        <Field label="Color secundario"><ColorField value={cfg.secondary} onSave={(v) => saveCfg({ secondary: v })} /></Field>
        <Field label="Tipografía"><select className="select" value={cfg.font} onChange={(e) => saveCfg({ font: e.target.value })}>{["Arial"].map((f) => <option key={f}>{f}</option>)}</select></Field>
        <Field label="Logo"><ImageField value={cfg.logo_url} label="Logo" onChange={(url) => saveCfg({ logo_url: url })} /></Field>
        <Field label="WhatsApp"><input className="input" defaultValue={cfg.whatsapp} onBlur={(e) => saveText("whatsapp", e.target.value)} /></Field>
      </div>
    </Card>
  );
}
