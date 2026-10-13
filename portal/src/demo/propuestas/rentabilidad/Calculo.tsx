/* «Cómo se calcula»: cada fórmula a la vista, con los números del mes 12 del escenario elegido sustituidos.
   Cada fuente dice qué supuestos la mueven y lleva directo a cambiarlos. */
import type { ReactNode } from "react";
import { SelloPropuesta } from "./comun";
import { crc, crcSigno, numero, pct } from "./cifras";
import type { Estado } from "./estado";
import { mes, type ClaveBase, type ClaveEscenario, type ParamEscenario, type Resultado } from "./modelo";
import { CAMPOS_BASE, CAMPO_ESCENARIO, ENFOQUES, ESCENARIO_INFO, FUENTES, type FuenteId } from "./supuestos";

type Props = { s: Estado; r: Resultado; esc: ParamEscenario; enfoque: string | null; irACampo: (clave: string) => void };

const n2 = (x: number) => numero(x, 2);

function Mueven(p: { claves: (ClaveBase | ClaveEscenario)[]; s: Estado; esc: ParamEscenario; irACampo: (c: string) => void }) {
  return (
    <p className="rnt-calc__mueven">
      <span>Lo mueven:</span>
      {p.claves.map((k) => {
        const c = (CAMPOS_BASE as Record<string, (typeof CAMPOS_BASE)[ClaveBase]>)[k] ?? CAMPO_ESCENARIO[k as ClaveEscenario];
        const v = k in p.esc ? p.esc[k as ClaveEscenario] : p.s.base[k as ClaveBase];
        const txt = v === null ? "sin dato" : c.unidad === "porcentaje" ? pct(v) : c.unidad === "colones" ? crc(v) : c.unidad === "horas" ? `${n2(v)} h` : c.unidad === "meses" ? `${n2(v)} meses` : n2(v);
        return (
          <button key={k} type="button" className="rnt-chip-btn" onClick={() => p.irACampo(k)}>
            {c.corto} <b>{txt}</b>
          </button>
        );
      })}
    </p>
  );
}

function Tarjeta(p: { id: string; eyebrow: string; titulo: string; sello?: ReactNode; enfoque: string | null; children: ReactNode }) {
  const foco = p.enfoque && ENFOQUES[p.enfoque]?.destino === p.id ? ENFOQUES[p.enfoque] : null;
  return (
    <article id={p.id} className="rnt-calc" data-enfocado={foco ? "" : undefined} tabIndex={-1} aria-labelledby={`${p.id}-t`}>
      {foco && <p className="rnt-enfoque"><span aria-hidden="true">◆ </span>Resaltado: {foco.texto}</p>}
      <p className="rnt-calc__eyebrow">{p.eyebrow}</p>
      <div className="rnt-calc__cab"><h3 id={`${p.id}-t`}>{p.titulo}</h3>{p.sello}</div>
      {p.children}
    </article>
  );
}

const F = (p: { children: ReactNode }) => <p className="rnt-calc__f"><code>{p.children}</code></p>;
const Sust = (p: { children: ReactNode; total: string }) => (
  <p className="rnt-calc__s">Mes 12: {p.children} = <b>{p.total}</b></p>
);

