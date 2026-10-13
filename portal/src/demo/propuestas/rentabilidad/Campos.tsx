/* Campos de los supuestos: número editable con insignia, error accesible y «¿Por qué este ejemplo?».
   Las cifras cambian al instante con cada tecla válida (sin animación); lo inválido nunca entra al modelo. */
import { useEffect, useId, useRef, useState, type Dispatch, type ReactNode } from "react";
import { cerca, crc, enPantalla, leerCampo, numero, textoCampo } from "./cifras";
import { type Accion, type Estado, TU_DATO } from "./estado";
import { BASE_EJEMPLO, ESCENARIO_EJEMPLO, ESCENARIOS, INICIO_EJEMPLO, type ClaveBase, type EscenarioId, type Resultado } from "./modelo";
import { CAMPOS_BASE, CAMPOS_ESCENARIO, ESCENARIO_INFO, ETAPAS_RNT, GRUPOS, type Campo, type Grupo, type TipoInsignia, type Unidad } from "./supuestos";

const INSIGNIA: Record<TipoInsignia | "editado", string> = { ejemplo: "Ejemplo", crimson: "Dato de Crimson", "tu-dato": "Tu dato", editado: "Editado" };

export function Insignia(p: { tipo: TipoInsignia | "editado" }) {
  return <span className="rnt-insignia" data-tipo={p.tipo}>{INSIGNIA[p.tipo]}</span>;
}

const SUFIJO: Record<Unidad, string> = { colones: "", porcentaje: "%", horas: "h", cantidad: "", meses: "meses", mes: "" };
const RESUMEN_PORQUE: Record<TipoInsignia, string> = { ejemplo: "¿Por qué este ejemplo?", crimson: "¿De dónde sale este dato?", "tu-dato": "¿Qué va aquí?" };

const textoRango = (c: Pick<Campo<string>, "min" | "max" | "unidad">) =>
  c.unidad === "colones" ? `entre ₡${numero(c.min, 0)} y ₡${numero(c.max, 0)}` : `entre ${numero(c.min, 0)} y ${numero(c.max, 0)}`;

export type DatosCampo = Pick<Campo<string>, "rotulo" | "unidad" | "insignia" | "porque" | "min" | "max">;

