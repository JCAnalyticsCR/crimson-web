/* Ajustes → Ventas (ventas etapa 2): el jefe de ventas y quién asigna las «ventas por asignar».
   - Topes del jefe de ventas: aprueba descuentos hasta este % y cotizaciones hasta este monto; lo que pase lo aprueba
     el administrador como siempre.
   - Quién asigna las ventas por asignar: el administrador siempre; el jefe de ventas si se marca.
   Se guarda dentro de tenant.settings.ventas (se manda el objeto completo para no borrar los otros ajustes de ventas).
   Cada cambio queda en la bitácora. Una columna en tablet (768 px). */
import { useEffect, useState } from "react";
import { api, fmtMoney } from "../../lib/api";
import { useSession } from "../../app/session";
import { Card, Field, Loading } from "../../ui/components";
import { KitsAdmin, TemplatesAdmin } from "./VentasCatalogos";

type Ventas = Record<string, unknown> & { jefe_ventas_tope_descuento_pct?: number; jefe_ventas_tope_monto?: number; quienes_asignan_ventas?: string[] };
const DEF = { jefe_ventas_tope_descuento_pct: 15, jefe_ventas_tope_monto: 10_000_000, quienes_asignan_ventas: ["admin"], meta_mensual_default: 0, resumen_diario_vendedores: true, aceptacion_en_linea: true, enlace_cotizacion_dias: 30 };

