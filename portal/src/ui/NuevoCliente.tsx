/* Cliente nuevo sin salir de la pantalla (cotización, punto de venta): lo mínimo para vender; la ficha se completa
   después en Clientes. El API deja el cliente en la cartera de quien lo crea si es vendedor (cartera con dueño), y si
   ya existe uno con esa cédula o ese correo se usa esa ficha en lugar de duplicarla. */
import { useState } from "react";
import { ApiError, api, type Customer } from "../lib/api";
import { useSession } from "../app/session";
import { Field, I, Icon, Modal } from "./components";

export default function NuevoCliente({
  onClose,
  onCreated,
  emailHint = "Para mandarle la cotización",
  needId = false,
}: {
  onClose: () => void;
  onCreated: (c: Customer) => void;
  emailHint?: string;
  /** factura electrónica: la cédula es obligatoria */
  needId?: boolean;
}) {
  const { toast } = useSession();
  const [f, setF] = useState({ name: "", id_type: "fisica", id_number: "", email: "", phone: "" });
  const [busy, setBusy] = useState(false);
  const crear = async () => {
    setBusy(true);
    try {
      const c = await api<Customer>("/customers", { method: "POST", json: { name: f.name.trim(), id_type: f.id_type, id_number: f.id_number.trim() || null, email: f.email.trim() || null, phone: f.phone.trim() || null } });
      toast(`Cliente ${c.name} creado`); onCreated(c);
    } catch (e) {
      /* ya existe un cliente con esa cédula o ese correo (y no puede editar clientes): se usa esa ficha */
      const ex = e instanceof ApiError && e.status === 409 ? (e.data as { existing?: { id: number } } | undefined)?.existing : undefined;
      if (ex) {
        try { const c = await api<Customer>(`/customers/${ex.id}`); toast(`Ya existía: se usa la ficha de ${c.name}`); onCreated(c); return; } catch { /* abajo */ }
      }
      toast(e instanceof Error ? e.message : "No se pudo crear el cliente", "bad");
    }
    finally { setBusy(false); }
  };
  const listo = f.name.trim().length >= 2 && (!needId || f.id_number.trim().length >= 9);
  return (
    <Modal title="Cliente nuevo" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={busy || !listo} onClick={crear}><Icon d={I.check} />Crear y usar</button></>}>
      <div className="grid-2">
        <Field label="Nombre o razón social"><input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Tipo de identificación"><select className="select" value={f.id_type} onChange={(e) => setF({ ...f, id_type: e.target.value })}><option value="fisica">Física</option><option value="juridica">Jurídica</option><option value="dimex">DIMEX</option><option value="nite">NITE</option><option value="extranjero">Extranjero</option></select></Field>
        <Field label={needId ? "Cédula" : "Cédula (opcional)"} hint={needId ? "La factura electrónica la necesita" : undefined}><input className="input" value={f.id_number} onChange={(e) => setF({ ...f, id_number: e.target.value })} /></Field>
        <Field label="Correo (opcional)" hint={emailHint}><input className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Teléfono (opcional)"><input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}
