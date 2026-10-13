/* Estado de los pedidos de Detras de escena: los 8 de ejemplo (mutables en memoria y en
   sessionStorage["crimson-demo:ges:pedidos"]) + los pedidos de prueba que Andres hizo en la tienda (los guarda mockApi;
   se leen con leerPedidosDemo y su estado se escribe con escribirEstadoDemo para que "Mi pedido", B1 y C1 lo vean).
   La transicion es una funcion pura: el reducer la puede correr dos veces (StrictMode) sin efectos. */
import { almacen, escribirEstadoDemo, leerPedidosDemo, type EstadoDemo, type PedidoDemo } from "./comun";
import {
  ETIQUETA, ES_ESTADO, FACTURA_DE, METODO_LARGO, pedido128, pedidosEjemplo, textoBitacora,
  type Bitacora, type Correo, type Estado, type Hist, type PedidoGes,
} from "./datos";

const CLAVE = "crimson-demo:ges:pedidos";
export type Extras = { correos: Correo[]; bitacora: Bitacora[]; factura: string | null; facturaAuto?: boolean; por: Record<string, string>; notas: Record<string, string>; payment_ref: string | null; refund: number | null };
export type EstadoPedidos = { v: 1; ejemplo: PedidoGes[]; extras: Record<string, Extras> };
export type Aplicar = { a: Estado; nota: string; ref: string; guia: string; transportista: string; monto: string; avisar: boolean };
export type Accion =
  | { t: "aplicar"; numero: string; demo: PedidoGes | null; p: Aplicar; ahora: number }
  | { t: "restablecer"; ahora: number }
  | { t: "estado128"; k: Estado; ahora: number };

const iso = (ms: number) => new Date(ms).toISOString();
const fresco = (ahora = Date.now()): EstadoPedidos => ({ v: 1, ejemplo: pedidosEjemplo(ahora), extras: {} });

export function cargarPedidos(): EstadoPedidos {
  try {
    const raw = almacen.sesion.get(CLAVE);
    if (raw) {
      const v = JSON.parse(raw) as EstadoPedidos;
      if (v && v.v === 1 && Array.isArray(v.ejemplo) && v.ejemplo.length === 8 && v.extras && typeof v.extras === "object") return v;
    }
  } catch { /* dato corrupto: se vuelve a los de ejemplo */ }
  return fresco();
}
export const guardarPedidos = (e: EstadoPedidos) => almacen.sesion.set(CLAVE, JSON.stringify(e));

const facturaDemo = (numero: string) => `FE-00${numero.replace(/\D/g, "").slice(-4).padStart(4, "0")}`;
const asunto = (s: Estado, n: string) => `${ETIQUETA[s]} · ${n}`;

/** Pedido de prueba (mockApi) -> forma de la pantalla de Crimson, con lo que Crimson le agrego (correos, bitacora). */
export function demoAGes(d: PedidoDemo, x?: Extras): PedidoGes {
  const kind = d.payment_kind || "sinpe";
  const history: Hist[] = d.history.map((h) => ({
    status: ES_ESTADO(h.status) ? h.status : "nuevo", at: h.at, by: x?.por[h.at] ?? (h.status === "en_revision" ? "cliente" : null),
    ...(x?.notas[h.at] ? { note: x.notas[h.at] } : {}),
  }));
  const status: Estado = ES_ESTADO(d.status) ? d.status : "nuevo";
  const email = d.contact.email || "";
  return {
    numero: d.number, token: d.token, demo: true,
    cliente: { name: d.contact.name || "Cliente de prueba", email },
    entrega: { type: d.delivery.type === "retiro" ? "retiro" : "envio", method: d.shipping_method || (d.delivery.type === "retiro" ? "Retiro en Alajuela" : "Envío"), province: d.delivery.province, canton: d.delivery.canton, district: d.delivery.district },
    pago: { kind, label: METODO_LARGO[kind] || d.payment_method },
    lineas: d.lines.map((l) => ({ name: l.name, quantity: l.quantity, unit_price: l.unit_price, total: l.total, envio: l.shipping })),
    subtotal: d.subtotal, envio: d.shipping, iva: d.tax_total, total: d.total,
    created_at: d.created_at, status, paid_at: d.paid_at, expires_at: null,
    tracking_number: d.tracking_number, carrier: d.carrier, cancel_reason: d.cancel_reason,
    payment_ref: x?.payment_ref ?? null, refund: x?.refund ?? null,
    history, proofs: d.proofs,
    correos: x?.correos ?? (email ? [{ status: "simulado", subject: `Recibimos tu pedido · ${d.number}`, to: email, at: d.created_at }] : []),
    bitacora: x?.bitacora ?? [], factura: x?.factura ?? null, facturaAuto: x?.facturaAuto, requiereCompra: null, margen: 0.35,
  };
}

