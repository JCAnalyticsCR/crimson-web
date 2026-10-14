/* Maqueta B2 · «Plan de mantenimiento como suscripcion» (PROPUESTA, no incluida aun).
   1. Crimson disena el plan (nombre, precio, visitas, lo que incluye, como se cobra): se guarda en
      localStorage["crimson-demo:b2:plan"] y TODAS las pantallas siguientes lo usan (y ¿Es rentable? lo ofrece).
   2. La oferta en el ultimo paso del pago (replica del checkout real, CSS de la tienda en solo lectura).
   3. «Mi plan» en el portal, con selector Hoy / En 6 meses / En 12 meses que solo cambia lo mostrado.
   4. Los avisos al celular. 5. Asi lo ve Crimson en Mantenimientos.
   Lo que incluye y el precio los decide Crimson: aqui todo es ejemplo. Sin simulador propio: los numeros
   viven solo en /rentabilidad. Pantalla de decision: nada se mueve solo. */
import { useEffect, useId, useRef, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { notifyDemo } from "../../mockApi";
import "../../../modules/store/shop.css";
import "../../../modules/store/checkout.css";
import {
  CLAVES, MarcoMaqueta, Pantalla, SelloPropuesta, almacen, colones, desdeAhora, fecha, usePaso, useProducto, type Paso,
} from "./comun";
import {
  COBRO_TXT, CONTRATO, CONTRATOS_OTROS, MARIA, MAX_INCLUYE, NOTAS_VISITA, ORD128, PLAN_EJEMPLO, TECNICO, VISITAS_OPC,
  articulos, conIva, diasEntre, facturaPlan, fechaAno, fechaDia, fechaLargaSinDia, leerPlan, sumarDias, sumarMeses,
  type CobroPlan, type PlanB2,
} from "./datos";
import { CabezaSeccion, IC, Ico, PortalCliente } from "./piezas";
import "./cliente.css";

const PASOS: readonly Paso[] = [
  { id: "diseno", titulo: "Tu plan, tu precio", vista: "crimson" },
  { id: "oferta", titulo: "La oferta en el pago", vista: "cliente" },
  { id: "plan", titulo: "Mi plan", vista: "cliente" },
  { id: "avisos", titulo: "Los avisos", vista: "cliente" },
  { id: "crimson", titulo: "Así le llega a Crimson", vista: "crimson" },
];
const IDS = PASOS.map((p) => p.id);

// lo que eligio Maria en la oferta (solo esta sesion)
const CLAVE_OFERTA = "crimson-demo:cliente:b2";
type Oferta = { on: boolean; cobro: CobroPlan };
function leerOferta(): Oferta {
  try {
    const x = JSON.parse(almacen.sesion.get(CLAVE_OFERTA) || "null") as Partial<Oferta> | null;
    return { on: !!x?.on, cobro: x?.cobro === "factura" ? "factura" : "tarjeta" };
  } catch { return { on: false, cobro: "tarjeta" }; }
}
const cadaMeses = (p: PlanB2) => 12 / p.visitasAno;
const visitasTxt = (p: PlanB2) => (p.visitasAno === 1 ? "1 visita preventiva al año" : `${p.visitasAno} visitas preventivas al año (una cada ${cadaMeses(p)} meses)`);
const incluidos = (p: PlanB2) => p.incluye.filter((x) => x.on && x.texto.trim());

export default function B2Plan() {
  const [i, irA] = usePaso(IDS);
  const [plan, setPlanSt] = useState<PlanB2>(() => leerPlan(almacen.local.get(CLAVES.planB2)));
  const tocado = useRef(false);
  const [guardado, setGuardado] = useState(true);
  useEffect(() => { if (tocado.current) setGuardado(almacen.local.set(CLAVES.planB2, JSON.stringify(plan))); }, [plan]);
  const setPlan = (f: (p: PlanB2) => PlanB2) => { tocado.current = true; setPlanSt(f); };
  const volverAlEjemplo = () => { tocado.current = false; almacen.local.del(CLAVES.planB2); setPlanSt(PLAN_EJEMPLO); setGuardado(true); };

  const [oferta, setOfertaSt] = useState<Oferta>(leerOferta);
  const setOferta = (o: Partial<Oferta>) => setOfertaSt((x) => { const n = { ...x, ...o }; almacen.sesion.set(CLAVE_OFERTA, JSON.stringify(n)); return n; });
  const cobro: CobroPlan = plan.cobro.includes(oferta.cobro) ? oferta.cobro : plan.cobro[0];
  const paso = IDS[i];

  return (
    <MarcoMaqueta
      id="B2" etapa={2}
      titulo="Plan de mantenimiento como suscripción"
      bajada="Al comprar cámaras, el cliente puede sumar un plan mensual. Para Crimson es ingreso que se repite y se acumula mes a mes."
      hoy="Crimson ya maneja contratos de mantenimiento y facturación recurrente, pero se ofrecen y se arman a mano."
      conPropuesta="El plan se ofrece en el pago, el contrato se crea solo con su próxima visita, el cliente lo ve en su portal y el cobro se repite cada mes."
      pasos={PASOS}
      aparte={<Aparte paso={paso} />}
    >
      <div className="cli">
        <div className="cli-banda" role="note">
          <span className="cli-insignia cli-insignia--ej">Ejemplo</span>
          <p><b>Ejemplo: lo que incluye y el precio los decide Crimson.</b> <span>Depende de contratar ONVO para el cobro automático con tarjeta.</span></p>
        </div>
        {paso === "diseno" && (
          <Diseno plan={plan} setPlan={setPlan} guardado={guardado} onEjemplo={volverAlEjemplo} onSeguir={() => irA(1)} />
        )}
        {paso === "oferta" && (
          <OfertaPago plan={plan} on={oferta.on} cobro={cobro} onOn={(on) => setOferta({ on })} onCobro={(c) => setOferta({ cobro: c })} onSeguir={() => irA(2)} />
        )}
        {paso === "plan" && <MiPlan plan={plan} cobro={cobro} />}
        {paso === "avisos" && <Avisos plan={plan} cobro={cobro} />}
        {paso === "crimson" && <Crimson plan={plan} cobro={cobro} />}
      </div>
    </MarcoMaqueta>
  );
}

// ---------------------------------------------------------------- columna «Que pasa aqui»
const QUE_PASA: Record<string, { t: string; p: string; ya?: string }> = {
  diseno: {
    t: "Crimson define el plan",
    p: "Nombre, precio, cuántas visitas al año, qué incluye y cómo se cobra. Todo lo que ves en las pantallas siguientes sale de aquí: cambialo y miralo en el pago, en el portal y en los avisos.",
    ya: "Se guarda solo en este navegador. Lo que incluye y el precio los decide Andrés.",
  },
  oferta: {
    t: "Se ofrece en el último paso del pago",
    p: "Cuando el carrito trae cámaras, el pago ofrece el plan. El total de hoy no cambia: la cuota se cobra aparte cada mes, con tarjeta guardada en ONVO o con factura mensual.",
    ya: "El pago en 4 pasos con SINPE Móvil, transferencia, tarjeta (ONVO integrada; falta contratar) y efectivo ya funciona.",
  },
  plan: {
    t: "El contrato se crea solo",
    p: "Al pagar, el contrato queda con su próxima visita. María ve su plan, el historial de visitas y cada cobro con su factura electrónica, sin pedirle nada a nadie.",
    ya: "Los contratos de mantenimiento y la facturación recurrente ya existen.",
  },
  avisos: {
    t: "Los avisos salen solos",
    p: "Antes de cada visita, con cada cobro y antes de renovar. El canal (WhatsApp o SMS) y su costo están por definir.",
  },
  crimson: {
    t: "Ingreso que se repite",
    p: "Cada plan vendido entra a Mantenimientos con su cuota y su próxima visita. Cada mes se suman planes nuevos y se restan las bajas.",
    ya: "Mantenimientos y la facturación recurrente ya existen; lo nuevo es que el plan se venda solo en el pago.",
  },
};
function Aparte({ paso }: { paso: string }) {
  const q = QUE_PASA[paso] ?? QUE_PASA.diseno;
  return (
    <div className="cli cli-aparte">
      <h3 className="cli-aparte__h">{q.t}</h3>
      <p>{q.p}</p>
      {q.ya && <p className="cli-aparte__ya"><span aria-hidden="true">✓ </span>{q.ya}</p>}
      <div className="cli-aparte__gana">
        <p className="cli-meta">Lo que gana Crimson</p>
        <p>Cada sistema vendido puede volverse ingreso mensual, sin venderlo uno por uno. Arranca chico y crece cada mes.</p>
        <Link to="/rentabilidad?enfoque=B2" className="cli-flecha">Ver cuánto suma en ¿Es rentable? →</Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- tarjeta de la oferta (la usan el editor y el pago)
function TarjetaOferta({ plan, on, cobro, onOn, onCobro, vista = false }: {
  plan: PlanB2; on: boolean; cobro: CobroPlan; onOn: (on: boolean) => void; onCobro: (c: CobroPlan) => void; vista?: boolean;
}) {
  const uid = useId();
  const iva = conIva(plan.precioSinIva);
  const inc = incluidos(plan);
  return (
    <section className={`cli-oferta${on ? " cli-oferta--on" : ""}`} aria-labelledby={`${uid}-t`}>
      <div className="cli-oferta__top">
        <SelloPropuesta id="B2" tamano="chico" />
        <span className="cli-meta">{vista ? "Vista previa" : "Opcional"}</span>
      </div>
      <h4 id={`${uid}-t`} className="cli-oferta__h">¿Le sumás mantenimiento?</h4>
      <p className="cli-oferta__precio"><b>{plan.nombre}</b> · {colones(iva)} al mes, IVA incluido</p>
      <p className="cli-oferta__vis">{visitasTxt(plan)}</p>
      {inc.length > 0 && (
        <ul className="cli-oferta__inc">
          {inc.map((x, k) => <li key={k}><Ico d={IC.check} size={15} /><span>{x.texto}</span></li>)}
        </ul>
      )}
      <button type="button" role="switch" aria-checked={on} className="cli-switch" onClick={() => onOn(!on)}>
        <span className="cli-switch__pista" aria-hidden="true"><i /></span>
        <span>Agregar el plan</span>
      </button>
      {on && (
        <>
          <p className="cli-oferta__ok"><Ico d={IC.check} size={15} /><span>{plan.nombre} · {colones(iva)}/mes · se cobra aparte cada mes; el total de hoy no cambia</span></p>
          <fieldset className="cli-opciones cli-opciones--compacta">
            <legend>¿Cómo querés pagar el plan?</legend>
            {plan.cobro.map((c) => (
              <label key={c} className="cli-opcion">
                <input type="radio" name={`${uid}-cobro`} checked={cobro === c} onChange={() => onCobro(c)} />
                <span>
                  {c === "tarjeta" ? "Tarjeta guardada con ONVO" : "Factura mensual para pagar por SINPE Móvil o transferencia"}
                  <small>{c === "tarjeta" ? "Los datos de la tarjeta los guarda ONVO, no Crimson." : "Te llega cada mes al correo y a tu portal."}</small>
                </span>
              </label>
            ))}
          </fieldset>
        </>
      )}
      <p className="cli-note">Podés cancelarlo desde tu portal (condición de ejemplo).</p>
    </section>
  );
}

// ---------------------------------------------------------------- 1 · tu plan, tu precio (lo usa Crimson)
function Diseno({ plan, setPlan, guardado, onEjemplo, onSeguir }: {
  plan: PlanB2; setPlan: (f: (p: PlanB2) => PlanB2) => void; guardado: boolean; onEjemplo: () => void; onSeguir: () => void;
}) {
  const uid = useId();
  const [nombre, setNombre] = useState(plan.nombre);
  const [precio, setPrecio] = useState(String(plan.precioSinIva));
  const [vistaOn, setVistaOn] = useState(false);
  const [vistaCobro, setVistaCobro] = useState<CobroPlan>(plan.cobro[0]);
  const lista = useRef<HTMLUListElement>(null);
  const largo = useRef(plan.incluye.length);
  useEffect(() => {
    if (plan.incluye.length > largo.current) lista.current?.querySelector<HTMLTextAreaElement>("li:last-child textarea")?.focus();
    largo.current = plan.incluye.length;
  }, [plan.incluye.length]);

  const volver = () => { onEjemplo(); setNombre(PLAN_EJEMPLO.nombre); setPrecio(String(PLAN_EJEMPLO.precioSinIva)); setVistaCobro(PLAN_EJEMPLO.cobro[0]); };
  const errNombre = nombre.trim() ? null : "Escribí un nombre para el plan.";
  const precioN = Number(precio);
  const errPrecio = precio && precioN > 0 ? null : "Escribí un precio mayor que cero.";
  const iva = conIva(plan.precioSinIva);

  const onNombre = (e: ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value.slice(0, 60);
    setNombre(v);
    if (v.trim()) setPlan((p) => ({ ...p, nombre: v.trim() }));
  };
  const onPrecio = (e: ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value.replace(/\D/g, "").replace(/^0+(?=\d)/, "").slice(0, 7);
    setPrecio(v);
    if (Number(v) > 0) setPlan((p) => ({ ...p, precioSinIva: Number(v) }));
  };
  const setInc = (k: number, cambio: Partial<{ texto: string; on: boolean }>) =>
    setPlan((p) => ({ ...p, incluye: p.incluye.map((x, j) => (j === k ? { ...x, ...cambio } : x)) }));
  const quitarInc = (k: number) => setPlan((p) => ({ ...p, incluye: p.incluye.filter((_, j) => j !== k) }));
  const agregarInc = () => setPlan((p) => (p.incluye.length >= MAX_INCLUYE ? p : { ...p, incluye: [...p.incluye, { texto: "", on: true }] }));
  const alternarCobro = (c: CobroPlan) =>
    setPlan((p) => {
      const tiene = p.cobro.includes(c);
      if (tiene && p.cobro.length === 1) return p;
      return { ...p, cobro: tiene ? p.cobro.filter((x) => x !== c) : (["tarjeta", "factura"] as CobroPlan[]).filter((x) => x === c || p.cobro.includes(x)) };
    });

  return (
    <Pantalla superficie="admin" seccion="Mantenimientos" estado="propuesta">
      <div className="cli cli-adm">
        <CabezaSeccion rotulo="13 · Soporte · Planes" titulo="Tu plan, tu precio">
          <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={volver}>Volver al ejemplo</button>
        </CabezaSeccion>
        <div className="cli-editor">
          <form className="cli-form cli-editor__form" onSubmit={(e) => e.preventDefault()} noValidate aria-label="Diseño del plan de mantenimiento">
            <label className="cli-field">
              <span className="cli-field__l">Nombre del plan</span>
              <input className="cli-input" type="text" value={nombre} maxLength={60} onChange={onNombre}
                aria-invalid={!!errNombre} aria-describedby={errNombre ? `${uid}-en` : undefined} />
              {errNombre && <small id={`${uid}-en`} className="cli-err">{errNombre}</small>}
            </label>
            <label className="cli-field">
              <span className="cli-field__l">Precio mensual sin IVA</span>
              <span className="cli-suf">
                <span className="cli-suf__u" aria-hidden="true">₡</span>
                <input className="cli-input cli-input--num" type="text" inputMode="numeric" value={precio} onChange={onPrecio}
                  aria-invalid={!!errPrecio} aria-describedby={`${uid}-pi${errPrecio ? ` ${uid}-ep` : ""}`} />
              </span>
              <small id={`${uid}-pi`} className="cli-note">{colones(plan.precioSinIva)} sin IVA = {colones(iva)} al mes IVA incluido</small>
              {errPrecio && <small id={`${uid}-ep`} className="cli-err">{errPrecio}</small>}
            </label>
            <fieldset className="cli-field cli-fs">
              <legend className="cli-field__l">Visitas preventivas al año</legend>
              <div className="cli-seg">
                {VISITAS_OPC.map((v) => (
                  <label key={v}>
                    <input type="radio" name={`${uid}-vis`} checked={plan.visitasAno === v} onChange={() => setPlan((p) => ({ ...p, visitasAno: v }))} />
                    <span>{v}</span>
                  </label>
                ))}
              </div>
              <small className="cli-note">{plan.visitasAno === 1 ? "Una visita al año." : `Una visita cada ${cadaMeses(plan)} meses.`}</small>
            </fieldset>
            <fieldset className="cli-field cli-fs">
              <legend className="cli-field__l">Qué incluye</legend>
              <ul className="cli-inc" ref={lista}>
                {plan.incluye.map((x, k) => (
                  <li key={k}>
                    <input type="checkbox" checked={x.on} onChange={(e) => setInc(k, { on: e.target.checked })} aria-label={`Incluir en la oferta: ${x.texto || `punto ${k + 1}`}`} />
                    <textarea className="cli-input cli-input--sm cli-input--linea" rows={2} value={x.texto} maxLength={90} placeholder="Ej.: revisión del respaldo eléctrico"
                      onChange={(e) => setInc(k, { texto: e.target.value.replace(/\s*\n\s*/g, " ") })} onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }}
                      aria-label={`Texto del punto ${k + 1}`} />
                    <button type="button" className="cli-x" onClick={() => quitarInc(k)} aria-label={`Quitar ${x.texto ? `«${x.texto}»` : `el punto ${k + 1}`}`}>
                      <Ico d={IC.x} size={16} />
                    </button>
                  </li>
                ))}
              </ul>
              <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm cli-inc__mas" onClick={agregarInc} disabled={plan.incluye.length >= MAX_INCLUYE}>
                <Ico d={IC.plus} size={16} />Agregar otra
              </button>
              {plan.incluye.length >= MAX_INCLUYE && <small className="cli-note">Hasta {MAX_INCLUYE} puntos en esta vista previa.</small>}
            </fieldset>
            <fieldset className="cli-field cli-fs">
              <legend className="cli-field__l">Cómo se cobra</legend>
              {(["tarjeta", "factura"] as CobroPlan[]).map((c) => {
                const on = plan.cobro.includes(c);
                return (
                  <label key={c} className="cli-check">
                    <input type="checkbox" checked={on} disabled={on && plan.cobro.length === 1} onChange={() => alternarCobro(c)} />
                    <span>{COBRO_TXT[c]}{c === "tarjeta" && <small> · requiere contratar ONVO</small>}</span>
                  </label>
                );
              })}
              <small className="cli-note">Al menos una forma de cobro.</small>
            </fieldset>
            <p className="cli-note" role="status">
              {guardado ? "Se guarda solo en este navegador y lo usan todas las pantallas de esta maqueta." : "Tu navegador no deja guardar: el plan vive mientras no cerrés esta página."}
            </p>
          </form>
          <div className="cli-editor__vista">
            <p className="cli-meta">Así aparece en el pago (se actualiza mientras escribís)</p>
            <TarjetaOferta plan={plan} on={vistaOn} cobro={plan.cobro.includes(vistaCobro) ? vistaCobro : plan.cobro[0]} onOn={setVistaOn} onCobro={setVistaCobro} vista />
            <button type="button" className="cli-btn cli-btn--ink cli-btn--block" onClick={onSeguir}>Verla en el pago →</button>
          </div>
        </div>
      </div>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 2 · la oferta en el pago (replica del paso «Confirmar»)
function ImgLinea({ id }: { id: number }) {
  const p = useProducto(id);
  return <div className="sh-line__img">{p?.image ? <img src={p.image} alt="" width={52} height={52} loading="lazy" decoding="async" /> : null}</div>;
}
function ResumenPedido({ plan, on }: { plan: PlanB2; on: boolean }) {
  return (
    <div className="ck-sum">
      <span className="sh-meta">Tu pedido · {articulos(ORD128)} artículo(s)</span>
      <ul className="ck-lines">
        {ORD128.lineas.map((l) => (
          <li key={l.id}>
            <ImgLinea id={l.id} />
            <div style={{ minWidth: 0 }}>
              <div className="sh-line__name">{l.nombre}</div>
              <div className="ck-qty"><span>× {l.cantidad}</span></div>
            </div>
            <b className="sh-line__total">{colones(l.total)}</b>
          </li>
        ))}
      </ul>
      <div className="ck-totals">
        <div className="sh-totrow"><span>Subtotal (sin IVA)</span><b>{colones(ORD128.subtotal)}</b></div>
        <div className="sh-totrow"><span>Envío (sin IVA)</span><b>{colones(ORD128.envioSinIva)}</b></div>
        <div className="sh-totrow"><span>IVA 13 %</span><b>{colones(ORD128.iva)}</b></div>
        <div className="sh-totrow sh-totrow--big"><span>Total</span><b>{colones(ORD128.total)}</b></div>
        {on && <p className="cli-replica__plan"><b>+ {plan.nombre}</b> · {colones(conIva(plan.precioSinIva))}/mes · se cobra aparte cada mes; el total de hoy no cambia</p>}
        <p className="sh-note" style={{ margin: 0 }}>Calculado por la tienda con los precios vigentes. Con tu pago emitimos la factura electrónica.</p>
      </div>
    </div>
  );
}
function OfertaPago({ plan, on, cobro, onOn, onCobro, onSeguir }: {
  plan: PlanB2; on: boolean; cobro: CobroPlan; onOn: (on: boolean) => void; onCobro: (c: CobroPlan) => void; onSeguir: () => void;
}) {
  const confirmar = () => notifyDemo({
    title: "Pedido de demostración",
    text: on
      ? `En la tienda real se confirma el pedido por ${colones(ORD128.total)} y, aparte, el contrato ${CONTRATO} del ${plan.nombre} con su primera visita. Esta propuesta no está incluida aún.`
      : `En la tienda real aquí se confirma el pedido por ${colones(ORD128.total)}. Esta maqueta no crea nada.`,
  });
  return (
    <Pantalla superficie="tienda" direccion="tienda.crimsoncr.com/?v=pagar">
      <div className="shop cli-replica">
        <div className="sh-wrap ck">
          <ol className="ck-steps" aria-label="Pasos del pedido (réplica)">
            {["Tus datos", "Entrega", "Pago", "Confirmar"].map((s, k) => (
              <li key={s} aria-current={k === 3 ? "step" : undefined} data-done={k < 3}>
                <button type="button" disabled tabIndex={-1}>
                  <span className="ck-steps__n">{k < 3 ? <Ico d={IC.check} size={14} /> : k + 1}</span><span className="ck-steps__t">{s}</span>
                </button>
              </li>
            ))}
          </ol>
          <details className="ck-mini">
            <summary><span>Resumen · {articulos(ORD128)} artículo(s)</span><b>{colones(ORD128.total)}</b></summary>
            <ResumenPedido plan={plan} on={on} />
          </details>
          <div className="sh-checkout">
            <div className="sh-form">
              <p className="cli-replica__h"><span className="ck-stepno">Paso 4 de 4</span>Confirmar</p>
              <div className="sh-fieldset ck-review">
                <div><span className="sh-meta">Contacto</span><b>{MARIA.nombre}</b><span>{MARIA.correo}</span></div>
                <div><span className="sh-meta">Entrega</span><b>Envío GAM</b><span>{MARIA.lugar}</span></div>
                <div><span className="sh-meta">Pago</span><b>{ORD128.pago}</b></div>
              </div>
              <TarjetaOferta plan={plan} on={on} cobro={cobro} onOn={onOn} onCobro={onCobro} />
              <label className="ck-check"><input type="checkbox" defaultChecked /><span>Acepto los términos y condiciones de compra</span></label>
              <div className="ck-actions">
                <button type="button" className="sh-btn sh-btn--crimson ck-go" onClick={confirmar}>Confirmar pedido · {colones(ORD128.total)}</button>
              </div>
            </div>
            <aside className="sh-summary sh-panel ck-aside" aria-label="Resumen del pedido (réplica)"><ResumenPedido plan={plan} on={on} /></aside>
          </div>
        </div>
      </div>
      <div className="cli-bajo-replica">
        <p className="cli-note">Réplica del último paso del pago real con el pedido de ejemplo ORD-000128. Lo único nuevo es la tarjeta del plan.</p>
        <button type="button" className="cli-btn cli-btn--ink" onClick={onSeguir}>{on ? "Ver el plan en el portal de María →" : "Ver cómo se vería en el portal →"}</button>
      </div>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 3 · mi plan (portal)
const MOMENTOS = [{ t: 0, l: "Hoy" }, { t: 6, l: "En 6 meses" }, { t: 12, l: "En 12 meses" }] as const;
function MiPlan({ plan, cobro }: { plan: PlanB2; cobro: CobroPlan }) {
  const uid = useId();
  const [t, setT] = useState<0 | 6 | 12>(0);
  const hoy = desdeAhora(0, 9, 0);
  const cada = cadaMeses(plan);
  const iva = conIva(plan.precioSinIva);
  const visitas = Array.from({ length: plan.visitasAno * 2 }, (_, k) => ({ mes: cada * (k + 1), f: sumarMeses(hoy, cada * (k + 1)) }));
  const hechas = visitas.filter((v) => v.mes <= t);
  const proxima = visitas.find((v) => v.mes > t)!;
  const renovada = t >= 12;
  const renov = sumarMeses(hoy, renovada ? 24 : 12);
  const cobros = Array.from({ length: t }, (_, k) => ({ f: sumarMeses(hoy, k + 1), n: facturaPlan(k) }));
  const cuandoTxt = t === 0 ? "hoy" : `dentro de ${t} meses (${fechaAno(sumarMeses(hoy, t))})`;
  return (
    <Pantalla superficie="portal" direccion="app.crimsoncr.com/mi-plan">
      <PortalCliente actual="plan">
        <CabezaSeccion rotulo="Mantenimiento" titulo="Mi plan">
          <span className="cli-badge cli-badge--ok">Activo</span>
        </CabezaSeccion>
        <fieldset className="cli-tiempo">
          <legend className="cli-meta">Ver el portal de María</legend>
          <div className="cli-seg cli-seg--ancho">
            {MOMENTOS.map((x) => (
              <label key={x.t}>
                <input type="radio" name={`${uid}-t`} checked={t === x.t} onChange={() => setT(x.t)} />
                <span>{x.l}</span>
              </label>
            ))}
          </div>
          <small className="cli-note" aria-live="polite">Solo cambia lo que se muestra: así se vería {cuandoTxt}.</small>
        </fieldset>
        <div className="cli-plan">
          <div className="cli-plan__card">
            <p className="cli-meta"><span className="cli-mono">{CONTRATO}</span> · Activo desde {fechaAno(hoy)}</p>
            <h4 className="cli-plan__n">{plan.nombre}</h4>
            <p className="cli-plan__p">{colones(iva)} al mes, IVA incluido · {visitasTxt(plan)}</p>
            <dl className="cli-kv cli-kv--1">
              <div><dt>Próxima visita</dt><dd><b>{fechaDia(proxima.f)}</b> de {proxima.f.getFullYear()}, en la mañana</dd></div>
              <div><dt>Renovación</dt><dd>{fechaAno(renov)}{renovada ? ` · se renovó sola el ${fechaAno(sumarMeses(hoy, 12))}` : " · se renueva sola"}</dd></div>
              <div><dt>Cobro</dt><dd>El día {hoy.getDate()} de cada mes · {cobro === "tarjeta" ? "tarjeta guardada con ONVO" : "factura mensual (SINPE Móvil o transferencia)"}</dd></div>
            </dl>
            <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={() => notifyDemo({ title: "Cambiar la fecha", text: "En el portal real, María elige otra fecha entre las disponibles y el técnico de Crimson recibe el cambio. En la vista previa no se cambia nada." })}>
              Cambiar la fecha de la visita
            </button>
          </div>
          <div className="cli-plan__inc">
            <p className="cli-meta">Qué incluye</p>
            {incluidos(plan).length ? (
              <ul className="cli-oferta__inc">{incluidos(plan).map((x, k) => <li key={k}><Ico d={IC.check} size={15} /><span>{x.texto}</span></li>)}</ul>
            ) : <p className="cli-note">Crimson todavía no marcó qué incluye.</p>}
          </div>
        </div>
        <h4 className="cli-sub">Historial de visitas</h4>
        {hechas.length ? (
          <ol className="cli-hist">
            {hechas.map((v, k) => (
              <li key={k}>
                <b>Visita {k + 1} · {fechaAno(v.f)}</b>
                <span>Técnico: {TECNICO} · {NOTAS_VISITA[k % NOTAS_VISITA.length]}</span>
              </li>
            ))}
          </ol>
        ) : <p className="cli-note">Todavía no hay visitas: la primera es el {fechaDia(visitas[0].f)} de {visitas[0].f.getFullYear()}.</p>}
        <h4 className="cli-sub">Cobros y facturas</h4>
        {t === 0 ? (
          <p className="cli-note">El primer cobro es el {fechaAno(sumarMeses(hoy, 1))}: {colones(iva)} con su factura electrónica.</p>
        ) : (
          <>
            <p className="cli-cobros__res">{t} cobros de {colones(iva)} con su factura electrónica ({facturaPlan(0)} y siguientes, de ejemplo).</p>
            <details className="cli-cobros">
              <summary>Ver los {t} cobros</summary>
              <ul>{cobros.map((c) => <li key={c.n}><span>{fechaAno(c.f)}</span><span className="cli-mono">{c.n}</span><b>{colones(iva)}</b></li>)}</ul>
            </details>
          </>
        )}
      </PortalCliente>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 4 · avisos al celular
function Avisos({ plan, cobro }: { plan: PlanB2; cobro: CobroPlan }) {
  const [resp, setResp] = useState<"1" | "2" | null>(null);
  const hoy = desdeAhora(0, 9, 0);
  const visita = sumarMeses(hoy, cadaMeses(plan));
  const aviso = sumarDias(visita, -7);
  const cobro1 = sumarMeses(hoy, 1);
  const renov = sumarMeses(hoy, 12);
  const avisoRenov = sumarDias(renov, -30);
  const iva = conIva(plan.precioSinIva);
  type Msg = { k: string; f: Date; cuando: string; texto: string };
  const msgs: Msg[] = [
    { k: "visita", f: aviso, cuando: "7 días antes de la visita", texto: `Hola, María: el ${fechaDia(visita)} te visita el técnico de Crimson para el mantenimiento de tus cámaras, en la mañana. Respondé 1 para confirmar o 2 para cambiar la fecha.` },
    {
      k: "cobro", f: cobro1, cuando: "Cada mes, con el cobro",
      texto: cobro === "tarjeta"
        ? `Recibimos el pago de tu plan: ${colones(iva)}. Tu factura electrónica está en tu portal.`
        : `Tu factura electrónica del plan (${colones(iva)}) ya está en tu portal. Podés pagarla por SINPE Móvil o transferencia.`,
    },
    { k: "renov", f: avisoRenov, cuando: "30 días antes de renovar", texto: `Tu plan se renueva el ${fechaLargaSinDia(renov)}. No tenés que hacer nada; si querés cambiarlo o cancelarlo, entrá a tu portal.` },
  ];
  msgs.sort((a, b) => a.f.getTime() - b.f.getTime());
  return (
    <Pantalla superficie="celular" rotulo="Mensaje al celular: WhatsApp o SMS, a definir">
      <div className="cli-chat">
        <div className="cli-chat__top">
          <span className="cli-chat__av" aria-hidden="true">C</span>
          <div><b>Crimson</b><small>Mensajes automáticos · {plan.nombre}</small></div>
        </div>
        <ol className="cli-chat__lista" aria-label="Mensajes de ejemplo que recibiría María">
          {msgs.map((m) => (
            <li key={m.k} className="cli-msg-g">
              <div className="cli-msg">
                <p className="cli-msg__cuando">{m.cuando} · {fecha(m.f, "dia")}</p>
                <p>{m.texto}</p>
              </div>
              {m.k === "visita" && (resp ? (
                <>
                  <div className="cli-msg cli-msg--yo"><p>{resp}</p></div>
                  <div className="cli-msg" role="status">
                    <p>{resp === "1" ? `Listo, María: quedó confirmada para el ${fechaDia(visita)}, en la mañana.` : "Con gusto. En la tienda real aquí te mostramos otras fechas disponibles para que elijás."}</p>
                  </div>
                </>
              ) : (
                <div className="cli-chat__resp" role="group" aria-label="Responder al aviso de la visita">
                  <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={() => setResp("1")}>1 · Confirmar</button>
                  <button type="button" className="cli-btn cli-btn--ghost cli-btn--sm" onClick={() => setResp("2")}>2 · Cambiar la fecha</button>
                </div>
              ))}
            </li>
          ))}
        </ol>
        <p className="cli-chat__pie">Mensajes de ejemplo: el canal y el texto final los define Crimson.</p>
      </div>
    </Pantalla>
  );
}

// ---------------------------------------------------------------- 5 · asi le llega a Crimson (Mantenimientos)
function Crimson({ plan, cobro }: { plan: PlanB2; cobro: CobroPlan }) {
  const hoy = desdeAhora(0, 8, 0);
  const filas = [
    { numero: CONTRATO, cliente: MARIA.nombre, plan: plan.nombre, cuota: plan.precioSinIva, cada: cadaMeses(plan), proxima: sumarMeses(hoy, cadaMeses(plan)), cobro: cobro === "tarjeta" ? "Tarjeta (ONVO)" : "Factura mensual", nuevo: true },
    ...CONTRATOS_OTROS.map((c) => ({ numero: c.numero, cliente: c.cliente, plan: c.plan, cuota: c.cuota, cada: c.cadaMeses, proxima: desdeAhora(c.proxima, 8, 0), cobro: c.cobro, nuevo: false })),
  ];
  const ingreso = filas.reduce((a, f) => a + f.cuota, 0);
  const esteMes = filas.filter((f) => { const d = diasEntre(hoy, f.proxima); return d >= 0 && d <= 30; }).length;
  return (
    <Pantalla superficie="admin" seccion="Mantenimientos" estado="existente">
      <div className="cli cli-adm">
        <CabezaSeccion rotulo="13 · Soporte" titulo="Mantenimientos">
          <SelloPropuesta estado="ejemplo" tamano="chico" />
        </CabezaSeccion>
        <div className="cli-kpis">
          <div><span className="cli-meta">Planes activos</span><b>{filas.length}</b></div>
          <div><span className="cli-meta">Ingreso recurrente del mes</span><b>{colones(ingreso)}</b><small>sin IVA</small></div>
          <div><span className="cli-meta">Visitas este mes</span><b>{esteMes}</b></div>
        </div>
        <div className="cli-tabla-c">
          <div className="cli-tabla" role="region" tabIndex={0} aria-label="Contratos de mantenimiento (datos de ejemplo)">
            <table>
              <thead>
                <tr><th scope="col">Contrato y cliente</th><th scope="col">Plan</th><th scope="col">Próxima visita</th><th scope="col" className="cli-num">Cuota sin IVA</th></tr>
              </thead>
              <tbody>
                {filas.map((f) => (
                  <tr key={f.numero} data-nuevo={f.nuevo}>
                    <td data-l="Contrato y cliente">
                      <span className="cli-mono">{f.numero}</span>
                      <span className="cli-tabla__sub cli-tabla__fuerte">{f.cliente}</span>
                      {f.nuevo && <span className="cli-badge cli-badge--crimson">Nuevo · desde la tienda</span>}
                    </td>
                    <td data-l="Plan">{f.plan}<span className="cli-tabla__sub">Cada {f.cada} meses</span></td>
                    <td data-l="Próxima visita">{fechaAno(f.proxima)}</td>
                    <td data-l="Cuota sin IVA" className="cli-num"><span className="cli-mono">{colones(f.cuota)}</span><span className="cli-tabla__sub">{f.cobro}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="cli-caja">
          <p>Cada mes se suman planes nuevos y se restan las bajas: así se acumula el ingreso.</p>
          <Link to="/rentabilidad?enfoque=B2" className="cli-flecha">Ver cuánto suma en ¿Es rentable? →</Link>
        </div>
        <ul className="cli-sellos">
          <li><SelloPropuesta estado="existente" tamano="chico" /><span>Contratos y facturación recurrente</span></li>
          <li><SelloPropuesta id="B2" tamano="chico" /><span>Se vende solo en el pago, con avisos y cobro automáticos</span></li>
        </ul>
      </div>
    </Pantalla>
  );
}
