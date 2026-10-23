/* Plantilla v2 del levantamiento (documento de Andres §4 y curso Hikvision):
   - estado por campo del punto (confirmado / estimado / no aplica / pendiente), chico y comodo en el celular;
   - campos del punto agrupados por seccion, el campo viejo solo si ya trae dato y "Funciones avanzadas" plegadas;
   - seccion comun del levantamiento (quien recibio, objetivo, energia, red...) y bloques de sistema;
   - tarjeta "Calculos del sistema" con la formula de cada numero. Todo sale de la API: aqui no hay reglas. */
import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../lib/api";
import { Card } from "../../ui/components";
import "../../ui/survey-v2.css";

export type DataStatus = "confirmado" | "estimado" | "no_aplica" | "pendiente";
export type V2Field = {
  key: string; label: string; type: "text" | "number" | "select" | "multi" | "bool"; options?: string[]; unit?: string; placeholder?: string;
  hint?: string; section?: string; advanced?: boolean; legacy?: boolean; default?: number;
};
type Block = { label: string; kinds: string[]; fields: V2Field[] };
export type CommonSpec = { template_version: number; data_status: DataStatus[]; fields: V2Field[]; blocks: Record<string, Block> };
export type Common = Record<string, unknown> & { acceso_sistema?: Record<string, unknown>; cctv_sistema?: Record<string, unknown>; parametros?: Record<string, unknown> };

const STATUS_LABEL: Record<DataStatus, string> = { confirmado: "✓ Confirmado", estimado: "≈ Estimado", no_aplica: "No aplica", pendiente: "? Pendiente" };
const STATUS_ORDER: DataStatus[] = ["confirmado", "estimado", "pendiente", "no_aplica"];

/* Estado inicial de un punto nuevo: todos sus campos confirmados. Con estados, un campo vacio o pendiente nunca se
   toma como 0 en la sugerencia (queda "revisar: falta ..."). Los puntos viejos no traen estados y calculan como siempre. */
export const initialStatus = (fields: V2Field[] = []): Record<string, DataStatus> =>
  Object.fromEntries(fields.filter((f) => !f.legacy).map((f) => [f.key, "confirmado" as DataStatus]));

export function StatusPicker({ value, onChange, disabled }: { value: DataStatus; onChange: (v: DataStatus) => void; disabled?: boolean }) {
  return (
    <select className={`dstatus dstatus--${value}`} value={value} disabled={disabled} aria-label="Estado del dato" title="Estado del dato"
      onChange={(e) => onChange(e.target.value as DataStatus)}>
      {STATUS_ORDER.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
    </select>
  );
}

const vacio = (v: unknown) => v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);

/* Campos de un punto. render dibuja el input (lo pone Surveys, que ya sabe de decimales y chips). */
export function PointFields({ fields, data, status, onStatus, readOnly, render }: {
  fields: V2Field[]; data: Record<string, unknown>; status?: Record<string, string> | null;
  onStatus: (key: string, v: DataStatus) => void; readOnly?: boolean; render: (f: V2Field) => ReactNode;
}) {
  const visible = fields.filter((f) => !f.legacy || !vacio(data[f.key]));
  const normal = visible.filter((f) => !f.advanced);
  const avanzadas = visible.filter((f) => f.advanced);
  const secciones: [string, V2Field[]][] = [];
  for (const f of normal) {
    const s = f.section || "";
    const g = secciones.find(([k]) => k === s);
    if (g) g[1].push(f); else secciones.push([s, [f]]);
  }
  const one = (f: V2Field) => {
    const st = ((status || {})[f.key] as DataStatus) || "confirmado";
    return (
      <div className={`field pf pf--${st}`} key={f.key}>
        <div className="pf__head">
          <label>{f.label}{f.legacy ? " (dato anterior)" : ""}</label>
          {readOnly ? (st !== "confirmado" && <span className={`dstatus-tag dstatus--${st}`}>{STATUS_LABEL[st]}</span>) : <StatusPicker value={st} onChange={(v) => onStatus(f.key, v)} />}
        </div>
        {render(f)}
        {f.hint && <small>{f.hint}</small>}
      </div>
    );
  };
  const avanzadasActivas = avanzadas.filter((f) => !vacio(data[f.key]) && data[f.key] !== false).length;
  return (
    <>
      {secciones.map(([s, fs]) => (
        <div key={s || "_"} className="pf-sec">
          {s && secciones.length > 1 && <div className="pf-sec__title">{s}</div>}
          <div className="point__grid">{fs.map(one)}</div>
        </div>
      ))}
      {avanzadas.length > 0 && (
        <details className="pf-adv" open={avanzadasActivas > 0}>
          <summary>Funciones avanzadas{avanzadasActivas ? ` · ${avanzadasActivas} activa${avanzadasActivas !== 1 ? "s" : ""}` : ""}</summary>
          <div className="point__grid">{avanzadas.map(one)}</div>
        </details>
      )}
    </>
  );
}