/** La transicion de estado tal como la hace el portal: historial, correo (simulado), factura automatica al pagar
    y renglon de bitacora de auditoria. Pura. */
export function transicion(o: PedidoGes, p: Aplicar, ahora: number): PedidoGes {
  const at = iso(ahora);
  const nota = p.nota.trim();
  const n: PedidoGes = {
    ...o, status: p.a,
    history: [...o.history, { status: p.a, at, by: "Andrés", ...(nota ? { note: nota } : {}) }],
    correos: [...o.correos], bitacora: [...o.bitacora, { at, quien: "Andrés", texto: textoBitacora(p.a, o.numero) }],
  };
  if (p.a === "pagado") {
    n.paid_at = at; n.expires_at = null;
    if (p.ref.trim()) n.payment_ref = p.ref.trim();
    if (!n.factura) {
      n.factura = FACTURA_DE[o.numero] || facturaDemo(o.numero);
      n.facturaAuto = true;
      n.bitacora.push({ at, quien: "Sistema", texto: `emitió la factura electrónica ${n.factura} de ${o.numero} automáticamente (simulada)` });
      if (o.cliente.email) n.correos.push({ status: "simulado", subject: `Factura electrónica ${n.factura} · ${o.numero}`, to: o.cliente.email, at });
    }
  }
  if (p.a === "enviado") { n.tracking_number = p.guia.trim() || null; n.carrier = p.transportista.trim() || null; }
  if (p.a === "cancelado") { n.cancel_reason = nota; n.expires_at = null; }
  if (p.a === "reembolsado") n.refund = Number(String(p.monto).replace(/[^\d.,]/g, "").replace(",", ".")) || o.total;
  if (p.avisar && o.cliente.email) n.correos.push({ status: "simulado", subject: asunto(p.a, o.numero), to: o.cliente.email, at });
  return n;
}

export function reducer(s: EstadoPedidos, a: Accion): EstadoPedidos {
  if (a.t === "restablecer") return fresco(a.ahora);
  if (a.t === "estado128") return { ...s, ejemplo: s.ejemplo.map((o) => (o.numero === "ORD-000128" ? pedido128(a.k, a.ahora) : o)) };
  // aplicar
  if (a.demo) {
    const n = transicion(a.demo, a.p, a.ahora);
    const prev = s.extras[a.numero];
    const at = iso(a.ahora);
    const ex: Extras = {
      correos: n.correos, bitacora: n.bitacora, factura: n.factura, facturaAuto: n.facturaAuto,
      por: { ...(prev?.por || {}), [at]: "Andrés" }, notas: { ...(prev?.notas || {}), ...(a.p.nota.trim() ? { [at]: a.p.nota.trim() } : {}) },
      payment_ref: n.payment_ref, refund: n.refund,
    };
    return { ...s, extras: { ...s.extras, [a.numero]: ex } };
  }
  return { ...s, ejemplo: s.ejemplo.map((o) => (o.numero === a.numero ? transicion(o, a.p, a.ahora) : o)) };
}

/** Lo que "Mi pedido" del cliente ve de un pedido de prueba despues de que Crimson lo movio. */
export function estadoParaCliente(o: PedidoGes, base: PedidoDemo | undefined, a: Estado, ahora: number, p: Aplicar): EstadoDemo {
  const at = iso(ahora);
  const hist = (base?.history || []).map((h) => ({ status: h.status, label: h.label, at: h.at }));
  hist.push({ status: a, label: ETIQUETA[a], at });
  return {
    status: a,
    paid_at: a === "pagado" ? at : base?.paid_at ?? o.paid_at ?? null,
    tracking_number: a === "enviado" ? p.guia.trim() || null : base?.tracking_number ?? null,
    carrier: a === "enviado" ? p.transportista.trim() || null : base?.carrier ?? null,
    cancel_reason: a === "cancelado" ? p.nota.trim() : base?.cancel_reason ?? null,
    history: hist,
  };
}
export const escribirParaCliente = (token: string, e: EstadoDemo) => escribirEstadoDemo(token, e);

/** Todos los pedidos (prueba primero, luego los de ejemplo), solo lectura: Inventario, C1, indice. */
export function leerTodos(): PedidoGes[] {
  const s = cargarPedidos();
  const demos = leerPedidosDemo().map((d) => demoAGes(d, s.extras[d.number]));
  return [...demos, ...s.ejemplo];
}
