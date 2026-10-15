/* "Ver como este aliado" (solo administrador). Andrés se confundió con la vista previa por rol (13/10, 08:47-09:28)
   porque mostraba SUS datos: para los aliados la vista previa usa los datos REALES de la empresa elegida, en solo
   lectura (la API la aplica con X-View-As-Ally, core/ally.py). Para "Técnico de aliado" se elige además la persona.
   Se busca entre TODAS las empresas aliadas (también suspendidas o inactivas: justo ahí sirve ver qué ven), las personas
   salen de /allies/{id}/users y la vista previa se activa una sola vez, al confirmar: esa activación es la que queda en
   la bitácora (y sin ella la API no abre la vista previa). */
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { Field, I, Icon, Modal } from "../ui/components";
import { Lookup, type LookupItem } from "../ui/Lookup";
import { useSession } from "./session";

type Estado = "activo" | "suspendido" | "inactivo";
type PreviewOut = { company: { id: number; name: string; state: Estado; suspended_reason: string | null; features?: { ganancias: boolean } } };
type Persona = { id: number; name: string; role: string; active: boolean };
const ESTADO: Record<string, string> = { suspendido: "Suspendido", inactivo: "Inactivo" };

export default function AllyPreviewPicker({ role, company, onClose }: { role: "aliado" | "tecnico_aliado"; company?: { id: number; name: string } | null; onClose: () => void }) {
  const { setAllyPreview, toast } = useSession();
  const [sel, setSel] = useState<{ id: number; name: string } | null>(company || null);
  const [rol, setRol] = useState(role);
  const [people, setPeople] = useState<Persona[] | null>(null);
  const [uid, setUid] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const fetcher = useCallback(async (q: string): Promise<LookupItem[]> => {
    const r = await api<{ id: number; name: string; tax_id: string | null; state: Estado }[]>(`/allies?estado=todos&q=${encodeURIComponent(q)}`);
    return r.slice(0, 20).map((x) => ({ id: x.id, label: x.name, hint: [x.tax_id, ESTADO[x.state]].filter(Boolean).join(" · ") || undefined }));
  }, []);

  const cargar = async (c: { id: number; name: string }) => {
    setSel(c);
    setPeople(null);
    setUid("");
    try {
      const r = await api<{ users: Persona[] }>(`/allies/${c.id}/users`);
      setPeople(r.users.filter((u) => u.active));
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudieron cargar las personas", "bad"); }
  };
  // desde la ficha del aliado la empresa ya viene elegida: se cargan sus personas para "Técnico de aliado"
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (company) cargar(company); }, []);
  const tecnicos = (people || []).filter((u) => u.role === "tecnico_aliado");
  const entrar = async () => {
    if (!sel) return;
    setBusy(true);
    try {
      const persona = uid ? (people || []).find((u) => u.id === uid) : null;
      // la unica activacion: queda en la bitacora (preview_ally) con la persona elegida
      const r = await api<PreviewOut>(`/allies/${sel.id}/preview`, { method: "POST", json: { user_id: persona?.id ?? null } });
      setAllyPreview({
        company_id: sel.id, user_id: persona?.id ?? null, role: persona ? (persona.role as "aliado" | "tecnico_aliado") : rol, name: r.company.name, user_name: persona?.name ?? null,
        state: r.company.state, suspended_reason: r.company.suspended_reason, features: r.company.features,
      });
      onClose();
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo abrir la vista previa", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <Modal
      title="Ver como este aliado"
      onClose={onClose}
      foot={<>
        <button className="btn btn--ghost" onClick={onClose}>Cancelar</button>
        <button className="btn btn--crimson" disabled={!sel || busy || (rol === "tecnico_aliado" && !uid)} onClick={entrar}><Icon d={I.arrow} />Abrir el portal del aliado</button>
      </>}
    >
      <p className="muted" style={{ marginTop: 0 }}>Vas a ver exactamente lo que ve la empresa aliada (sus datos reales), en solo lectura. Queda registrado en la bitácora.</p>
      <Field label="Empresa aliada">
        <Lookup value={sel?.name || ""} placeholder="Buscá el aliado por nombre o cédula" fetcher={fetcher} onSelect={(it) => { if (it) cargar({ id: it.id, name: it.label }); }} />
      </Field>
      <Field label="Ver como">
        <select className="select" value={rol} onChange={(e) => { setRol(e.target.value as "aliado" | "tecnico_aliado"); setUid(""); }}>
          <option value="aliado">Aliado comercial (toda la empresa)</option>
          <option value="tecnico_aliado">Técnico de aliado (una persona)</option>
        </select>
      </Field>
      {rol === "tecnico_aliado" && sel && (
        <Field label="Persona" hint={people && !tecnicos.length ? "Esta empresa no tiene técnicos activos." : undefined}>
          <select className="select" value={uid} onChange={(e) => setUid(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Elegí el técnico…</option>
            {tecnicos.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </Field>
      )}
    </Modal>
  );
}