export function ComoSeCalcula({ s, r, esc, enfoque, irACampo }: Props) {
  const m12 = mes(r, 12), m11 = mes(r, 11);
  const b = s.base;
  const inc = (n: number) => n === 0 || s.incluida[n];
  const suma24 = (k: FuenteId) => r.meses.slice(0, 24).reduce((a, m) => a + m[k], 0);
  const nombreEsc = ESCENARIO_INFO[s.escenario].nombre.toLowerCase();
  const fuente = (id: FuenteId) => FUENTES.find((f) => f.id === id)!;
  const apagada = (n: number) => <p className="rnt-calc__off">La etapa {n} está apagada en el calendario: esta fuente no se suma (₡0).</p>;
  const rTxt = (i: 0 | 1 | 2 | 3) => n2(m12.r[i]);
  const pie = (k: FuenteId) => <p className="rnt-calc__suma">Suma en 24 meses: <b>{crc(suma24(k))}</b></p>;

  return (
    <>
    <details className="rnt-mas rnt-glosario">
      <summary>Qué significa cada letra</summary>
      <dl>
        {GLOSARIO.map(([k, v]) => <div key={k}><dt><code>{k}</code></dt><dd>{v}</dd></div>)}
      </dl>
    </details>
    <div className="rnt-calcs">
      <Tarjeta id="rnt-calc-base" eyebrow="Punto de partida" titulo="Ventas de hoy, pedidos y rampa" enfoque={enfoque}>
        <F>V0 = P0 × T</F>
        <p className="rnt-calc__s">{n2(b.P0)} pedidos × {crc(b.T)} = <b>{crc(r.V0)} al mes</b></p>
        <F>N = P0 × (1 + u × r₀)</F>
        <Sust total={`${n2(m12.N)} pedidos`}>{n2(b.P0)} × (1 + {pct(esc.u)} × {rTxt(0)})</Sust>
        <F>r = mín(1, (mes − arranque + 1) ÷ R)</F>
        <p className="rnt-calc__s">
          Con R = {n2(esc.R)} meses, al mes 12: Etapa 0 = {rTxt(0)} · Etapa 1 = {rTxt(1)} · Etapa 2 = {rTxt(2)} · Etapa 3 = {rTxt(3)}.
          Al arrancar, cada etapa sube parejo hasta rendir completo.
        </p>
        <Mueven claves={["P0", "T", "u", "R"]} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S1" eyebrow="S1 · Etapa 0 · tienda propia" titulo={fuente("S1").nombre} enfoque={enfoque}>
        <F>S1 = V0 × u × r₀ × m</F>
        <Sust total={crc(m12.S1)}>{crc(r.V0)} × {pct(esc.u)} × {rTxt(0)} × {pct(b.m)}</Sust>
        {pie("S1")}
        <Mueven claves={fuente("S1").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S2" eyebrow="S2 · Etapa 1" titulo={fuente("S2").nombre} sello={<SelloPropuesta id="A1" tamano="chico" />} enfoque={enfoque}>
        <F>S2 = N × pc × ti × r₁ × I × mi</F>
        {inc(1) ? <Sust total={crc(m12.S2)}>{n2(m12.N)} × {pct(b.pc)} × {pct(esc.ti)} × {rTxt(1)} × {crc(b.I)} × {pct(b.mi)}</Sust> : apagada(1)}
        {pie("S2")}
        <Mueven claves={fuente("S2").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S5" eyebrow="S5 · Etapa 1" titulo={fuente("S5").nombre} sello={<SelloPropuesta id="B1" tamano="chico" />} enfoque={enfoque}>
        <F>S5 = B(mes anterior) × ra ÷ 12 × Tr × m</F>
        <F>B = compradores con cuenta, acumulados: B(mes) = B(mes anterior) + N × r₁</F>
        {inc(1) ? (
          <>
            <Sust total={crc(m12.S5)}>{numero(m11.B, 1)} compradores × {pct(esc.ra)} ÷ 12 × {crc(b.Tr)} × {pct(b.m)}</Sust>
            <p className="rnt-calc__s">Cada compra deja al cliente con cuenta (B1): la base de quienes pueden volver crece mes a mes.</p>
          </>
        ) : apagada(1)}
        {pie("S5")}
        <Mueven claves={fuente("S5").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S3" eyebrow="S3 · Etapa 2" titulo={fuente("S3").nombre} sello={<SelloPropuesta id="B2" tamano="chico" />} enfoque={enfoque}>
        <F>A(mes) = A(mes anterior) × (1 − b) + N × pc × tp × r₂</F>
        <F>MRR = A × c · S3 = MRR × mp</F>
        {inc(2) ? (
          <>
            <Sust total={`${n2(m12.A)} planes`}>{n2(m11.A)} × (1 − {pct(esc.b)}) + {n2(m12.N)} × {pct(b.pc)} × {pct(esc.tp)} × {rTxt(2)}</Sust>
            <p className="rnt-calc__s">MRR = {n2(m12.A)} × {crc(b.c)} = <b>{crc(m12.MRR)}</b> de ingreso al mes (sin IVA) · S3 = {crc(m12.MRR)} × {pct(b.mp)} = <b>{crc(m12.S3)}</b></p>
            <p className="rnt-calc__s">Cada mes entran planes nuevos y salen las bajas: por eso se acumula.</p>
          </>
        ) : apagada(2)}
        {pie("S3")}
        <Mueven claves={fuente("S3").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S4" eyebrow="S4 · Etapa 3" titulo={fuente("S4").nombre} sello={<SelloPropuesta id="A2" tamano="chico" />} enfoque={enfoque}>
        <F>S4 = K × rr × r₃ × T × m</F>
        {inc(3) ? <Sust total={crc(m12.S4)}>{n2(b.K)} carritos × {pct(esc.rr)} × {rTxt(3)} × {crc(b.T)} × {pct(b.m)}</Sust> : apagada(3)}
        {pie("S4")}
        <Mueven claves={fuente("S4").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S7" eyebrow="S7 · Etapa 0" titulo="Lo que se deja de pagar a Fygaro" enfoque={enfoque}>
        <F>S7 = Fm + cF × V0</F>
        <Sust total={crc(m12.S7)}>{b.Fm === null ? "₡0 (sin dato)" : crc(b.Fm)} + {b.cF === null ? "0 % (sin dato)" : pct(b.cF)} × {crc(r.V0)}</Sust>
        {b.Fm === null && b.cF === null && <p className="rnt-calc__off">Vacío = no se cuenta ese ahorro. Escribí lo que pagás hoy.</p>}
        {pie("S7")}
        <Mueven claves={fuente("S7").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-fuente-S6" eyebrow="S6 · Etapa 0 · aparte" titulo="Horas liberadas" enfoque={enfoque}>
        <F>Horas = Hh × he × r₀ · S6 = Horas × ch</F>
        <Sust total={`${numero(m12.horas, 1)} h ≈ ${crc(m12.S6)}`}>{n2(b.Hh)} h × {pct(esc.he)} × {rTxt(0)} × {crc(b.ch)}</Sust>
        <p className="rnt-calc__s">{s.sumarHoras ? "Se está sumando al resultado (lo encendiste)." : "No se suma al resultado: las horas valen plata solo si ese tiempo se usa en otra cosa."}</p>
        <Mueven claves={fuente("S6").mueven} s={s} esc={esc} irACampo={irACampo} />
      </Tarjeta>

      <Tarjeta id="rnt-calc-total" eyebrow="Resultado" titulo="Utilidad del mes, acumulados y equilibrio" enfoque={enfoque}>
        <F>U = S1 + S2 + S3 + S4 + S5 + S7 − Cop{s.sumarHoras ? " + S6" : ""}</F>
        <Sust total={crc(m12.U)}>
          {[m12.S1, m12.S2, m12.S3, m12.S4, m12.S5, m12.S7].map((v) => numero(v, 0)).join(" + ")} − {numero(m12.costos, 0)}{s.sumarHoras ? ` + ${numero(m12.S6, 0)}` : ""}
        </Sust>
        <F>Acumulado(M) = suma de U de los meses 1 a M</F>
        <p className="rnt-calc__s">A 12 meses: <b>{crc(mes(r, 12).acum)}</b> · a 24 meses: <b>{crc(mes(r, 24).acum)}</b></p>
        <F>Recurrente acumulado(M) = suma de MRR de los meses 1 a M</F>
        <p className="rnt-calc__s">A 12 meses: <b>{crc(mes(r, 12).recAcum)}</b> · a 24 meses: <b>{crc(mes(r, 24).recAcum)}</b> (ingreso sin IVA, no utilidad).</p>
        <F>Equilibrio = primer mes en que Acumulado ≥ Inversión de las etapas incluidas</F>
        <p className="rnt-calc__s">
          {r.inversion === null ? "Sin inversión escrita: no hay punto de equilibrio que calcular."
            : `Inversión ${crc(r.inversion)} → ${r.equilibrio.tipo === "mes" ? `mes ${r.equilibrio.mes}` : "más de 36 meses con estos supuestos"}.`}
        </p>
        <F>Aporte(etapa s) = Acumulado 24(etapas 0…s) − Acumulado 24(etapas 0…s−1)</F>
        <p className="rnt-calc__s">
          Escenario {nombreEsc}: Etapa 0 {crcSigno(r.aportes[0])} · Etapa 1 {crcSigno(r.aportes[1])} · Etapa 2 {crcSigno(r.aportes[2])} · Etapa 3 {crcSigno(r.aportes[3])}.
          Las etapas 4 y 5 no llevan cifra propia.
        </p>
      </Tarjeta>
    </div>
    </>
  );
}

const GLOSARIO: [string, string][] = [
  ["P0", "pedidos en línea por mes, hoy"], ["T", "monto promedio por pedido (sin IVA)"], ["V0", "ventas en línea de hoy por mes"],
  ["N", "pedidos del mes con tienda propia"], ["u", "aumento de ventas en línea"], ["r₀ … r₃", "rampa de cada etapa: de 0 a 1"],
  ["R", "meses hasta que una etapa rinde completo"], ["m", "margen sobre venta de equipo"], ["pc", "pedidos que llevan cámaras"],
  ["ti", "pedidos con cámaras que agregan instalación"], ["I", "precio promedio de una instalación"], ["mi", "margen sobre venta de instalación"],
  ["B", "compradores con cuenta (acumulados)"], ["ra", "compradores que vuelven a comprar en un año"], ["Tr", "monto de una recompra"],
  ["A", "planes de mantenimiento activos"], ["tp", "pedidos con cámaras que toman el plan"], ["b", "bajas del plan por mes"],
  ["c", "cuota mensual del plan"], ["MRR", "ingreso del plan en el mes"], ["mp", "margen del plan"],
  ["K", "carritos abandonados con contacto por mes"], ["rr", "carritos que vuelven con el recordatorio"],
  ["Hh", "horas manuales al mes"], ["he", "parte de esas horas que hace la plataforma"], ["ch", "costo de una hora"],
  ["Fm", "mensualidad de Fygaro"], ["cF", "comisión de Fygaro"], ["Cop", "costo mensual de operar la tienda propia"],
  ["U", "utilidad incremental del mes"],
];

export function QueNoIncluye() {
  return (
    <ul className="rnt-noincluye">
      <li>No le pone número a A3, A4, A5, B3, B4, C1, C2, C3, D1, D2, E1, E2 ni E3: no las sumamos para no inflar.</li>
      <li>No incluye impuesto sobre la renta ni gastos fijos que no cambian con la tienda.</li>
      <li>No incluye comisiones de tarjeta: se pagan igual con o sin tienda propia.</li>
      <li>No promete resultados: es una forma ordenada de hacer la cuenta.</li>
    </ul>
  );
}
