/* Cierre «Aceptar por etapas» (6.8): seis tarjetas en línea de tiempo, sin cifras inventadas. */
import { useState } from "react";
import { Link } from "react-router-dom";
import { almacen, CLAVES, SelloPropuesta, useInteres } from "./comun";
import { crc, crcSigno, pct } from "./cifras";
import { paramDe, type Estado } from "./estado";
import type { Resultado } from "./modelo";
import { ENFOQUES, ESCENARIO_INFO, ETAPAS_RNT, PROPUESTAS_RNT } from "./supuestos";

export function AceptarPorEtapas(p: { s: Estado; r: Resultado; enfoque: string | null; irACampo: (clave: string) => void }) {
  const { s, r } = p;
  const interes = useInteres();
  const esc = ESCENARIO_INFO[s.escenario].nombre.toLowerCase();
  // Etapa 0 (la tienda propia): no tiene propuestas, así que su «Me interesa» se guarda aparte para el resumen
  const [etapa0, setEtapa0] = useState(() => almacen.local.get(CLAVES.etapa0) === "1");
  const alternarEtapa0 = () => { const v = !etapa0; setEtapa0(v); almacen.local.set(CLAVES.etapa0, v ? "1" : "0"); };
  // qué cuenta de verdad la Etapa 0 con los datos de hoy (no se promete lo que está apagado o vacío)
  const conFygaro = s.base.Fm !== null || s.base.cF !== null;
  const modela0 = `${!s.sumarHoras && !conFygaro ? "Hoy solo cuenta" : "Cuenta"} el aumento de ventas supuesto (${pct(paramDe(s, s.escenario).u)})`
    + `${s.sumarHoras ? "; también las horas liberadas (las sumaste)" : "; las horas no se suman"}`
    + `${conFygaro ? " y lo que se deja de pagar a Fygaro." : " y Fygaro no tiene dato."}`;
  return (
    <section id="rnt-etapas" className="rnt-cierre" aria-labelledby="rnt-etapas-t">
      <div className="rnt-wrap">
        <p className="rnt-ceja">Cierre</p>
        <h2 id="rnt-etapas-t" className="rnt-h2">Aceptar por etapas</h2>
        <p className="rnt-bajada">No hace falta decidir todo junto. La tienda propia ya está construida; cada etapa se aprueba, se construye y se mide con datos reales antes de la siguiente.</p>

        <ol className="rnt-linea">
          {ETAPAS_RNT.map((et) => {
            const foco = p.enfoque && ENFOQUES[p.enfoque]?.destino === `rnt-etapa-${et.n}` ? ENFOQUES[p.enfoque] : null;
            const todas = et.ids.length > 0 && et.ids.every((id) => interes.tiene(id));
            const apagada = et.n > 0 && !s.incluida[et.n];
            const inv = s.inversion[et.n];
            return (
              <li key={et.n} id={`rnt-etapa-${et.n}`} className="rnt-etapa" tabIndex={-1} data-enfocado={foco ? "" : undefined}
                data-interes={(et.n === 0 ? etapa0 : todas) || undefined} data-construida={et.n === 0 || undefined} aria-labelledby={`rnt-etapa-${et.n}-t`}>
                {foco && <p className="rnt-enfoque"><span aria-hidden="true">◆ </span>Resaltado: {foco.texto}</p>}
                <p className="rnt-etapa__ceja">
                  <span className="rnt-etapa__n">Etapa {et.n}</span>
                  <span>Arranca el mes {s.inicio[et.n]} (ejemplo)</span>
                </p>
                <h3 id={`rnt-etapa-${et.n}-t`} className="rnt-etapa__t">{et.titulo}</h3>
                {et.n === 0 ? (
                  <p className="rnt-etapa__sello"><SelloPropuesta estado="existente" tono="oscuro" /></p>
                ) : (
                  <ul className="rnt-etapa__props" aria-label={`Propuestas de la etapa ${et.n}`}>
                    {et.ids.map((id) => {
                      const pr = PROPUESTAS_RNT[id];
                      return (
                        <li key={id}>
                          <SelloPropuesta id={id} tamano="chico" tono="oscuro" />
                          {pr.maqueta ? <Link to={pr.maqueta} className="rnt-etapa__prop">{pr.corto}<span className="rnt-sr">: ver la maqueta</span> <span aria-hidden="true">→</span></Link>
                            : <span className="rnt-etapa__prop">{pr.corto}</span>}
                        </li>
                      );
                    })}
                  </ul>
                )}
                <p className="rnt-etapa__modela"><b>Qué cuenta en el modelo:</b> {et.n === 0 ? modela0 : et.modela}{et.nota ? ` ${et.nota}` : ""}</p>
                <dl className="rnt-etapa__datos">
                  <div>
                    <dt>Aporte a la utilidad acumulada de 24 meses ({esc}, supuestos de ejemplo)</dt>
                    <dd>{et.n <= 3 ? <b>{crcSigno(r.aportes[et.n as 0 | 1 | 2 | 3])}</b> : <span className="rnt-etapa__sin">Sin cifra propia</span>}</dd>
                  </div>
                  <div>
                    <dt>Inversión</dt>
                    <dd>{inv === null || inv <= 0 ? <button type="button" className="rnt-link" onClick={() => p.irACampo(`inv-${et.n}`)}>Sin dato · escribirla</button> : <b>{crc(inv)}</b>}</dd>
                  </div>
                  {et.depende && <div><dt>Depende de</dt><dd>{et.depende}</dd></div>}
                </dl>
                {apagada && <p className="rnt-etapa__off">Apagada en el modelo: no suma ni cuenta su inversión. <button type="button" className="rnt-link" onClick={() => p.irACampo(`inicio-${et.n}`)}>Cambiarlo en el calendario</button></p>}
                {et.n === 0 ? (
                  <button type="button" className="rnt-interes" aria-pressed={etapa0} onClick={alternarEtapa0}>
                    <span className="rnt-interes__caja" aria-hidden="true">{etapa0 ? "✓" : ""}</span>
                    {etapa0 ? "Te interesa esta etapa" : "Me interesa esta etapa"}
                  </button>
                ) : et.ids.length > 0 && (
                  <button type="button" className="rnt-interes" aria-pressed={todas} onClick={() => (todas ? interes.quitar(et.ids) : interes.agregar(et.ids))}>
                    <span className="rnt-interes__caja" aria-hidden="true">{todas ? "✓" : ""}</span>
                    {todas ? "Te interesa esta etapa" : "Me interesa esta etapa"}
                  </button>
                )}
              </li>
            );
          })}
        </ol>

        <div className="rnt-sugerencia">
          <p className="rnt-ceja">Sugerencia de JC Analytics</p>
          <p>Empezar por la Etapa 1. Es la base de las demás: deja la cuenta del cliente (B1) sobre la que se apoyan el plan, los recordatorios y las reseñas, y vende instalación desde la ficha (A1).</p>
        </div>

        <div className="rnt-cta">
          <Link to="/propuestas?resumen=1" className="rnt-btn rnt-btn--crimson">Preparar mi resumen <span aria-hidden="true">→</span></Link>
          <p>Esto no es un contrato ni se envía nada: copiás el resumen y se lo mandás a JC Analytics por el medio que prefieras.</p>
        </div>
      </div>
    </section>
  );
}
