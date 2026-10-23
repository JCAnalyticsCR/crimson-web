/* Ventas etapa 2 · traspaso de cartera de un clic: los clientes, oportunidades abiertas y cotizaciones abiertas de un
   vendedor pasan a otro. Primero la vista previa con los conteos y luego la confirmación. Lo ya devengado o pendiente en
   comisiones se queda con el vendedor original. Lo usan Ajustes → Usuarios (también al desactivar a un vendedor con
   cartera). Funciona a 768 px: los conteos son una cuadrícula que se acomoda sola. */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Field, Loading, Modal } from "../../ui/components";

type Counts = { clientes: number; oportunidades: number; cotizaciones: number; cotizaciones_con_comision: number };
type Preview = { from: { id: number; name: string | null }; counts: Counts; receivers: { id: number; name: string }[] };

export default function PortfolioTransfer({ userId, onClose, onDone, intro }: { userId: number; onClose: () => void; onDone?: () => void; intro?: string }) {
  const { toast } = useSession();
  const [pv, setPv] = useState<Preview | null>(null);
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<Preview>(`/cartera/sellers/${userId}`).then(setPv).catch((e) => setErr(e instanceof Error ? e.message : "Error"));
  }, [userId]);

  const vacio = pv ? !pv.counts.clientes && !pv.counts.oportunidades && !pv.counts.cotizaciones : false;
  const destino = pv?.receivers.find((r) => String(r.id) === to);

  const go = async () => {
    if (!pv || !destino) return;
    if (!confirm(`¿Pasar la cartera de ${pv.from.name || "este vendedor"} a ${destino.name}?`)) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<{ counts: Counts }>(`/cartera/sellers/${userId}/transfer`, { method: "POST", json: { to_user_id: destino.id } });
      toast(`Cartera traspasada a ${destino.name}: ${r.counts.clientes} clientes, ${r.counts.oportunidades} oportunidades y ${r.counts.cotizaciones} cotizaciones`);
      onDone?.(); onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : "Error"); }
    finally { setBusy(false); }
  };

  const box = (label: string, n: number) => (
    <div style={{ padding: "10px 12px", borderRadius: 10, background: "var(--bg-2)" }}><div className="meta">{label}</div><b style={{ fontSize: 22 }}>{n}</b></div>
  );

  return (
    <Modal title={`Traspasar cartera${pv?.from.name ? ` · ${pv.from.name}` : ""}`} onClose={onClose}
      foot={<><button className="btn btn--ghost" onClick={onClose}>{vacio ? "Cerrar" : "Ahora no"}</button>{!vacio && <button className="btn btn--crimson" onClick={go} disabled={busy || !destino}>{busy ? "Traspasando…" : "Traspasar cartera"}</button>}</>}>
      {intro && <p style={{ fontSize: 13, margin: 0 }}>{intro}</p>}
      {!pv && !err && <Loading />}
      {pv && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
            {box("Clientes", pv.counts.clientes)}
            {box("Oportunidades abiertas", pv.counts.oportunidades)}
            {box("Cotizaciones abiertas", pv.counts.cotizaciones)}
          </div>
          {pv.counts.cotizaciones_con_comision > 0 && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>{pv.counts.cotizaciones_con_comision} cotización(es) abierta(s) ya generaron comisión: se quedan con el vendedor original.</p>}
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Las facturas y las comisiones ya devengadas o pendientes se quedan con el vendedor original. El traspaso queda en la bitácora y quien recibe la cartera recibe un aviso por correo.</p>
          {vacio ? <p style={{ fontSize: 13, margin: 0 }}>Este vendedor no tiene cartera que traspasar.</p> : (
            <Field label="¿Quién recibe la cartera?">
              <select className="select" value={to} onChange={(e) => setTo(e.target.value)}>
                <option value="">Elija un vendedor…</option>
                {pv.receivers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </Field>
          )}
        </>
      )}
      {err && <p style={{ color: "var(--bad)", fontSize: 13, margin: 0 }} role="alert">{err}</p>}
    </Modal>
  );
}
