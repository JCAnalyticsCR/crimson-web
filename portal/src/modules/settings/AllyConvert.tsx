/* Pasar un usuario interno al portal de aliados (reunión 2, 21/10, pedido 20): una persona de una empresa aliada que
   quedó como usuario interno ve todo el panel de Crimson. Aquí su acceso pasa a ser del portal de aliados de su
   empresa (aliado comercial o técnico de aliado): desde ese momento solo ve lo que su empresa tiene en común con
   Crimson. Pide la contraseña de quien lo hace; no cambia nada más (sus órdenes o levantamientos internos se revisan
   aparte). */
import { useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, Modal } from "../../ui/components";
import { Lookup, searchAllies } from "../../ui/Lookup";

export type AllyMatch = { company_id: number; company: string; reason: "correo" | "dominio" | "nombre" };
type Who = { id: number; name: string; email: string; ally_match?: AllyMatch | null };
type Out = { pendientes: { ordenes: number; levantamientos: number; tickets: number } };

export function AllyMatchChip({ m }: { m: AllyMatch }) {
  const t = m.reason === "correo"
    ? `Su correo es de la empresa aliada ${m.company}: no puede tener un rol interno. Páselo al portal de aliados.`
    : m.reason === "dominio"
    ? `Su correo es del mismo dominio que la empresa aliada ${m.company}. Si trabaja para ese aliado, páselo al portal de aliados.`
    : `Se llama igual que la empresa aliada ${m.company} (o uno de sus contactos). Si es la misma persona, páselo al portal de aliados.`;
  return <span className="badge badge--warn" title={t} style={{ whiteSpace: "normal", lineHeight: 1.35, borderRadius: 8, padding: "3px 8px" }}>Parece del aliado {m.company}</span>;
}

export default function AllyConvert({ user, onClose, onDone }: { user: Who; onClose: () => void; onDone: () => void }) {
  const { toast } = useSession();
  // empresa elegida: arranca en la detectada; si no hay, se busca por nombre o cédula (las opciones vienen de a 20)
  const [sel, setSel] = useState<{ id: number; name: string } | null>(user.ally_match ? { id: user.ally_match.company_id, name: user.ally_match.company } : null);
  const [role, setRole] = useState("tecnico_aliado");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const cid = sel ? String(sel.id) : "";
  const nombre = sel?.name || "la empresa aliada";
  const pasar = async () => {
    setBusy(true);
    try {
      const r = await api<Out>(`/allies/${cid}/users/from-internal`, { method: "POST", json: { user_id: user.id, role, admin_password: pw } });
      const p = r.pendientes;
      const resto = p.ordenes + p.levantamientos + p.tickets;
      toast(resto ? `${user.name} ya entra al portal de ${nombre}. Tiene trabajo interno a su nombre (${p.ordenes} OT, ${p.levantamientos} levantamientos, ${p.tickets} tickets): revíselo.` : `${user.name} ya entra al portal de aliados de ${nombre}`, resto ? "bad" : "ok");
      onDone();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <Modal title={`Pasar al portal de aliados · ${user.name}`} onClose={onClose} foot={<>
      <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
      <button className="btn btn--crimson" disabled={busy || !cid || !pw} onClick={pasar}>{busy ? "Pasando…" : "Pasar al portal de aliados"}</button>
    </>}>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        {user.email} deja de ser usuario interno: ya no entra al panel de Crimson. Entra con el mismo correo y contraseña al portal de aliados y ve solo lo que su empresa tiene en común con Crimson. Se cierran sus sesiones abiertas y le avisamos por correo.
      </p>
      <Field label="Empresa aliada" hint="Solo aparecen las empresas aliadas activas y sin suspender.">
        <Lookup value={sel?.name || ""} placeholder="Busque la empresa por nombre o cédula" fetcher={searchAllies}
          onSelect={(it) => setSel(it ? { id: it.id, name: it.label } : null)} />
      </Field>
      <Field label="Rol en el portal" hint={role === "aliado" ? "Gerencia del aliado: oportunidades y proyectos en común, precios, su equipo y sus ganancias." : "Técnico: su trabajo asignado (levantamientos, órdenes y tickets) y las oportunidades en común en resumen. Sin precios."}>
        <select className="select" value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="tecnico_aliado">Técnico de aliado</option>
          <option value="aliado">Aliado comercial</option>
        </select>
      </Field>
      <Field label="Confirme con su contraseña"><input className="input" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
    </Modal>
  );
}
