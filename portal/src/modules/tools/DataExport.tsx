/* Ajustes -> Respaldo: "Exportar todos mis datos" (solo administrador) y como se respalda la plataforma. */
import { useState } from "react";
import { downloadFile } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, I, Icon } from "../../ui/components";

export default function DataExport() {
  const { me, toast } = useSession();
  const [busy, setBusy] = useState(false);
  const admin = me?.role === "admin";
  const go = async () => {
    setBusy(true);
    try {
      const stamp = new Date().toLocaleDateString("en-CA", { timeZone: "America/Costa_Rica" });
      await downloadFile("/settings/data-export", `crimson-datos-${me?.tenant.slug || "empresa"}-${stamp}.zip`);
      toast("Exportación descargada");
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo exportar", "bad"); }
    finally { setBusy(false); }
  };
  return (
    <div style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 340px), 1fr))" }}>
      <Card title="Exportar todos mis datos">
        <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 13, lineHeight: 1.6 }}>
          <p className="muted" style={{ margin: 0 }}>Descarga un archivo .zip con <b>todos los datos de la empresa</b>: clientes, cotizaciones, facturas, pagos, inventario, campo, proyectos, vehículos, la bitácora y los archivos (fotos y PDF). Cada tabla va en un JSON legible y el archivo trae un manifiesto con conteos y huellas (SHA-256) para comprobar que está completo.</p>
          <p className="muted" style={{ margin: 0 }}>Por seguridad <b>no incluye</b> contraseñas, 2FA, sesiones ni las llaves de pasarelas y API. Cada descarga queda registrada en la bitácora.</p>
          {admin ? (
            <button className="btn btn--crimson" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={go}><Icon d={I.upload} />{busy ? "Preparando…" : "Exportar todos mis datos"}</button>
          ) : <p className="meta" style={{ margin: 0 }}>Solo el administrador de la empresa puede exportar todos los datos.</p>}
          <p className="meta" style={{ margin: 0 }}>Guardá el archivo en un lugar seguro: contiene datos personales de clientes y colaboradores.</p>
        </div>
      </Card>
      <Card title="Respaldo de la plataforma">
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13, lineHeight: 1.6 }} className="muted">
          <span>El respaldo completo (para recuperar el sistema ante una falla) lo corre el equipo técnico con <code>python -m app.tools.backup</code> y se guarda fuera del proveedor de la nube.</span>
          <span>La recuperación se prueba restaurando en una base vacía con <code>python -m app.tools.restore</code>, que compara conteos y huellas con el respaldo.</span>
          <span>El procedimiento completo está en <code>infra/RESPALDO.md</code>.</span>
        </div>
      </Card>
    </div>
  );
}
