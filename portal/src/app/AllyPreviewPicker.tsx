/* "Ver como este aliado" (solo administrador). Andrés se confundió con la vista previa por rol (13/10, 08:47-09:28)
   porque mostraba SUS datos: para los aliados la vista previa usa los datos REALES de la empresa elegida, en solo
   lectura (la API la aplica con X-View-As-Ally, core/ally.py). Para "Técnico de aliado" se elige además la persona. */
import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { Field, I, Icon, Modal } from "../ui/components";
import { Lookup, type LookupItem } from "../ui/Lookup";
import { useSession } from "./session";

type PreviewOut = { company: { id: number; name: string; state: string }; users: { id: number; name: string; role: string; active: boolean }[] };

export default function AllyPreviewPicker({ role, company, onClose }: { role: "aliado" | "tecnico_aliado"; company?: { id: number; name: string } | null; onClose: () => void }) {
  const { setAllyPreview, toast } = useSession();
  const [sel, setSel] = useState<{ id: number; name: string } | null>(company || null);
  const [rol, setRol] = useState(role);
  const [people, setPeople] = useState<PreviewOut["users"] | null>(null);
  const [uid, setUid] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const fetcher = useCallback(async (q: string): Promise<LookupItem[]> => {
    const r = await api<{ id: number; name: string; tax_id: string | null }[]>(`/allies/options?q=${encodeURIComponent(q)}`);
    return r.map((x) => ({ id: x.id, label: x.name, hint: x.tax_id || undefined }));
  }, []);

  const cargar = async (c: { id: number; name: string }) => {
    setSel(c);
    setPeople(null);
    setUid("");
    try {
      const r = await api<PreviewOut>(`/allies/${c.id}/preview`, { method: "POST", json: {} });
      setPeople(r.users.filter((u) => u.active));
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo abrir la vista previa", "bad"); }
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
      // queda en la bitacora (preview_ally) con la persona elegida
      await api(`/allies/${sel.id}/preview`, { method: "POST", json: { user_id: persona?.id ?? null } });
      setAllyPreview({ company_id: sel.id, user_id: persona?.id ?? null, role: persona ? (persona.role as "aliado" | "tecnico_aliado") : rol, name: sel.name, user_name: persona?.name ?? null });
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
