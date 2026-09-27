/* Tabla de SLA por prioridad (horas de primera respuesta y de resolucion). La usan Ajustes (SLA de la
   empresa) y los contratos (SLA propio del cliente, que manda sobre el de la empresa). */
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card } from "../../ui/components";

export type Sla = Record<string, { respuesta: number; resolucion: number }>;
export const PRIORIDADES: [string, string][] = [["critica", "Crítica"], ["alta", "Alta"], ["media", "Media"], ["baja", "Baja"]];

export function SlaTable({ value, onChange, disabled }: { value: Sla; onChange: (v: Sla) => void; disabled?: boolean }) {
  const set = (p: string, k: "respuesta" | "resolucion", n: string) =>
    onChange({ ...value, [p]: { ...(value[p] || { respuesta: 0, resolucion: 0 }), [k]: Number(n) } });
  return (
    <table className="table">
      <thead><tr><th>Prioridad</th><th className="num">Primera respuesta (h)</th><th className="num">Resolución (h)</th></tr></thead>
      <tbody>
        {PRIORIDADES.map(([k, l]) => (
          <tr key={k}>
            <td>{l}</td>
            <td className="num"><input className="input input--mono" style={{ maxWidth: 110 }} type="number" min={1} max={2160} disabled={disabled} value={value[k]?.respuesta ?? ""} onChange={(e) => set(k, "respuesta", e.target.value)} /></td>
            <td className="num"><input className="input input--mono" style={{ maxWidth: 110 }} type="number" min={1} max={2160} disabled={disabled} value={value[k]?.resolucion ?? ""} onChange={(e) => set(k, "resolucion", e.target.value)} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* Pestaña de Ajustes: el SLA por defecto de la empresa. Solo edita quien tenga settings.configurar. */
export function SlaSettings() {
  const { toast, allows } = useSession();
  const [sla, setSla] = useState<Sla | null>(null);
  const puede = allows("settings.configurar");
  useEffect(() => { api<{ sla: Sla }>("/settings").then((c) => setSla(c.sla)); }, []);
  const save = () =>
    api<{ sla: Sla }>("/settings", { method: "PUT", json: { sla } })
      .then((c) => { setSla(c.sla); toast("SLA guardado; aplica a los tickets nuevos"); })
      .catch((e) => toast(e instanceof Error ? e.message : "Error", "bad"));
  if (!sla) return null;
  return (
    <Card title="SLA de soporte por prioridad" extra={puede ? <button className="btn btn--crimson btn--sm" onClick={save}>Guardar</button> : undefined}>
      <p className="muted" style={{ fontSize: 13.5, marginTop: 0 }}>
        Plazos que la empresa promete por defecto. Si el cliente tiene un contrato activo con SLA propio, manda el del contrato.
        El sistema avisa a administración y supervisión cuando un ticket se pasa del plazo de respuesta o de resolución.
      </p>
      <SlaTable value={sla} onChange={setSla} disabled={!puede} />
    </Card>
  );
}
