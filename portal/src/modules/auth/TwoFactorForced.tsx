/* Pantalla de configuración forzada (plan 1.10): el rol de la persona exige verificación en dos pasos y venció el
   plazo de gracia. La API ya rechaza el panel interno (403 + X-2FA-Setup); aquí solo puede activarla o salir. */
import { useState } from "react";
import { parseTs } from "../../lib/api";
import { useSession } from "../../app/session";
import { roleMeta } from "../../app/roles";
import { Card, I, Icon } from "../../ui/components";
import TwoFactorSetup, { RecoveryCodes } from "./TwoFactorSetup";

export default function TwoFactorForced() {
  const { me, reload, logout } = useSession();
  const [codes, setCodes] = useState<string[] | null>(null);
  const venc = me?.twofa?.deadline ? parseTs(me.twofa.deadline).toLocaleDateString("es-CR", { day: "numeric", month: "long" }) : null;
  return (
    <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: "24px 16px", background: "var(--bg)" }}>
      <div style={{ width: "min(560px, 100%)", display: "flex", flexDirection: "column", gap: 14 }}>
        <img src="/logo.png" alt="Crimson Consulting" style={{ height: 34, alignSelf: "flex-start" }} />
        <Card title={codes ? "Verificación en dos pasos activada" : "Activá la verificación en dos pasos"}>
          {codes ? (
            <RecoveryCodes codes={codes} email={me?.user.email} onDone={() => { void reload(); }} />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <p style={{ fontSize: 14, margin: 0 }}>
                <Icon d="M6 11V8a6 6 0 0 1 12 0v3|M5 11h14v10H5z|M12 15v3" size={16} /> Tu rol ({roleMeta(me?.role || "").label}) maneja datos y dinero de {me?.tenant.name}: la empresa exige
                verificación en dos pasos{venc ? ` y el plazo para activarla venció el ${venc}` : ""}. Activala para seguir usando el panel; toma un minuto.
              </p>
              <TwoFactorSetup onDone={setCodes} />
            </div>
          )}
        </Card>
        <button className="btn btn--ghost btn--sm" style={{ alignSelf: "flex-start" }} onClick={() => { void logout(); }}><Icon d={I.logout} />Cerrar sesión</button>
      </div>
    </div>
  );
}