export default function VentasTab() {
  const { allows, toast } = useSession();
  const edita = allows("settings.configurar");
  const [v, setV] = useState<Ventas | null>(null);
  const [pct, setPct] = useState("");
  const [monto, setMonto] = useState("");
  const [jefeAsigna, setJefeAsigna] = useState(false);
  /* ventas etapa 3 */
  const [metaDef, setMetaDef] = useState("");
  const [resumen, setResumen] = useState(true);
  const [enLinea, setEnLinea] = useState(true);
  const [dias, setDias] = useState("30");
  const [busy, setBusy] = useState(false);

  const apply = (raw: Ventas | undefined) => {
    const x = { ...DEF, ...(raw || {}) } as Ventas;
    setV(raw || {});
    setPct(String(x.jefe_ventas_tope_descuento_pct ?? ""));
    setMonto(String(x.jefe_ventas_tope_monto ?? ""));
    setJefeAsigna((x.quienes_asignan_ventas || []).includes("jefe_ventas"));
    setMetaDef(String(x.meta_mensual_default ?? 0));
    setResumen(x.resumen_diario_vendedores !== false);
    setEnLinea(x.aceptacion_en_linea !== false);
    setDias(String(x.enlace_cotizacion_dias ?? 30));
  };
  useEffect(() => { api<{ ventas?: Ventas }>("/settings").then((c) => apply(c.ventas)).catch((e) => toast(e instanceof Error ? e.message : "Error", "bad")); }, [toast]);

  if (!v) return <Loading />;
  const save = async () => {
    const p = Number(pct), m = Number(monto);
    if (pct.trim() === "" || !(p >= 0) || p > 100) { toast("El tope de descuento debe estar entre 0 y 100 %", "bad"); return; }
    if (monto.trim() === "" || !(m >= 0)) { toast("El tope de monto no puede quedar vacío ni ser negativo", "bad"); return; }
    const md = Number(metaDef || 0), dd = Math.round(Number(dias));
    if (!(md >= 0)) { toast("La meta por defecto no puede ser negativa", "bad"); return; }
    if (!(dd >= 1 && dd <= 120)) { toast("La vigencia del enlace debe estar entre 1 y 120 días", "bad"); return; }
    setBusy(true);
    try {
      const body = { ...v, jefe_ventas_tope_descuento_pct: p, jefe_ventas_tope_monto: m, quienes_asignan_ventas: jefeAsigna ? ["admin", "jefe_ventas"] : ["admin"], meta_mensual_default: md, resumen_diario_vendedores: resumen, aceptacion_en_linea: enLinea, enlace_cotizacion_dias: dd };
      const r = await api<{ ventas?: Ventas }>("/settings", { method: "PUT", json: { ventas: body } });
      apply(r.ventas); toast("Ajustes de ventas guardados");
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 760 }}>
      <Card title="Jefe de ventas">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>El jefe de ventas ve lo que vende todo el equipo (nunca el costo ni el margen) y aprueba las cotizaciones retenidas de su equipo hasta estos topes. Lo que pase de ahí, el margen bajo el mínimo y sus propias cotizaciones los sigue aprobando el administrador.</p>
          <div className="grid-2">
            <Field label="Puede aprobar descuentos hasta (%)" hint="Por defecto 15 %. El tope general de la empresa está en Ajustes → Facturación.">
              <input className="input" type="number" min={0} max={100} step="0.5" inputMode="decimal" value={pct} disabled={!edita} onChange={(e) => setPct(e.target.value)} />
            </Field>
            <Field label="Puede aprobar cotizaciones hasta (₡)" hint={`Por defecto ${fmtMoney(10_000_000, "CRC")}. Monto total de la cotización.`}>
              <input className="input" type="number" min={0} step="1000" inputMode="numeric" value={monto} disabled={!edita} onChange={(e) => setMonto(e.target.value)} />
            </Field>
          </div>
        </div>
      </Card>
      <Card title="Ventas por asignar">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>Quién elige el vendedor de una venta que entró sin vendedor (tienda, caja, recurrencias). El administrador siempre puede.</p>
          <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
            <input type="checkbox" checked={jefeAsigna} disabled={!edita} onChange={(e) => setJefeAsigna(e.target.checked)} style={{ marginTop: 3 }} />
            <span><b>También el jefe de ventas</b><br /><span className="muted" style={{ fontSize: 13 }}>Puede asignar el vendedor o marcar «venta de la casa» en las ventas por asignar.</span></span>
          </label>
        </div>
      </Card>
      <Card title="Metas, agenda y aceptación en línea">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="grid-2">
            <Field label="Meta mensual por defecto (₡)" hint="Para el vendedor que no tiene meta propia. 0 = sin meta. Cada meta se pone en Metas del equipo o en Usuarios.">
              <input className="input" type="number" min={0} step="50000" inputMode="numeric" value={metaDef} disabled={!edita} onChange={(e) => setMetaDef(e.target.value)} />
            </Field>
            <Field label="Vigencia del enlace sin fecha de validez (días)" hint="Con fecha de validez, el enlace vence ese día.">
              <input className="input" type="number" min={1} max={120} step="1" inputMode="numeric" value={dias} disabled={!edita} onChange={(e) => setDias(e.target.value)} />
            </Field>
          </div>
          <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
            <input type="checkbox" checked={enLinea} disabled={!edita} onChange={(e) => setEnLinea(e.target.checked)} style={{ marginTop: 3 }} />
            <span><b>Aceptación en línea</b><br /><span className="muted" style={{ fontSize: 13 }}>La cotización enviada lleva un enlace para que el cliente la vea y la acepte con su nombre y cédula. Aceptarla no la gana: el vendedor recibe la tarea de facturar.</span></span>
          </label>
          <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 14 }}>
            <input type="checkbox" checked={resumen} disabled={!edita} onChange={(e) => setResumen(e.target.checked)} style={{ marginTop: 3 }} />
            <span><b>Resumen de la mañana</b><br /><span className="muted" style={{ fontSize: 13 }}>A las 6:45 a. m. de lunes a sábado, cada vendedor recibe por correo su agenda del día y lo atrasado.</span></span>
          </label>
        </div>
      </Card>
      {edita && <div><button className="btn btn--crimson" onClick={save} disabled={busy}>{busy ? "Guardando…" : "Guardar ajustes de ventas"}</button></div>}
      {edita && <KitsAdmin />}
      {edita && <TemplatesAdmin />}
    </div>
  );
}