export function useCommonSpec() {
  const [spec, setSpec] = useState<CommonSpec | null>(null);
  useEffect(() => { api<CommonSpec>("/field/specs/common").then(setSpec).catch(() => setSpec(null)); }, []);
  return spec;
}

function SimpleInput({ f, value, onChange, disabled }: { f: V2Field; value: unknown; onChange: (v: unknown) => void; disabled?: boolean }) {
  if (f.type === "bool") return <label className="pf-bool"><input type="checkbox" disabled={disabled} checked={value === true} onChange={(e) => onChange(e.target.checked)} />Sí</label>;
  if (f.type === "select") return <select className="select" disabled={disabled} value={String(value ?? "")} onChange={(e) => onChange(e.target.value || null)}><option value="">—</option>{f.options?.map((o) => <option key={o}>{o}</option>)}</select>;
  if (f.type === "number") {
    const ph = f.default !== undefined ? `${f.default}${f.unit ? ` ${f.unit}` : ""} (por defecto)` : f.unit ? `0 ${f.unit}` : undefined;
    return <input className="input input--mono" inputMode="decimal" disabled={disabled} placeholder={ph} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
  }
  return <input className="input" disabled={disabled} placeholder={f.placeholder} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
}

/* Seccion comun del levantamiento: plegada para no estorbar en el celular. Los bloques de sistema aparecen segun
   los tipos presentes (acceso, CCTV); los parametros de calculo siempre. */