export function CampoNumero(p: {
  id: string; campo: DatosCampo; valor: number | null; ejemplo: number | null; onCambio: (v: number | null) => void;
  compacto?: boolean; rotuloAccesible?: string; nota?: string; entero?: boolean;
}) {
  const { unidad, insignia } = p.campo;
  const opcional = insignia === "tu-dato";
  const editado = opcional ? p.valor !== null : !cerca(p.valor, p.ejemplo);
  const ref = useRef<HTMLInputElement>(null);
  const ultimo = useRef(p.valor);
  const [texto, setTexto] = useState(() => textoCampo(p.valor, unidad));
  const [error, setError] = useState<string | null>(null);
  const base = useId();
  const errId = `${base}-e`, notaId = `${base}-n`;

  // un cambio que viene de afuera (Volver a los ejemplos, el plan de B2) se refleja si el campo no se está editando
  useEffect(() => {
    if (cerca(ultimo.current, p.valor)) return;
    ultimo.current = p.valor;
    if (document.activeElement !== ref.current) { setTexto(textoCampo(p.valor, unidad)); setError(null); }
  }, [p.valor, unidad]);

  const cambiar = (t: string) => {
    setTexto(t);
    const l = leerCampo(t, unidad);
    if (!l.ok) { setError(`Escribí solo un número, ${textoRango(p.campo)}.`); return; }
    if (l.v === null) {
      if (opcional) { setError(null); ultimo.current = null; p.onCambio(null); }
      else setError("Este supuesto necesita un número. Para volver al ejemplo, tocá «Volver al ejemplo».");
      return;
    }
    const vp = enPantalla(l.v, unidad);
    if (vp < p.campo.min || vp > p.campo.max) { setError(`Escribí un número ${textoRango(p.campo)}.`); return; }
    if (p.entero && !Number.isInteger(l.v)) { setError("Escribí un mes entero (1, 2, 3…)."); return; }
    setError(null);
    ultimo.current = l.v;
    p.onCambio(l.v);
  };
  const restablecer = () => {
    const v = opcional ? null : p.ejemplo;
    ultimo.current = v;
    setTexto(textoCampo(v, unidad));
    setError(null);
    p.onCambio(v);
  };
  const ejemploTxt = p.ejemplo === null ? "" : `${unidad === "colones" ? "₡" : ""}${textoCampo(p.ejemplo, unidad)}${SUFIJO[unidad] ? ` ${SUFIJO[unidad]}` : ""}`;
  const describe = [error ? errId : "", p.nota ? notaId : ""].filter(Boolean).join(" ") || undefined;

  return (
    <div className="rnt-campo" data-compacto={p.compacto || undefined} data-editado={editado || undefined}>
      <div className="rnt-campo__cab">
        <label htmlFor={p.id} className={p.compacto ? "rnt-sr" : "rnt-campo__rot"}>{p.rotuloAccesible ?? p.campo.rotulo}</label>
        {!p.compacto && <Insignia tipo={editado ? "editado" : insignia} />}
      </div>
      <div className="rnt-campo__caja" data-unidad={unidad}>
        {unidad === "colones" && <span className="rnt-campo__u" aria-hidden="true">₡</span>}
        {unidad === "mes" && <span className="rnt-campo__u" aria-hidden="true">Mes</span>}
        <input
          ref={ref} id={p.id} className="rnt-campo__input" type="text" inputMode="decimal" autoComplete="off" spellCheck={false}
          value={texto} placeholder={opcional ? "Sin dato" : undefined}
          aria-invalid={error ? true : undefined} aria-describedby={describe}
          onChange={(e) => cambiar(e.target.value)}
          onBlur={() => { if (!error) setTexto(textoCampo(ultimo.current, unidad)); }}
        />
        {SUFIJO[unidad] && <span className="rnt-campo__u" aria-hidden="true">{SUFIJO[unidad]}</span>}
      </div>
      {error && <p id={errId} className="rnt-campo__error"><span aria-hidden="true">⚠ </span>{error}</p>}
      {p.nota && <p id={notaId} className="rnt-campo__nota">{p.nota}</p>}
      {p.compacto ? (
        editado && (
          <p className="rnt-campo__mini">
            <Insignia tipo="editado" />
            <button type="button" className="rnt-ico" onClick={restablecer} aria-label={`Volver al ejemplo: ${ejemploTxt}`} title={`Volver al ejemplo: ${ejemploTxt}`}>↺</button>
          </p>
        )
      ) : (
        <div className="rnt-campo__pie">
          {editado && <button type="button" className="rnt-link" onClick={restablecer}>{opcional ? "Borrar mi dato" : `Volver al ejemplo (${ejemploTxt})`}</button>}
          <details className="rnt-porque">
            <summary>{RESUMEN_PORQUE[insignia]}</summary>
            <p>{p.campo.porque}</p>
          </details>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------- */

export type GrupoId = Grupo["id"];

/** Qué grupo contiene cada campo (para «Cambiar este supuesto» desde «Cómo se calcula»). */
export function grupoDe(clave: string): GrupoId {
  if (clave.startsWith("inv-")) return "costos";
  if (clave.startsWith("inicio-")) return "calendario";
  if (CAMPOS_ESCENARIO.some((c) => c.clave === clave)) return "escenario";
  return GRUPOS.find((g) => (g.claves as string[]).includes(clave))?.id ?? "tienda";
}
/** id del input de un campo (los de escenario llevan el escenario elegido). */
export const idCampo = (clave: string, escenario: EscenarioId) =>
  CAMPOS_ESCENARIO.some((c) => c.clave === clave) ? `rnt-campo-${clave}-${escenario}` : `rnt-campo-${clave}`;

export function Supuestos(p: {
  s: Estado; d: Dispatch<Accion>; escritorio: boolean; abiertos: Record<GrupoId, boolean>; abrir: (g: GrupoId, v: boolean) => void;
  planB2: number | null; resultado: Resultado;
}) {
  const { s, d } = p;
  const nombre = ESCENARIO_INFO[s.escenario].nombre;
  const campoBase = (k: ClaveBase) => (
    <CampoNumero key={k} id={`rnt-campo-${k}`} campo={CAMPOS_BASE[k]} valor={s.base[k]} ejemplo={TU_DATO.includes(k) ? null : (BASE_EJEMPLO[k] as number)}
      onCambio={(v) => d({ t: "base", k, v })} />
  );
  const grupo = (g: Grupo, cuerpo: ReactNode) => (
    <details key={g.id} className="rnt-grupo" open={p.abiertos[g.id]} onToggle={(e) => p.abrir(g.id, e.currentTarget.open)} id={`rnt-grupo-${g.id}`}>
      <summary className="rnt-grupo__sum">
        <span className="rnt-grupo__t">{g.titulo}</span>
        <span className="rnt-grupo__b">{g.bajada}</span>
      </summary>
      <div className="rnt-grupo__cuerpo">{cuerpo}</div>
    </details>
  );

  return (
    <div className="rnt-grupos">
      {GRUPOS.map((g) => {
        if (g.id === "escenario") {
          return grupo(g, p.escritorio ? (
            <div className="rnt-tabla-region" role="region" tabIndex={0} aria-label="Supuestos por escenario">
              <table className="rnt-esc">
                <caption className="rnt-sr">Supuestos que cambian por escenario. El escenario elegido es {nombre}.</caption>
                <thead>
                  <tr>
                    <th scope="col">Supuesto</th>
                    {ESCENARIOS.map((e) => (
                      <th key={e} scope="col" data-on={e === s.escenario || undefined}>
                        {ESCENARIO_INFO[e].nombre}{e === s.escenario && <span className="rnt-esc__elegido"> · elegido</span>}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {CAMPOS_ESCENARIO.map((c) => (
                    <tr key={c.clave}>
                      <th scope="row">
                        <span className="rnt-esc__rot">{c.rotulo}</span>
                        <span className="rnt-esc__meta"><Insignia tipo="ejemplo" /></span>
                        <details className="rnt-porque"><summary>¿Por qué este ejemplo?</summary><p>{c.porque}</p></details>
                      </th>
                      {ESCENARIOS.map((e) => (
                        <td key={e} data-on={e === s.escenario || undefined}>
                          <CampoNumero compacto id={`rnt-campo-${c.clave}-${e}`} campo={c} rotuloAccesible={`${c.rotulo} · escenario ${ESCENARIO_INFO[e].nombre.toLowerCase()}`}
                            valor={s.ajustes[e]?.[c.clave] ?? ESCENARIO_EJEMPLO[e][c.clave]} ejemplo={ESCENARIO_EJEMPLO[e][c.clave]}
                            onCambio={(v) => { if (v !== null) d({ t: "ajuste", e, k: c.clave, v }); }} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <>
              <p className="rnt-grupo__aviso">Estás editando el escenario <b>{nombre}</b>. Cambiá el escenario arriba para editar los otros; cada uno guarda sus cambios.</p>
              <div className="rnt-campos">
                {CAMPOS_ESCENARIO.map((c) => (
                  <CampoNumero key={`${c.clave}-${s.escenario}`} id={`rnt-campo-${c.clave}-${s.escenario}`} campo={c}
                    valor={s.ajustes[s.escenario]?.[c.clave] ?? ESCENARIO_EJEMPLO[s.escenario][c.clave]} ejemplo={ESCENARIO_EJEMPLO[s.escenario][c.clave]}
                    onCambio={(v) => { if (v !== null) d({ t: "ajuste", e: s.escenario, k: c.clave, v }); }} />
                ))}
              </div>
            </>
          ));
        }
        if (g.id === "precios") {
          return grupo(g, (
            <div className="rnt-campos">
              {g.claves.map(campoBase)}
              {p.planB2 !== null && !cerca(p.planB2, s.base.c) && (
                <div className="rnt-plan-b2">
                  <p>Diseñaste un plan en la maqueta B2 con una cuota de <b>{crc(p.planB2)}</b> sin IVA.</p>
                  <button type="button" className="rnt-btn rnt-btn--fantasma rnt-btn--sm" onClick={() => d({ t: "base", k: "c", v: p.planB2 })}>
                    Usar {crc(p.planB2)} del plan que diseñaste en B2
                  </button>
                </div>
              )}
            </div>
          ));
        }
        if (g.id === "costos") {
          return grupo(g, (
            <>
              <div className="rnt-campos">{g.claves.map(campoBase)}</div>
              <h3 className="rnt-grupo__h3">Inversión por etapa</h3>
              <p className="rnt-grupo__aviso">La define la propuesta de JC Analytics. No sugerimos montos: vacío = sin dato.</p>
              <div className="rnt-campos">
                {ETAPAS_RNT.map((et) => (
                  <CampoNumero key={et.n} id={`rnt-campo-inv-${et.n}`} valor={s.inversion[et.n]} ejemplo={null}
                    campo={{ rotulo: `Inversión de la etapa ${et.n} · ${et.corto}`, unidad: "colones", insignia: "tu-dato", min: 0, max: 100_000_000_000,
                      porque: "Lo define la propuesta de JC Analytics. No sugerimos montos." }}
                    nota={et.n > 0 && !s.incluida[et.n] ? "Etapa apagada en el calendario: esta inversión no se cuenta." : undefined}
                    onCambio={(v) => d({ t: "inversion", s: et.n, v })} />
                ))}
              </div>
              <p className="rnt-grupo__total">Inversión de las etapas incluidas: <b>{p.resultado.inversion === null ? "sin dato" : crc(p.resultado.inversion)}</b></p>
            </>
          ));
        }
        if (g.id === "calendario") {
          return grupo(g, (
            <ol className="rnt-cal">
              {ETAPAS_RNT.map((et) => (
                <li key={et.n} className="rnt-cal__fila" data-apagada={et.n > 0 && !s.incluida[et.n] ? "" : undefined}>
                  <p className="rnt-cal__t"><span className="rnt-cal__n">Etapa {et.n}</span>{et.corto}{!et.conCifra && <span className="rnt-cal__sin"> · sin cifra propia</span>}</p>
                  <CampoNumero id={`rnt-campo-inicio-${et.n}`} valor={s.inicio[et.n]} ejemplo={INICIO_EJEMPLO[et.n]} entero
                    campo={{ rotulo: "Mes de arranque", unidad: "mes", insignia: "ejemplo", min: 1, max: 36, porque: "Calendario de ejemplo; el real lo fija la propuesta." }}
                    onCambio={(v) => { if (v !== null) d({ t: "inicio", s: et.n, v }); }} />
                  {et.n === 0 ? (
                    <p className="rnt-cal__siempre">Siempre incluida: es la tienda que ya viste funcionando.</p>
                  ) : (
                    <label className="rnt-switch">
                      <input type="checkbox" role="switch" checked={s.incluida[et.n]} onChange={(e) => d({ t: "incluida", s: et.n, v: e.target.checked })} />
                      <span className="rnt-switch__pista" aria-hidden="true" />
                      <span>Incluir la etapa {et.n}</span>
                    </label>
                  )}
                </li>
              ))}
            </ol>
          ));
        }
        return grupo(g, <div className="rnt-campos">{g.claves.map(campoBase)}</div>);
      })}
    </div>
  );
}
