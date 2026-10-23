/* Ventas etapa 2 · cartera con dueño: «Vendedor de la cuenta» en la ficha del cliente.
   Todos lo ven; el administrador y el jefe de ventas (crm.cartera) lo cambian. El cambio queda en la bitácora y el
   nuevo vendedor recibe un aviso. No toca comisiones. En tablet (768 px) el formulario se acomoda en una columna. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, Modal } from "../../ui/components";

type Seller = { id: number; name: string };

export function AccountOwner({ customerId, ownerId, ownerName, onChanged }: { customerId: number; ownerId: number | null; ownerName: string | null; onChanged: () => void }) {
  const { allows, toast } = useSession();
  const puede = allows("crm.cartera");
  const [open, setOpen] = useState(false);
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [sel, setSel] = useState<string>(ownerId ? String(ownerId) : "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || sellers) return;
    api<Seller[]>("/cartera/sellers").then(setSellers).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"));
  }, [open, sellers, toast]);
  useEffect(() => { setSel(ownerId ? String(ownerId) : ""); }, [ownerId]);

  const save = async () => {
    setBusy(true);
    try {
      await api(`/customers/${customerId}/owner`, { method: "PUT", json: { owner_id: sel ? Number(sel) : null, reason: reason.trim() || null } });
      toast(sel ? "Vendedor de la cuenta actualizado: se le avisó por correo" : "El cliente quedó sin vendedor");
      setOpen(false); setReason(""); onChanged();
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "10px 12px", borderRadius: 10, background: "var(--bg-2)", fontSize: 13, marginBottom: 14 }}>
        <span className="meta">Vendedor de la cuenta</span>
        <b>{ownerName || <span className="muted">Sin vendedor</span>}</b>
        {puede && <button className="btn btn--ghost btn--sm" style={{ marginLeft: "auto" }} onClick={() => setOpen(true)}>Cambiar vendedor</button>}
      </div>
      {open && (
        <Modal title="Cambiar el vendedor de la cuenta" onClose={() => setOpen(false)} foot={<><button className="btn btn--ghost" onClick={() => setOpen(false)}>Cancelar</button><button className="btn btn--crimson" onClick={save} disabled={busy || sel === (ownerId ? String(ownerId) : "")}>{busy ? "Guardando…" : "Guardar"}</button></>}>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>El nuevo vendedor ve al cliente en su cartera y recibe un aviso por correo. Las comisiones de lo ya vendido no cambian.</p>
          <Field label="Vendedor">
            <select className="select" value={sel} onChange={(e) => setSel(e.target.value)} disabled={!sellers}>
              <option value="">Sin vendedor</option>
              {(sellers || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Motivo (queda en la bitácora)"><input className="input" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Opcional" /></Field>
        </Modal>
      )}
    </>
  );
}