export function CommonSection({ spec, value, onChange, receivedBy, onReceivedBy, kinds, readOnly }: {
  spec: CommonSpec | null; value: Common | null; onChange: (c: Common) => void; receivedBy: string; onReceivedBy: (v: string) => void;
  kinds: string[]; readOnly?: boolean;
}) {
  if (!spec) return null;
  const c: Common = value || {};
  const set = (k: string, v: unknown) => onChange({ ...c, [k]: v });
  const setB = (b: string, k: string, v: unknown) => onChange({ ...c, [b]: { ...((c[b] as Record<string, unknown>) || {}), [k]: v } });
  const llenos = spec.fields.filter((f) => !vacio(c[f.key])).length + (receivedBy ? 1 : 0);
  const bloques = Object.entries(spec.blocks).filter(([, b]) => !b.kinds.length || b.kinds.some((k) => kinds.includes(k)));
  return (
    <details className="common card">
      <summary>
        <span className="h3">Datos generales del sitio</span>
        <span className="muted common__count">{llenos ? `${llenos} de ${spec.fields.length + 1} anotados` : "Objetivo, energía, red, horarios, permisos…"}</span>
      </summary>
      <div className="common__body">
        <div className="field"><label>Quién recibió la visita</label><input className="input" disabled={readOnly} value={receivedBy} placeholder="Nombre y puesto" onChange={(e) => onReceivedBy(e.target.value)} /></div>
        <div className="common__grid">
          {spec.fields.map((f) => (
            <div className="field" key={f.key}><label>{f.label}</label>
              <textarea className="textarea common__text" rows={1} disabled={readOnly} placeholder={f.placeholder} value={String(c[f.key] ?? "")} onChange={(e) => set(f.key, e.target.value)} />
            </div>
          ))}
        </div>
        {bloques.map(([bk, b]) => {
          const bv = (c[bk] as Record<string, unknown>) || {};
          return (
            <div className="common__block" key={bk}>
              <div className="pf-sec__title">{b.label}</div>
              <div className="point__grid">
                {b.fields.map((f) => (
                  <div className="field" key={f.key}><label>{f.label}</label>
                    <SimpleInput f={f} value={bv[f.key]} disabled={readOnly} onChange={(v) => setB(bk, f.key, v)} />
                    {f.hint && <small>{f.hint}</small>}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </details>
  );
}

/* ---------- Calculos del sistema ---------- */
type Alt = { label: string; capacidad: number; reserva: number; controladoras: number; actual: boolean };
type Cred = { total: number; paquete: number | null; paquetes: number | null; formula: string; faltan: string[] };
type Dep = { point_code: string; text: string; missing: string[] };
type Sys = {
  template_version: number; survey_template_version: number; parametros: { desperdicio_cable_pct: number; desperdicio_default: number };
  acceso: null | { puertas: number; lectores: number; formula_lectores: string; botones: number; controladoras: { actual: Alt | null; alternativas: Alt[] }; credenciales: Cred | null };
  cctv: null | {
    camaras: number; canales: number | null; canales_sugeridos: number; modo: string | null;
    almacenamiento: { tb: number | null; detalle: string | null; formula: string; faltan: string[]; leyenda: string; completo: boolean };
    entrada_nvr: { requerido_mbps: number; disponible_mbps: number | null; ok: boolean | null };
    poe: { camaras: number; consumo_w: number; reserva_pct: number; requerido_w: number; switch_w: number | null; ok: boolean | null; sin_consumo: string[]; formula: string };
  };
  advertencias: string[]; dependencias: Dep[];
};

const n3 = (v: number | string | null | undefined, d = 3) => (v === null || v === undefined ? "—" : Number(v).toLocaleString("es-CR", { maximumFractionDigits: d }));

function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  return <div className={`eco__box${tone === "good" ? " is-good" : tone === "bad" ? " is-bad" : ""}`}><span className="meta">{label}</span><b>{value}</b>{sub && <span className="muted sys__sub">{sub}</span>}</div>;
}

export function SystemCalcs({ surveyId, refresh }: { surveyId?: number; refresh: number }) {
  const [sys, setSys] = useState<Sys | null>(null);
  useEffect(() => {
    if (!surveyId) { setSys(null); return; }
    api<Sys>(`/surveys/${surveyId}/system`).then(setSys).catch(() => setSys(null));
  }, [surveyId, refresh]);
  if (!sys || (!sys.acceso && !sys.cctv)) return null;
  const a = sys.acceso, c = sys.cctv;
  return (
    <Card title="Cálculos del sistema" extra={<span className="meta" title="Versión de la plantilla con que se llenó este levantamiento">Plantilla v{sys.survey_template_version}</span>}>
      <p className="muted sys__hint">Estimaciones a partir de lo guardado, cada una con su fórmula. No cambian la lista de materiales: sirven para revisarla. Guarde para recalcular.</p>
      {sys.dependencias.length > 0 && (
        <div className="sys__alert sys__alert--bad">
          <b>Dependencia técnica pendiente</b>
          <ul>{sys.dependencias.map((d) => <li key={d.point_code + d.text}>{d.text}</li>)}</ul>
        </div>
      )}
      {sys.advertencias.length > 0 && (
        <div className="sys__alert"><b>Para revisar</b><ul>{sys.advertencias.map((t) => <li key={t}>{t}</li>)}</ul></div>
      )}
      {a && (
        <section className="sys__sec">
          <div className="pf-sec__title">Control de acceso</div>
          <div className="eco">
            <Stat label="Puertas" value={a.puertas} />
            <Stat label="Lectores" value={a.lectores} sub={a.formula_lectores} />
            <Stat label="Botones de salida" value={a.botones} sub="solo puertas que salen con botón" />
            <Stat label="Credenciales" value={a.credenciales ? (a.credenciales.paquetes ? `${a.credenciales.paquetes} paq.` : n3(a.credenciales.total, 0)) : "—"}
              sub={a.credenciales ? `${a.credenciales.formula}${a.credenciales.faltan.length ? ` · falta ${a.credenciales.faltan.join(", ").toLowerCase()}` : ""}` : "Anotá usuarios y visitantes en Datos generales del sitio"}
              tone={a.credenciales?.faltan.length ? "bad" : undefined} />
          </div>
          {a.controladoras.alternativas.length > 0 && (
            <div className="sys__alts">
              <span className="meta">Controladoras: opciones para elegir</span>
              <table className="table">
                <thead><tr><th>Combinación</th><th className="num">Controladoras</th><th className="num">Reserva</th><th /></tr></thead>
                <tbody>{a.controladoras.alternativas.map((o) => (
                  <tr key={o.label}><td className="mono">{o.label}</td><td className="num mono">{o.controladoras}</td><td className="num mono">{o.reserva}</td>
                    <td>{o.actual ? <span className="badge badge--info">la que sugiere la lista</span> : o.reserva === 0 ? <span className="badge badge--ok">exacta</span> : null}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {c && (
        <section className="sys__sec">
          <div className="pf-sec__title">CCTV</div>
          <div className="eco">
            <Stat label="Almacenamiento" value={c.almacenamiento.tb !== null ? `${n3(c.almacenamiento.tb)} TB` : "—"}
              sub={<>{c.almacenamiento.detalle || c.almacenamiento.formula}{c.almacenamiento.faltan.length > 0 && <> · falta {c.almacenamiento.faltan.join(", ")}</>}<br /><i>{c.almacenamiento.leyenda}</i></>}
              tone={c.almacenamiento.completo ? undefined : "bad"} />
            <Stat label="Entrada del grabador" value={`${n3(c.entrada_nvr.requerido_mbps, 2)} Mb/s`}
              sub={c.entrada_nvr.disponible_mbps ? `de ${n3(c.entrada_nvr.disponible_mbps, 2)} Mb/s disponibles` : "Anotá el ancho de entrada del grabador"}
              tone={c.entrada_nvr.ok === false ? "bad" : c.entrada_nvr.ok ? "good" : undefined} />
            <Stat label="Presupuesto PoE" value={`${n3(c.poe.requerido_w, 1)} W`}
              sub={<>{n3(c.poe.consumo_w, 1)} W de {c.poe.camaras} cámara(s) + {n3(c.poe.reserva_pct, 1)} % de reserva{c.poe.switch_w ? ` · switch ${n3(c.poe.switch_w, 1)} W` : " · falta el presupuesto del switch"}{c.poe.sin_consumo.length > 0 && <> · sin consumo: {c.poe.sin_consumo.join(", ")}</>}</>}
              tone={c.poe.ok === false ? "bad" : c.poe.ok ? "good" : undefined} />
            <Stat label="Canales" value={c.canales ?? `${c.canales_sugeridos} (sug.)`} sub={`${c.camaras} cámara(s)${c.modo ? ` · grabación ${c.modo.toLowerCase()}` : ""}`}
              tone={c.canales && c.camaras > c.canales ? "bad" : undefined} />
          </div>
        </section>
      )}
      <p className="muted sys__hint">Desperdicio de cable y tubo: <b>{n3(sys.parametros.desperdicio_cable_pct, 2)} %</b>{Number(sys.parametros.desperdicio_cable_pct) !== Number(sys.parametros.desperdicio_default) ? ` (por defecto ${n3(sys.parametros.desperdicio_default, 2)} %)` : " (por defecto)"}. Se cambia en Datos generales del sitio › Parámetros de cálculo.</p>
    </Card>
  );
}
