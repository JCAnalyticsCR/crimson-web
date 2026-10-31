/* Editor de documento (cotizacion o factura): contenido + columna derecha fija con totales/acciones/tipo de cambio.
   Los totales se recalculan en vivo contra /documents/preview (misma logica que el guardado). */
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, fmtDate, fmtMoney, type Customer, type Invoice, type Line, type Product, type Quote } from "../../lib/api";
import { useSession } from "../../app/session";
import { Badge, Field, I, Icon, Loading, Modal } from "../../ui/components";
import AuthLink from "../../ui/AuthLink";
import { NO_SUMA, TREATMENTS, unitOptions, type Treatment } from "../../lib/units";
import QuoteVersions from "./QuoteVersions";
import { KitPicker, QuoteShare } from "./QuoteExtras";
import { useRecordAutosave } from "../../lib/useRecordAutosave";
import { AvailBadge, availText, sinExistencia } from "../../ui/Availability";
import NuevoCliente from "../../ui/NuevoCliente";
import EnProveedor from "../inventory/EnProveedor";

type Preview = { subtotal: string; discount_total: string; tax_total: string; total: string; lines: { subtotal: string; tax_amount: string; total: string; unit_price: string; name: string; tax_rate: string }[] };
const num = (v: unknown) => { const n = Number(v); return Number.isFinite(n) ? String(Number(n.toFixed(5))) : String(v ?? ""); };
const blankLine = (): Line => ({ product_id: null, code: null, name: "", description: "", unit: "Unid", quantity: "1", unit_price: "0", discount_type: "percent", discount_value: "0", tax_rate: "13", treatment: "normal" });
/* Una linea en 0 sin tratamiento no explica nada: hay que decir si es pendiente, aportada, cortesia o exclusion. */
const zeroUnexplained = (l: Line) => (l.treatment || "normal") === "normal" && !(Number(l.unit_price) > 0);
const nameRows = (s: string) => Math.min(4, Math.max(1, Math.ceil((s || "").length / 44)));
/* Imagen principal del producto (o la primera); undefined = la respuesta no trae imagenes */
type ConFotos = { images?: { url: string; main?: boolean }[] };
const fotoDe = (p: Product): string | undefined => { const ims = (p as Product & ConFotos).images; return ims ? ims.find((im) => im.main)?.url || ims[0]?.url || "" : undefined; };
function Miniatura({ url, size = 36 }: { url?: string; size?: number }) {
  return url
    ? <img src={url} alt="" loading="lazy" style={{ width: size, height: size, flex: "none", objectFit: "contain", borderRadius: 8, border: "1px solid var(--hair)", background: "#fff" }} />
    : <span aria-hidden style={{ display: "grid", placeItems: "center", width: size, height: size, flex: "none", borderRadius: 8, background: "var(--bg-2)", color: "var(--text-3)" }}><Icon d={I.products} size={Math.round(size * 0.45)} /></span>;
}
/* Precio del producto en la moneda del documento con el tipo de cambio de venta: antes se copiaba el numero tal cual
   (un producto de USD 50 entraba a una factura en colones como 50 colones). Solo CRC y USD en el sistema. */
const inCurrency = (price: number, from: string | null | undefined, to: string | null | undefined, usd: number) => {
  const f = (from || "CRC").toUpperCase(), t = (to || "CRC").toUpperCase();
  if (f === t || !(usd > 0)) return price;
  return f === "USD" ? price * usd : price / usd;
};
type DocState = Partial<Quote & Invoice>;
/* Lo que se manda al guardar (y lo que compara el borrador local). */
const toPayload = (doc: DocState, lines: Line[], asigna = false) => ({
  customer_id: doc.customer_id ?? null, currency: doc.currency || "CRC", discount_type: doc.discount_type || "percent", discount_value: doc.discount_value || "0",
  internal_notes: doc.internal_notes || null, external_notes: doc.external_notes || null, external_order: doc.external_order || null, activity_code: doc.activity_code || null,
  medical_exemption_card: !!doc.medical_exemption_card, issue_date: doc.issue_date || null, due_date: doc.due_date || null,
  lines: lines.filter((l) => l.name.trim()).map((l) => ({ product_id: l.product_id, code: l.code, name: l.name, description: l.description, unit: l.unit, quantity: l.quantity || "1", unit_price: l.unit_price || "0", discount_type: l.discount_type, discount_value: l.discount_value || "0", tax_rate: l.tax_rate || "13", treatment: l.treatment || "normal", supplied_by: l.treatment === "aportado" ? l.supplied_by || null : null })),
  // sin dato (documento nuevo sin elegir) = quien lo crea; la API ignora el campo a quien no puede asignar
  ...(asigna && doc.seller_id !== undefined ? { seller_id: doc.seller_id } : {}),
});
/* Al recuperar un borrador solo vuelve lo que el usuario escribe; numero, estado y versiones son del servidor. */
const GRUPO: Record<string, string> = { FE: "FEC · Facturas electrónicas", TE: "TE · Tiquetes electrónicos", FEE: "FEE · Facturas de exportación", NC: "NC · Notas de crédito" };
const EDITABLE = ["customer_id", "currency", "discount_type", "discount_value", "internal_notes", "external_notes", "external_order", "activity_code", "medical_exemption_card", "issue_date", "due_date"] as const;

/* Pedido 21/10: la factura con numero provisional (BOR-...) no se envia: primero se emite y el cliente ve el fiscal. */
const PROVISIONAL_ENVIO = "Tiene número provisional: emitila ante Hacienda (abajo, en Documentos electrónicos) y después enviala; así el cliente recibe su número fiscal.";

export default function DocEditor({ kind }: { kind: "quote" | "invoice" }) {
  const { id } = useParams();
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  const { toast, me, allows } = useSession();
  const isNew = !id || id === "nueva";
  const isQ = kind === "quote";
  const preCustomer = isNew && search.get("cliente") ? Number(search.get("cliente")) : undefined;
  const [doc, setDoc] = useState<Partial<Quote & Invoice>>({ currency: "CRC", discount_type: "percent", discount_value: "0", status: "creado", lines: [], customer_id: preCustomer });
  // tipo de la factura nueva (FE / TE / FEE); en una ya creada manda el suyo
  const [tipoNuevo, setTipoDoc] = useState("FE");
  const tipoDoc = isNew ? tipoNuevo : (doc.doc_type || "FE");
  const [lines, setLines] = useState<Line[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [prev, setPrev] = useState<Preview | null>(null);
  const [pick, setPick] = useState(false);
  const [kit, setKit] = useState(false); // ventas etapa 3: insertar un kit
  const [emailMsg, setEmailMsg] = useState(""); // ventas etapa 3: texto de la plantilla de correo elegida
  const [busy, setBusy] = useState(false);
  /* tasa vigente (services/fx en la API): con su fecha, su fuente y si tiene mas de 2 dias habiles */
  const [fx, setFx] = useState<FxToday | null>(null);
  const [fxLoaded, setFxLoaded] = useState(false);
  const [limit, setLimit] = useState<{ limit: number; free: boolean } | null>(null);
  const [cargado, setCargado] = useState(isNew); // un documento existente no tiene borrador hasta que llega del servidor
  /* vendedor responsable: lo elige solo quien tiene sales.asignar (el admin); los demas lo ven */
  const asigna = allows("sales.asignar");
  const [sellers, setSellers] = useState<{ id: number; name: string }[]>([]);
  /* actividades economicas de Ajustes -> Hacienda (antes fijas en 6202.0 / 4322.0) y modo fiscal vigente */
  const [acts, setActs] = useState<{ id: number; code: string; description: string | null; is_default: boolean }[]>([]);
  const [fmode, setFmode] = useState<string | null>(null);
  const pending = doc.status === "por_aprobar";
  // grupoB: aceptada por el cliente = bloqueada (se deshace la respuesta en el panel Versiones)
  const accepted = isQ && (doc as Quote).acceptance_status === "aceptada";
  /* emitida (o en camino a Hacienda) o emitida en Fygaro: el comprobante ya existe y la API no deja editarla */
  const emitida = kind === "invoice" && ["pendiente", "aceptada", "emitida_fuera"].includes((doc as Invoice).einvoice_status || "");
  const locked = !isNew && (doc.status === "convertida" || doc.status === "anulada" || accepted || emitida || (kind === "invoice" && ((doc as Invoice).payments?.length ?? 0) > 0));
  /* quien no edita ventas (p. ej. contabilidad: envia y cobra, no crea ventas, plan 1.7) ve el documento sin campos
     editables ni botones que le darian 403; si puede enviar, tiene "Enviar al cliente" sin guardar. Una cotizacion
     la envia solo quien cotiza (crear o editar ventas, como la API: Principal.sends_quotes): contabilidad envia facturas */
  const canEdit = isNew ? allows("sales.crear") : allows("sales.editar");
  const ro = locked || !canEdit;
  const canSend = allows("sales.enviar") && (!isQ || allows("sales.crear") || allows("sales.editar"));

  useEffect(() => {
    api<{ items: Customer[] }>("/customers?limit=100").then((r) => setCustomers(r.items));
    api<{ id: number; code: string; description: string | null; is_default: boolean }[]>("/fiscal/activities").then(setActs).catch(() => setActs([]));
    api<{ mode: string }>("/fiscal/mode").then((r) => setFmode(r.mode)).catch(() => setFmode(null));
    api<FxToday | null>("/fx/today").then((r) => { setFx(r); setFxLoaded(true); }).catch(() => setFxLoaded(true));
    api<{ limit: number; free: boolean }>("/sales/discount-limit").then(setLimit).catch(() => {});
    if (!isNew) api<Quote & Invoice>(`/${isQ ? "quotes" : "invoices"}/${id}`).then(hydrate);
  }, [id, isNew, isQ]);
  useEffect(() => {
    if (asigna) api<{ sellers: { id: number; name: string }[] }>("/opportunities/meta/config").then((r) => setSellers(r.sellers)).catch(() => {});
  }, [asigna]);

  /* Documento recibido de la API -> estado del editor (al abrir y cuando el panel Versiones lo cambia). */
  function hydrate(d: Quote & Invoice) {
    auto.fromServer(); // lo que llega del servidor es la referencia, no un cambio del usuario
    setCargado(true);
    setDoc({ ...d, discount_value: num(d.discount_value) });
    setLines(d.lines.map((l) => ({ ...l, quantity: num(l.quantity), unit_price: num(l.unit_price), discount_value: num(l.discount_value), tax_rate: num(l.tax_rate) })));
  }

  const payload = useMemo(() => toPayload(doc, lines, asigna), [doc, lines, asigna]);

  /* Borrador en este dispositivo (D1/D2): una cotizacion o factura nunca se autoguarda en el servidor (el PUT puede
     dejarla "por aprobar" y queda en la bitacora; la factura es un documento fiscal). Salir de la pagina deja la
     copia y al volver se ofrece recuperarla. Guardar y "Guardar & enviar" pasan por exclusive. */
  const auto = useRecordAutosave<{ doc: DocState; lines: Line[] }>({
    ns: isQ ? "doc-q" : "doc-i",
    mode: "never",
    value: cargado ? { doc, lines } : null,
    idOf: (v) => (v.doc.id as number | undefined) ?? undefined,
    body: (v) => toPayload(v.doc, v.lines, asigna),
    enabled: !ro,
    label: (v) => v.doc.number || (isQ ? "La cotización nueva" : "La factura nueva"),
    apply: (v) => {
      setDoc((cur) => { const next = { ...cur } as Record<string, unknown>; for (const k of EDITABLE) next[k] = (v.doc as Record<string, unknown>)[k]; return next as DocState; });
      setLines(v.lines);
    },
  });

  useEffect(() => {
    if (!isNew && doc.id == null) return; // auditA: sin documento cargado no hay totales que calcular (evita ₡0)
    const t = setTimeout(() => { api<Preview>("/documents/preview", { method: "POST", json: payload }).then(setPrev).catch(() => {}); }, 250);
    return () => clearTimeout(t);
  }, [payload, isNew, doc.id]);

  const setLine = (i: number, patch: Partial<Line>) => setLines((ls) => ls.map((l, k) => (k === i ? { ...l, ...patch } : l)));
  /* Producto del catalogo -> campos de la linea (precio en la moneda del documento). Lo usan el buscador del modal
     y las sugerencias que salen al escribir el concepto: una sola conversion. */
  const fromProduct = (ps: Product[]) => {
    const to = doc.currency || "CRC";
    const usd = Number(to === "USD" ? (doc.fx_sell ?? fx?.sell) : fx?.sell) || 0;
    const otra = ps.filter((p) => (p.currency || "CRC") !== to);
    if (otra.length && !usd) toast("Sin tipo de cambio del día: revisá el precio de los productos en otra moneda", "bad");
    const sin = ps.filter((p) => sinExistencia(p.availability));
    if (sin.length) toast(`${sin.length === 1 ? sin[0].name : `${sin.length} productos`}: sin existencia propia ni del proveedor (se venden sin respaldo)`, "bad");
    const conFoto = ps.filter((p) => fotoDe(p) !== undefined);
    if (conFoto.length) setFotos((f0) => { const n = { ...f0 }; conFoto.forEach((p) => { n[p.id] = fotoDe(p) || ""; }); return n; });
    return ps.map((p) => ({ product_id: p.id, code: p.code, name: p.name, unit: p.unit, unit_price: num(inCurrency(Number(p.price), p.currency, to, usd)), tax_rate: String(p.tax_rate ?? 13) }));
  };
  const addProducts = (ps: Product[]) => {
    const nuevas = fromProduct(ps);
    setLines((ls) => [...ls, ...nuevas.map((x) => ({ ...blankLine(), ...x }))]);
    setPick(false);
  };
  /* Sugerencias del catalogo al escribir el concepto (pedido 21/10). Por que "antes lo hacia y ya no": el que sugeria
     al escribir era Fygaro; en Crimson el concepto de la linea siempre fue un textarea libre (desde la fase 0) y el
     catalogo solo se buscaba desde el boton del modal, asi que al teclear en la linea no salia nada. Ahora, con 2 letras
     o mas busca nombre, codigo, modelo o CABYS; si no hay coincidencia la linea sigue siendo libre y se escribe a mano.
     Solo una eleccion explicita cambia la linea (clic, o flechas + Enter): Tab y Enter sin haber navegado dejan el texto
     tal cual. La linea que ya tiene producto no sugiere (se puede renombrar); si se borra el concepto, vuelve a ser libre. */
  const [sug, setSug] = useState<{ i: number; q: string; items: Product[]; idx: number } | null>(null);
  const sugTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sugSeq = useRef(0);
  const onLineName = (i: number, text: string) => {
    const l = lines[i];
    const libre = !l?.product_id || !text.trim();
    setLine(i, !text.trim() ? { name: text, product_id: null, code: null } : { name: text });
    if (sugTimer.current) clearTimeout(sugTimer.current);
    const q = text.trim();
    if (!libre || q.length < 2) { setSug(null); return; }
    const seq = ++sugSeq.current;
    sugTimer.current = setTimeout(() => {
      api<{ items: Product[] }>(`/products?limit=8&q=${encodeURIComponent(q)}`)
        .then((r) => { if (seq === sugSeq.current) setSug({ i, q, items: r.items, idx: -1 }); })
        .catch(() => { if (seq === sugSeq.current) setSug(null); });
    }, 220);
  };
  /* Cierra la lista e invalida la busqueda pendiente: una respuesta tardia no la reabre sobre una linea sin foco */
  const closeSug = () => {
    if (sugTimer.current) clearTimeout(sugTimer.current);
    sugSeq.current++;
    setSug(null);
  };
  const pickSug = (i: number, p: Product) => {
    const [x] = fromProduct([p]);
    setLine(i, x);
    closeSug();
  };
  const onLineKey = (i: number, e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!sug || sug.i !== i || !sug.items.length) { if (e.key === "Escape" || e.key === "Tab") closeSug(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setSug({ ...sug, idx: Math.min(sug.idx + 1, sug.items.length - 1) }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSug({ ...sug, idx: Math.max(sug.idx - 1, -1) }); }
    else if (e.key === "Enter" && sug.idx >= 0) { e.preventDefault(); pickSug(i, sug.items[sug.idx]); }
    else if (e.key === "Escape" || e.key === "Tab") closeSug(); // Tab pasa el foco con el texto escrito, nunca elige
  };

  /* ?productos=1,2,3: vienen de Productos & Servicios ("Agregar a cotización"). Se agregan una sola vez, cuando el documento
     y el tipo de cambio ya cargaron, igual que desde el buscador; la vendedora revisa y guarda. */
  const traidos = useRef(false);
  /* Miniatura de cada línea con producto (pedido 21/10): reconocimiento visual para la vendedora y el cliente */
  const [fotos, setFotos] = useState<Record<number, string>>({});
  const idsLineas = lines.map((l) => l.product_id).filter((x): x is number => !!x).join(",");
  useEffect(() => {
    const faltan = [...new Set(idsLineas.split(",").filter(Boolean).map(Number))].filter((i) => !(i in fotos));
    if (!faltan.length) return;
    api<{ items: (Product & { images?: { url: string; main?: boolean }[] })[] }>(`/products?ids=${faltan.join(",")}&limit=100`)
      .then((r) => setFotos((f0) => { const n = { ...f0 }; faltan.forEach((i) => { n[i] = ""; }); r.items.forEach((x) => { n[x.id] = (x.images || []).find((im) => im.main)?.url || x.images?.[0]?.url || ""; }); return n; }))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsLineas]);
  const [nuevoCliente, setNuevoCliente] = useState(false);
  useEffect(() => {
    const raw = search.get("productos");
    if (!raw || traidos.current || !cargado || !fxLoaded) return;
    traidos.current = true;
    const ids = raw.split(",").map(Number).filter((n) => n > 0).slice(0, 50);
    const limpiar = () => { const n = new URLSearchParams(search); n.delete("productos"); setSearch(n, { replace: true }); };
    if (locked || !canEdit) { toast("Esta cotización ya no se puede editar: los artículos no se agregaron", "bad"); limpiar(); return; }
    Promise.all(ids.map((i) => api<Product>(`/products/${i}`).catch(() => null)))
      .then((ps) => { const ok = ps.filter((x): x is Product => !!x); if (ok.length) { addProducts(ok); toast(`${ok.length} artículo(s) agregado(s): revisá y guardá`); } })
      .finally(limpiar);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cargado, fxLoaded, search]);

  /* Pedido 21/10: sin cliente no se convierte a factura (la factura no se corrige despues) y la factura hereda el de
     la cotizacion. En una aceptada (bloqueada) el cliente se elige en el momento de convertir. */
  const [clienteFactura, setClienteFactura] = useState(false);
  const SIN_CLIENTE = "Elija el cliente antes de convertir: la factura sale a su nombre y después no se puede editar.";
  const save = useCallback(async (then?: "send" | "convert") => {
    if (payload.lines.length === 0) return toast("Agregue al menos una línea", "bad");
    if (then === "convert" && !payload.customer_id) { toast(SIN_CLIENTE, "bad"); document.getElementById("doc-cliente")?.focus(); return; }
    setBusy(true);
    try {
      const path = isQ ? "quotes" : "invoices";
      const saved = await auto.exclusive({ doc, lines }, () => (isNew ? api<Quote & Invoice>(isQ ? `/${path}` : `/${path}?doc_type=${tipoDoc}`, { method: "POST", json: payload }) : api<Quote & Invoice>(`/${path}/${id}`, { method: "PUT", json: payload })));
      auto.fromServer();
      if (saved.status === "por_aprobar") {
        toast(`Cotización ${saved.number} guardada. El descuento supera tu límite: queda pendiente de aprobación de un administrador.`, "bad");
        nav(`/cotizaciones/${saved.id}`, { replace: true });
        if (!isNew) setDoc(saved);
        return;
      }
      if (then === "send" && !isQ && saved.number_provisional) {
        /* Pedido 21/10: el cliente nunca recibe el numero provisional (cambia al emitir); primero se emite */
        toast(`Factura ${saved.number} guardada. ${PROVISIONAL_ENVIO}`, "bad");
        nav(`/facturas/${saved.id}`, { replace: true });
        setDoc(saved);
        return;
      }
      if (then === "send") {
        /* Solo queda "enviada" (y su oportunidad en "Cotización enviada") si el correo salió de verdad;
           si quedó simulado o con error, se guarda el documento pero se avisa claro que el cliente no lo recibió. */
        const r = await api<{ status: string; sent: boolean; to: string; note: string | null; version?: number | null }>(`/${path}/${saved.id}/email`, { method: "POST", json: isQ && emailMsg ? { message: emailMsg } : {} });
        if (!r.sent) {
          toast(`${isQ ? "Cotización" : "Factura"} ${saved.number} guardada. ${r.note || "El correo no salió."}`, "bad");
          nav(`/${isQ ? "cotizaciones" : "facturas"}/${saved.id}`, { replace: true });
          setDoc(saved);
          return;
        }
        toast(`${isQ ? "Cotización" : "Factura"} ${saved.number} enviada a ${r.to}${r.version ? ` · v${r.version}` : ""}`);
        nav(`/${isQ ? "cotizaciones" : "facturas"}/${saved.id}`, { replace: true });
        setDoc({ ...saved, status: saved.status === "creado" ? "enviada" : saved.status });
        if (isQ) api<Quote & Invoice>(`/quotes/${saved.id}`).then(hydrate).catch(() => {}); // version nueva en el panel
        return;
      }
      if (then === "convert") { const inv = await api<Invoice>(`/quotes/${saved.id}/convert`, { method: "POST" }); toast(`Factura ${inv.number} creada`); nav(`/facturas/${inv.id}`); return; }
      toast(`${isQ ? "Cotización" : "Factura"} ${saved.number} guardada`);
      nav(`/${isQ ? "cotizaciones" : "facturas"}/${saved.id}`, { replace: true });
      if (!isNew) setDoc(saved);
    } catch (e) { toast(e instanceof Error ? e.message : "Error al guardar", "bad"); }
    finally { setBusy(false); }
  }, [payload, isQ, isNew, id, nav, toast, auto, doc, lines, emailMsg]);

  /* Enviar sin guardar: para quien envia pero no edita ventas (contabilidad, plan 1.7). El mismo correo que
     "Guardar & enviar": PDF y enlace de pago; solo queda "enviada" si el correo salio de verdad. */
  const sendOnly = async () => {
    setBusy(true);
    try {
      const r = await api<{ status: string; sent: boolean; to: string; note: string | null; version?: number | null }>(`/${isQ ? "quotes" : "invoices"}/${id}/email`, { method: "POST", json: isQ && emailMsg ? { message: emailMsg } : {} });
      if (!r.sent) { toast(`${isQ ? "Cotización" : "Factura"} ${doc.number}: ${r.note || "el correo no salió."}`, "bad"); return; }
      toast(`${isQ ? "Cotización" : "Factura"} ${doc.number} enviada a ${r.to}${r.version ? ` · v${r.version}` : ""}`);
      if (isQ) api<Quote & Invoice>(`/quotes/${id}`).then(hydrate).catch(() => {});
      else setDoc({ ...doc, status: doc.status === "creado" ? "enviada" : doc.status });
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); }
  };

  /* Aceptada: no se guarda nada antes (esta bloqueada); la API convierte la version aceptada. */
  const convertAccepted = async (customerId?: number) => {
    if (!doc.customer_id && !customerId) { setClienteFactura(true); return; }
    setBusy(true);
    try {
      const inv = await api<Invoice>(`/quotes/${id}/convert`, { method: "POST", json: customerId ? { customer_id: customerId } : {} });
      toast(`Factura ${inv.number} creada con la v${(doc as Quote).accepted_version}`); nav(`/facturas/${inv.id}`);
    }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
    finally { setBusy(false); setClienteFactura(false); }
  };

  const act = async (action: "void" | "duplicate") => {
    const path = isQ ? "quotes" : "invoices";
    if (action === "void" && !confirm(`¿Anular ${isQ ? "la cotización" : "la factura"} ${doc.number}? Esta acción no se deshace.`)) return;
    try {
      const r = await api<Quote>(`/${path}/${id}/${action}`, { method: "POST" });
      if (action === "duplicate") { toast(`Duplicada como ${r.number}`); nav(`/cotizaciones/${r.id}`); } else { setDoc(r); toast("Documento anulado"); }
    } catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  const approve = async () => {
    try { const r = await api<Quote>(`/quotes/${id}/approve`, { method: "POST" }); setDoc({ ...doc, status: r.status, rejection: null }); toast("Descuento aprobado: el vendedor ya puede enviarla o convertirla"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };
  /* Rechazar: sigue retenida y vuelve a la vendedora con el motivo (le llega por correo). Si la vuelve a guardar fuera
     del limite, pide aprobacion otra vez. */
  const reject = async () => {
    const motivo = (prompt("¿Por qué no se aprueba? La vendedora recibe este motivo por correo.") || "").trim();
    if (!motivo) return;
    if (motivo.length < 3) return toast("Escriba un motivo un poco más claro", "bad");
    try { const r = await api<Quote>(`/quotes/${id}/reject`, { method: "POST", json: { reason: motivo } }); setDoc((d) => ({ ...d, rejection: r.rejection ?? null })); toast("Descuento rechazado: la vendedora ya recibió el motivo"); }
    catch (e) { toast(e instanceof Error ? e.message : "Error", "bad"); }
  };

  /* Pendientes y lineas en 0 sin explicar: se guarda, pero no se envia ni se convierte (la API responde 409). */
  const named = lines.filter((l) => l.name.trim());
  const pendLines = named.filter((l) => l.treatment === "pendiente");
  const zeroLines = named.filter(zeroUnexplained);
  const blocked = isQ && (pendLines.length > 0 || zeroLines.length > 0);

  const cur = doc.currency || "CRC";
  const provisional = !isQ && !isNew && !!(doc as Invoice).number_provisional;
  const title = isNew ? (isQ ? "Nueva cotización" : "Nueva factura") : `${isQ ? "Cotización" : "Factura"}: ${doc.number}`;

  /* Cotización aprobada -> proyecto: copia líneas, costo estimado y crea la primera orden de trabajo. */
  const opp = isQ ? (doc as Quote).opportunity : null;
  const proyecto = isQ && (doc as Quote).project_id ? { id: (doc as Quote).project_id as number, number: (doc as Quote).project_number || "el proyecto" } : null;
  const rechazo = isQ && pending ? (doc as Quote).rejection ?? null : null;
  const parties = opp?.parties ?? [];
  const toProject = async () => {
    if (!id) return;
    // oportunidad de solo venta: el proyecto se crea solo si se confirma que si lleva instalacion
    const force = opp?.kind === "venta";
    if (force && !confirm(`La oportunidad ${opp!.number} es de solo venta (sin instalación). ¿Crear el proyecto de todas formas?`)) return;
    try {
      const pr = await api<{ id: number; number: string }>(`/quotes/${id}/project`, { method: "POST", json: force ? { force: true } : {} });
      toast(`Proyecto ${pr.number} creado con su primera orden de trabajo`);
      nav(`/proyectos/${pr.id}`);
    } catch (e) { toast(e instanceof Error ? e.message : "No se pudo crear el proyecto", "bad"); }
  };

  // auditA: mientras llega el documento y sus totales, "Cargando…" en vez de "undefined" y total ₡0
  if (!isNew && (doc.id == null || !prev)) return <Loading />;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="status-line"><span>{isQ ? "02 · Cotización" : "03 · Factura"}</span>{doc.status && <Badge status={doc.status} />}{(doc as Invoice).consecutive && <span>· {(doc as Invoice).consecutive}</span>}</div>
          <h1 className="h1" style={{ marginTop: 6 }}>{title}</h1>
          {provisional && (
            <div className="status-line" style={{ marginTop: 6 }}>
              <span className="badge badge--warn" title="El número fiscal se asigna al emitir: si otra factura se emite antes, toma el siguiente.">Número provisional · toma {(doc as Invoice).expected_number || "su número fiscal"} al emitir</span>
              <span className="muted">No se envía ni se comparte el enlace de pago hasta emitirla: el cliente solo ve el número fiscal.</span>
            </div>
          )}
        </div>
        <div className="page-head__actions">
          {!isNew && <AuthLink path={`/${isQ ? "quotes" : "invoices"}/${id}/pdf`}>PDF / Imprimir</AuthLink>}
          {!isNew && isQ && allows("sales.crear") && <button className="btn btn--ghost btn--sm" onClick={() => act("duplicate")}>Duplicar</button>}
        </div>
      </div>

      <div className="doc">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card"><div className="card__body grid-3">
            <Field label="Cliente">
              <div style={{ display: "flex", gap: 6 }}>
              <select
                id="doc-cliente"
                className="select"
                style={{ flex: 1, minWidth: 0 }}
                value={doc.customer_id ?? ""}
                disabled={ro}
                onChange={(e) => {
                  /* La divisa la manda el cliente: si prefiere dolares, el documento arranca en dolares
                     (pedido de Andres en la revision del 22/09). Se puede cambiar a mano despues. */
                  const id = e.target.value ? Number(e.target.value) : null;
                  const c = customers.find((x) => x.id === id);
                  setDoc({ ...doc, customer_id: id, currency: c?.currency || doc.currency || "CRC" });
                }}
              >
                <option value="">Seleccione…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              {!ro && allows("crm.crear") && <button type="button" className="btn btn--soft btn--sm" title="Crear un cliente sin salir de la cotización" onClick={() => setNuevoCliente(true)}><Icon d={I.plus} />Nuevo</button>}
              </div>
            </Field>
            <Field label="Divisa" hint={doc.customer_id && customers.find((c) => c.id === doc.customer_id)?.currency !== cur ? `El cliente prefiere ${customers.find((c) => c.id === doc.customer_id)?.currency}; esta cambiada a mano.` : fx ? `Tipo de cambio (${fx.source_label}, ${fmtDate(fx.date)}): venta ${fx.sell} · compra ${fx.buy}${fx.stale ? " · tasa vieja" : ""}` : undefined}>
              <select className="select" value={cur} disabled={ro} onChange={(e) => setDoc({ ...doc, currency: e.target.value })}><option>CRC</option><option>USD</option></select>
            </Field>
            {/* Tipo y grupo (30/10, Andres): un tiquete va en su grupo TE, no en el de facturas. Al crear se elige el tipo;
                despues queda fijo (es el consecutivo de Hacienda). */}
            {!isQ && (
              <Field label="Tipo de documento" hint={tipoDoc === "TE" ? "Tiquete: para consumidor final, no requiere cédula del cliente." : tipoDoc === "FE" ? "Factura: el cliente necesita cédula y correo para Hacienda." : undefined}>
                <select className="select" value={tipoDoc} disabled={!isNew || ro} onChange={(e) => setTipoDoc(e.target.value)}>
                  <option value="FE">Factura electrónica</option>
                  <option value="TE">Tiquete electrónico</option>
                  <option value="FEE">Factura electrónica de exportación</option>
                </select>
              </Field>
            )}
            <Field label="Grupo de facturación"><select className="select" disabled><option>{isQ ? "COT · Cotizaciones" : GRUPO[tipoDoc] || `${tipoDoc}`}</option></select></Field>
          </div></div>

          <div className="card">
            <div className="card__head"><h3 className="h3">Productos & Servicios</h3><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{isQ && <button className="btn btn--ghost btn--sm" disabled={ro} onClick={() => setKit(true)}><Icon d={I.box} />Insertar kit</button>}<button className="btn btn--soft btn--sm" disabled={ro} onClick={() => setPick(true)}><Icon d={I.plus} />Agregar Productos & Servicios</button></div></div>
            <div className="card__body" style={{ padding: 0 }}>
              <table className="lines">
                <thead><tr><th>Concepto</th><th style={{ width: 118 }}>Cant. · unidad</th><th style={{ width: 128 }}>Precio</th><th style={{ width: 72 }}>Desc. %</th><th style={{ width: 82 }}>IVA</th><th className="num" style={{ width: 120 }}>Subtotal</th><th style={{ width: 34 }} /></tr></thead>
                <tbody>
                  {lines.map((l, i) => {
                    const t = (l.treatment || "normal") as Treatment;
                    const nosuma = NO_SUMA.includes(t);
                    const zero = zeroUnexplained(l) && l.name.trim() !== "";
                    return (
                      <tr key={i} className="line">
                        <td className="c-name">
                          {l.product_id && fotos[l.product_id] ? <img src={fotos[l.product_id]} alt="" loading="lazy" style={{ float: "left", width: 44, height: 44, objectFit: "contain", borderRadius: 8, border: "1px solid var(--hair)", background: "#fff", marginRight: 8 }} /> : null}
                          <div className="lookup">
                            <textarea className="input line-name" rows={nameRows(l.name)} placeholder={l.product_id ? "Nombre del producto o servicio" : "Escriba para buscar en el catálogo o una línea libre"} value={l.name} disabled={ro}
                              aria-autocomplete="list" aria-expanded={sug?.i === i && sug.items.length > 0}
                              onChange={(e) => onLineName(i, e.target.value)} onKeyDown={(e) => onLineKey(i, e)} onBlur={closeSug} />
                            {sug?.i === i && !ro && (
                              <ul className="lookup__list line-sug" role="listbox">
                                {sug.items.map((p, k) => (
                                  <li key={p.id} role="option" aria-selected={k === sug.idx}>
                                    <button type="button" className={k === sug.idx ? "is-active" : undefined} onMouseDown={(e) => e.preventDefault()} onClick={() => pickSug(i, p)}>
                                      <Miniatura url={fotoDe(p)} size={34} />
                                      <span style={{ minWidth: 0 }}><b>{p.name}</b><small>{p.code}{p.price != null ? ` · ${fmtMoney(p.price, p.currency)}` : ""}{p.item_type === "producto" && p.availability ? ` · ${availText(p.availability)}` : ""}</small></span>
                                    </button>
                                  </li>
                                ))}
                                {sug.items.length === 0 && <li className="line-sug__none">Sin coincidencias en el catálogo: seguí escribiendo la línea a mano.</li>}
                              </ul>
                            )}
                          </div>
                          <textarea className="line-desc" rows={1} placeholder="Descripción (click para editar)" value={l.description || ""} disabled={ro} onChange={(e) => setLine(i, { description: e.target.value })} />
                          <div className="line-meta">
                            <select className={`select select--sm line-treat${zero ? " is-warn" : ""}${t !== "normal" ? " is-set" : ""}`} value={t} disabled={ro} title={TREATMENTS.find((x) => x.value === t)?.hint} onChange={(e) => setLine(i, { treatment: e.target.value })} aria-label="Tratamiento de la línea">
                              {TREATMENTS.map((x) => <option key={x.value} value={x.value}>{x.value === "normal" ? "Tratamiento: normal" : x.label}</option>)}
                            </select>
                            {/* grupo B: quién aporta la línea (empresas participantes de la oportunidad ligada) */}
                            {t === "aportado" && (parties.length > 0 || l.supplied_by) && (
                              <select className="select select--sm line-treat is-set" value={l.supplied_by || ""} disabled={ro} onChange={(e) => setLine(i, { supplied_by: e.target.value || null })} aria-label="Aportado por">
                                <option value="">Aportado por: cliente/aliado</option>
                                {l.supplied_by && !parties.some((x) => x.name === l.supplied_by) && <option value={l.supplied_by}>Aportado por {l.supplied_by}</option>}
                                {parties.map((x) => <option key={x.name} value={x.name}>Aportado por {x.name}</option>)}
                              </select>
                            )}
                            {l.code && <span className="meta">{l.code}</span>}
                          </div>
                          {zero && <div className="line-warn">Línea en {fmtMoney(0, cur)}: elija si es pendiente, aportada, cortesía o exclusión.</div>}
                          {t !== "normal" && <div className="line-note">{TREATMENTS.find((x) => x.value === t)?.hint}</div>}
                        </td>
                        <td className="c-qty" data-label="Cant. · unidad">
                          <input className="input input--mono" inputMode="decimal" value={l.quantity} disabled={ro} onChange={(e) => setLine(i, { quantity: e.target.value })} />
                          <select className="select select--sm line-unit" value={l.unit} disabled={ro} onChange={(e) => setLine(i, { unit: e.target.value })} aria-label="Unidad" title={unitOptions(l.unit).find((u) => u.value === l.unit)?.label}>{unitOptions(l.unit).map((u) => <option key={u.value} value={u.value}>{u.short}</option>)}</select>
                        </td>
                        <td className="c-price" data-label="Precio"><input className="input input--mono line-price" inputMode="decimal" value={l.unit_price} disabled={ro} onChange={(e) => setLine(i, { unit_price: e.target.value })} /></td>
                        <td className="c-disc" data-label="Desc. %"><input className="input input--mono" inputMode="decimal" value={l.discount_value} disabled={ro} onChange={(e) => setLine(i, { discount_value: e.target.value })} /></td>
                        <td className="c-tax" data-label="IVA"><select className="select" value={l.tax_rate} disabled={ro} onChange={(e) => setLine(i, { tax_rate: e.target.value })}>{["13", "4", "2", "1", "0"].map((r) => <option key={r} value={r}>{r}%</option>)}</select></td>
                        <td className="c-sub num money" data-label="Subtotal">{nosuma ? <span className="muted">No suma</span> : prev?.lines[i] ? fmtMoney(prev.lines[i].subtotal, cur) : "—"}</td>
                        <td className="c-x">{!ro && <button className="x" onClick={() => { closeSug(); setLines((ls) => ls.filter((_, k) => k !== i)); }} aria-label="Eliminar"><Icon d={I.x} size={14} /></button>}</td>
                      </tr>
                    );
                  })}
                  {lines.length === 0 && <tr><td colSpan={7}><div className="empty" style={{ padding: 28 }}><span className="meta">Sin líneas</span><div className="h3">{ro ? "El documento no tiene líneas" : "Agregue productos o escriba una línea libre"}</div>{!ro && <button className="btn btn--ghost btn--sm" onClick={() => setLines([blankLine()])}>Línea libre</button>}</div></td></tr>}
                </tbody>
              </table>
              {lines.length > 0 && !ro && <div style={{ padding: 10 }}><button className="btn btn--ghost btn--sm" onClick={() => setLines((ls) => [...ls, blankLine()])}><Icon d={I.plus} />Línea libre</button></div>}
            </div>
          </div>

          <div className="grid-2">
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Field label="Notas internas" hint="No salen en el documento."><textarea className="textarea" value={doc.internal_notes || ""} disabled={ro} onChange={(e) => setDoc({ ...doc, internal_notes: e.target.value })} /></Field>
              <Field label="Notas externas" hint="Se imprimen y se envían al cliente."><textarea className="textarea" value={doc.external_notes || ""} disabled={ro} onChange={(e) => setDoc({ ...doc, external_notes: e.target.value })} /></Field>
            </div></div>
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="meta">Otros ajustes</div>
              <Field label="Orden externa #"><input className="input" value={doc.external_order || ""} disabled={ro} onChange={(e) => setDoc({ ...doc, external_order: e.target.value })} /></Field>
              {/* Campos fiscales: solo para quien gestiona la facturación (admin/contabilidad); el vendedor no los necesita */}
              {allows("sales.ver_todo") && <Field label="Código de actividad" hint="Vacío = la actividad por defecto de Ajustes → Hacienda."><select className="select" value={doc.activity_code || ""} disabled={ro} onChange={(e) => setDoc({ ...doc, activity_code: e.target.value })}><option value="">Por defecto{acts.find((a) => a.is_default) ? ` (${acts.find((a) => a.is_default)!.code})` : ""}</option>{acts.map((a) => <option key={a.id} value={a.code}>{a.code}{a.description ? ` · ${a.description}` : ""}</option>)}{doc.activity_code && !acts.some((a) => a.code === doc.activity_code) && <option value={doc.activity_code}>{doc.activity_code}</option>}</select></Field>}
              {!isQ && (fmode === "pruebas" || fmode === "produccion" || ((doc as Invoice).einvoice_status && (doc as Invoice).einvoice_status !== "sin_emitir")) && (
                <Field label="Fecha" hint="La fecha la pone la emisión (Hacienda no admite fechas anteriores ni posteriores)."><div className="input" style={{ display: "flex", alignItems: "center", background: "var(--bg-2)" }}>{doc.issue_date || "al emitir"}</div></Field>
              )}
              {/* La exoneracion medica no aplica a las cotizaciones de Crimson (pedido de Andres); el campo es compartido y sigue en facturas. Solo para quien factura. */}
              {!isQ && allows("sales.ver_todo") && <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}><input type="checkbox" checked={!!doc.medical_exemption_card} disabled={ro} onChange={(e) => setDoc({ ...doc, medical_exemption_card: e.target.checked })} />Exoneración médica IVA (tarjeta de crédito)</label>}
            </div></div>
          </div>
        </div>

        <aside className="doc__side">
          <div className="card"><div className="card__body">
            <div className="meta" style={{ marginBottom: 10 }}>Totales · {cur}</div>
            <div className="totals">
              <div><span>Subtotal</span><span className="money">{fmtMoney(prev?.subtotal, cur)}</span></div>
              <div><span>Descuento</span><span className="money">−{fmtMoney(prev?.discount_total, cur)}</span></div>
              <div><span>Impuestos</span><span className="money">{fmtMoney(prev?.tax_total, cur)}</span></div>
              <div className="total"><span>Total</span><span className="money">{fmtMoney(prev?.total, cur)}</span></div>
              {kind === "invoice" && !isNew && <div className="total" style={{ color: "var(--crimson)" }}><span>Saldo</span><span className="money">{fmtMoney((doc as Invoice).balance, cur)}</span></div>}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 14 }}>
              <Field label="Descuento global"><select className="select" value={doc.discount_type} disabled={ro} onChange={(e) => setDoc({ ...doc, discount_type: e.target.value })}><option value="percent">%</option><option value="amount">Monto</option></select></Field>
              <Field label="Valor"><input className="input input--mono" value={doc.discount_value ?? "0"} disabled={ro} onChange={(e) => setDoc({ ...doc, discount_value: e.target.value })} /></Field>
            </div>
          </div></div>

          {(!isNew || asigna) && (
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="meta">Vendedor</div>
              {asigna && !locked ? (
                <select className="select" aria-label="Vendedor responsable" value={doc.seller_id == null ? "" : String(doc.seller_id)}
                  onChange={(e) => setDoc({ ...doc, seller_id: e.target.value ? Number(e.target.value) : isNew ? undefined : null })}>
                  <option value="">{isNew ? "Yo (quien la crea)" : "Sin vendedor (no comisiona)"}</option>
                  {doc.seller_id != null && !sellers.some((s) => s.id === doc.seller_id) && <option value={doc.seller_id}>{doc.seller_name || `Usuario ${doc.seller_id}`}</option>}
                  {sellers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              ) : <b style={{ fontSize: 14 }}>{doc.seller_name || "—"}</b>}
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>De quién es la venta: quién la ve y a quién se le comisiona.{asigna && !locked ? " «Sin vendedor» no le comisiona a nadie; quien la tecleó la sigue viendo." : ""}{asigna && !isQ && !locked ? " En una factura con pagos ya no cambia." : ""}</p>
            </div></div>
          )}
          {isQ && !isNew && doc.id != null && <QuoteVersions quote={doc as Quote} onQuote={(q) => hydrate(q as Quote & Invoice)} />}
          {pending && (
            <div className="card" style={{ borderColor: rechazo ? "var(--bad)" : "var(--warn)" }}><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div className="meta" style={{ color: rechazo ? "var(--bad)" : "var(--warn)" }}>{rechazo ? "Aprobación rechazada" : "Por aprobar"}</div>
              {rechazo ? (
                <>
                  <p style={{ fontSize: 13, margin: 0 }}>{rechazo.by || "Administración"} no lo aprobó{rechazo.reason ? `: «${rechazo.reason}»` : ""}.</p>
                  <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Ajustá el descuento o los precios y guardala: si queda dentro del límite se libera; si no, vuelve a pedir aprobación.</p>
                </>
              ) : (
                /* el motivo real que guardo la API (descuento, monto, margen o linea sin producto del catalogo): antes siempre
                   decia "descuento" y una linea libre de instalacion parecia un descuento que nadie habia puesto (26/10) */
                <>
                  <p style={{ fontSize: 13, margin: 0 }}>{(doc as { approval_reason?: string | null }).approval_reason || `El descuento supera el límite del vendedor${limit ? ` (${Number(limit.limit)} %)` : ""}.`} No se puede enviar ni convertir hasta que un administrador la apruebe.</p>
                  {/sin producto del cat|no tiene producto del cat/i.test((doc as { approval_reason?: string | null }).approval_reason || "") && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>Para que no pida aprobación, elegí el servicio desde «Agregar Productos & Servicios» en vez de escribir la línea a mano.</p>}
                </>
              )}
              {/* jefe de ventas (ventas etapa 2): aprueba solo lo que cabe en sus topes; lo demás lo dice y va a administración */}
              {!allows("sales.aprobar") && doc.team_approval && !doc.team_approval.can && <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>{doc.team_approval.reason}</p>}
              {(allows("sales.aprobar") || !!doc.team_approval?.can) && <button className="btn btn--crimson" onClick={approve}><Icon d={I.check} />Aprobar descuento</button>}
              {(allows("sales.aprobar") || !!doc.team_approval?.can) && !rechazo && <button className="btn btn--ghost btn--sm" onClick={reject}>Rechazar con un motivo</button>}
            </div></div>
          )}
          {!locked && blocked && (
            <div className="card" style={{ borderColor: "var(--warn)" }}><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="meta" style={{ color: "var(--warn)" }}>No se puede enviar ni convertir todavía</div>
              {pendLines.length > 0 && <p style={{ fontSize: 13, margin: 0 }}>{pendLines.length} línea(s) pendientes de costo o precio del proveedor: {pendLines.slice(0, 4).map((l) => l.name).join(", ")}{pendLines.length > 4 ? "…" : ""}.</p>}
              {zeroLines.length > 0 && <p style={{ fontSize: 13, margin: 0 }}>{zeroLines.length} línea(s) en {fmtMoney(0, cur)} sin tratamiento: {zeroLines.slice(0, 4).map((l) => l.name).join(", ")}{zeroLines.length > 4 ? "…" : ""}. Elija si son pendientes, aportadas, cortesía o exclusiones.</p>}
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>Puede guardarla mientras tanto.</p>
            </div></div>
          )}
          {(!locked || (accepted && doc.status !== "convertida" && doc.status !== "anulada")) && (canEdit || canSend || allows("sales.crear") || allows("projects.crear") || allows("sales.anular")) && (
            <div className="card"><div className="card__body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {canEdit && limit && !limit.free && !accepted && <p className="muted" style={{ fontSize: 12, margin: 0 }}>Tu descuento máximo es {Number(limit.limit)} % (incluye rebajas al precio de catálogo). Más que eso requiere aprobación.</p>}
              {canEdit && <div>{auto.bar}</div>}
              {canEdit && !accepted && <button className="btn btn--crimson" disabled={busy} onClick={() => save()}><Icon d={I.check} />Guardar</button>}
              {!pending && !accepted && canSend && (canEdit
                ? <button className="btn" disabled={busy || blocked || provisional} title={provisional ? PROVISIONAL_ENVIO : undefined} onClick={() => save("send")}><Icon d={I.mail} />Guardar & enviar al cliente</button>
                : !isNew && <button className="btn" disabled={busy || blocked || provisional} title={provisional ? PROVISIONAL_ENVIO : undefined} onClick={sendOnly}><Icon d={I.mail} />Enviar al cliente</button>)}
              {isQ && !pending && !accepted && !isNew && canSend && doc.status !== "por_aprobar" && <QuoteShare quote={doc} emailTpl={emailMsg} setEmailTpl={setEmailMsg} onSent={() => api<Quote & Invoice>(`/quotes/${id}`).then(hydrate).catch(() => {})} />}
              {!isQ && !pending && !accepted && !isNew && (() => {
                const cust = customers.find((c) => c.id === doc.customer_id);
                const rawPhone = cust?.whatsapp || cust?.phone || null;
                if (!rawPhone) return null;
                const digits = rawPhone.replace(/\D/g, "");
                const wa = digits.length === 8 ? `506${digits}` : digits;
                const docLabel = isQ ? `cotización ${doc.number ?? ""}` : `factura ${doc.number ?? ""}`;
                const text = encodeURIComponent(`Hola ${cust?.name ?? ""}, le comparte ${me?.tenant.name ?? ""}: ${docLabel}. Cualquier consulta con gusto le atendemos.`);
                return <a className="btn btn--soft" href={`https://wa.me/${wa}?text=${text}`} target="_blank" rel="noreferrer"><Icon d={I.whatsapp} />Enviar por WhatsApp</a>;
              })()}
              {!pending && !accepted && canSend && provisional && <p className="muted" style={{ fontSize: 12, margin: 0, color: "var(--warn)" }}>{PROVISIONAL_ENVIO}</p>}
              {isQ && !pending && allows("sales.crear") && (accepted || canEdit) && <button className="btn btn--soft" disabled={busy || blocked} title={doc.customer_id ? undefined : "Primero elija el cliente"} onClick={() => (accepted ? convertAccepted() : save("convert"))}><Icon d={I.invoice} />Convertir a factura</button>}
              {isQ && !pending && allows("sales.crear") && (accepted || canEdit) && !doc.customer_id && <p className="muted" style={{ fontSize: 12, margin: 0, color: "var(--warn)" }}>Sin cliente no se convierte a factura: la factura sale a su nombre y después no se edita.</p>}
              {isQ && !isNew && !pending && !proyecto && allows("projects.crear") && (
                /* Instalaciones: la cotización aprobada arranca el proyecto con su primera orden de trabajo. */
                opp?.kind === "venta"
                  ? <button className="btn btn--ghost btn--sm" disabled={busy} onClick={toProject} title={`La oportunidad ${opp.number} es de solo venta`}>¿Lleva instalación? Crear proyecto</button>
                  : <button className="btn btn--soft" disabled={busy} onClick={toProject}><Icon d={I.box} />Convertir a proyecto</button>
              )}
              {!isNew && allows("sales.anular") && <button className="btn btn--danger" disabled={busy} onClick={() => act("void")}>Anular {isQ ? "cotización" : "factura"}</button>}
            </div></div>
          )}
          {proyecto && (
            /* ya tiene proyecto: se va a el en vez de intentar crearlo otra vez */
            <div className="card"><div className="card__body">
              <p className="muted" style={{ fontSize: 13, margin: 0 }}>Esta cotización ya es el proyecto {proyecto.number}.</p>
              {allows("projects.ver") && <button className="btn btn--soft" style={{ marginTop: 10, width: "100%" }} onClick={() => nav(`/proyectos/${proyecto.id}`)}><Icon d={I.box} />Ver {proyecto.number} <Icon d={I.arrow} /></button>}
            </div></div>
          )}
          {locked && doc.status === "convertida" && (doc as Quote).converted_invoice_id && (
            <div className="card"><div className="card__body"><p className="muted" style={{ fontSize: 13 }}>Convertida a factura.</p><button className="btn btn--soft" style={{ marginTop: 10, width: "100%" }} onClick={() => nav(`/facturas/${(doc as Quote).converted_invoice_id}`)}>Ver factura <Icon d={I.arrow} /></button></div></div>
          )}
          <div className="card"><div className="card__body">
            <div className="meta" style={{ marginBottom: 8 }}>Tipos de cambio</div>
            <div className="totals"><div><span>Venta</span><span className="money">{cur === "CRC" ? "—" : num(doc.fx_sell ?? fx?.sell ?? "")}</span></div><div><span>Compra</span><span className="money">{cur === "CRC" ? "—" : num(doc.fx_buy ?? fx?.buy ?? "")}</span></div></div>
            <FxSource fx={fx} loaded={fxLoaded} cur={cur} docFx={isNew ? null : doc.fx_sell} base={me?.tenant.default_currency} />
          </div></div>
        </aside>
      </div>

      {nuevoCliente && <NuevoCliente onClose={() => setNuevoCliente(false)} onCreated={(c) => { setCustomers((cs) => [c, ...cs.filter((x) => x.id !== c.id)]); setDoc({ ...doc, customer_id: c.id, currency: c.currency || doc.currency || "CRC" }); setNuevoCliente(false); }} />}
      {clienteFactura && <ClienteParaFactura customers={customers} busy={busy} onClose={() => setClienteFactura(false)} onCreated={(c) => setCustomers((cs) => [c, ...cs.filter((x) => x.id !== c.id)])} onPick={(cid) => convertAccepted(cid)} />}
      {pick && <ProductPicker onClose={() => setPick(false)} onPick={addProducts} docCurrency={cur} />}
      {kit && <KitPicker currency={cur} onClose={() => setKit(false)} onPick={(ls) => { setLines((x) => [...x, ...ls.map((l) => ({ ...blankLine(), ...l }) as Line)]); setKit(false); }} />}
    </>
  );
}

/* Cotizacion aceptada sin cliente (ya no se edita): el cliente de la factura se elige al convertir. */
function ClienteParaFactura({ customers, busy, onClose, onPick, onCreated }: { customers: Customer[]; busy: boolean; onClose: () => void; onPick: (id: number) => void; onCreated: (c: Customer) => void }) {
  const { allows } = useSession();
  const [cid, setCid] = useState<number | null>(null);
  const [nuevo, setNuevo] = useState(false);
  if (nuevo) return <NuevoCliente onClose={() => setNuevo(false)} onCreated={(c) => { onCreated(c); setCid(c.id); setNuevo(false); }} />;
  return (
    <Modal title="¿A nombre de quién sale la factura?" onClose={onClose} foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button><button className="btn btn--crimson" disabled={busy || !cid} onClick={() => cid && onPick(cid)}><Icon d={I.invoice} />Convertir a factura</button></>}>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>La cotización no tiene cliente. La factura sale a su nombre y después no se puede editar: elegilo antes de convertir.</p>
      <Field label="Cliente">
        <div style={{ display: "flex", gap: 6 }}>
          <select className="select" style={{ flex: 1, minWidth: 0 }} autoFocus value={cid ?? ""} onChange={(e) => setCid(e.target.value ? Number(e.target.value) : null)}>
            <option value="">Seleccione…</option>{customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {allows("crm.crear") && <button type="button" className="btn btn--soft btn--sm" onClick={() => setNuevo(true)}><Icon d={I.plus} />Nuevo</button>}
        </div>
      </Field>
    </Modal>
  );
}

function ProductPicker({ onClose, onPick, docCurrency }: { onClose: () => void; onPick: (ps: Product[]) => void; docCurrency: string }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Product[]>([]);
  const [sel, setSel] = useState<Record<number, Product>>({});
  const [cursor, setCursor] = useState<number | null>(null);
  const load = useCallback(async (append = false, c: number | null = null) => {
    const qs = new URLSearchParams({ limit: "12" }); if (q) qs.set("q", q); if (c) qs.set("cursor", String(c));
    const r = await api<{ items: Product[]; next_cursor: number | null }>(`/products?${qs}`);
    setItems((p) => (append ? [...p, ...r.items] : r.items)); setCursor(r.next_cursor);
  }, [q]);
  useEffect(() => { const t = setTimeout(() => load(), 200); return () => clearTimeout(t); }, [load]);
  const toggle = (p: Product) => setSel((s) => { const c = { ...s }; if (c[p.id]) delete c[p.id]; else c[p.id] = p; return c; });
  const n = Object.keys(sel).length;
  const sinRespaldo = Object.values(sel).filter((p) => sinExistencia(p.availability)).length;
  return (
    <Modal title="Productos & Servicios" onClose={onClose} wide foot={<><button className="btn btn--ghost" onClick={onClose}>Cancelar</button>{sinRespaldo > 0 && <span className="muted" style={{ fontSize: 12.5, color: "var(--warn)", marginRight: "auto" }}>{sinRespaldo} sin existencia propia ni del proveedor</span>}<button className="btn btn--crimson" disabled={!n} onClick={() => onPick(Object.values(sel))}>Seleccionar{n ? ` (${n})` : ""}</button></>}>
      <div className="search" style={{ maxWidth: "none" }}><Icon d={I.search} size={16} /><input autoFocus placeholder="Buscar por nombre, código o CABYS…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      {items.length === 0 ? <><Empty hint="Cree el producto desde Productos & Servicios." />{q.trim().length >= 3 && <EnProveedor q={q.trim()} onCreated={(code) => setQ(code)} />}</> : (<>
        <table className="table pick-table">
          <thead><tr><th /><th style={{ width: 52 }} /><th>Código</th><th>Nombre</th><th>Disponibilidad</th><th className="num">Precio</th><th>IVA</th></tr></thead>
          <tbody>{items.map((p) => (
            <tr key={p.id} onClick={() => toggle(p)} style={{ cursor: "pointer" }}>
              <td><input type="checkbox" readOnly checked={!!sel[p.id]} /></td><td><Miniatura url={fotoDe(p)} size={40} /></td><td className="mono muted">{p.code}</td><td style={{ fontWeight: 600 }}>{p.name}<div className="meta" style={{ marginTop: 2 }}>{p.item_type}{p.cabys_code ? ` · CABYS ${p.cabys_code}` : ""}</div></td><td style={{ minWidth: 150 }}>{p.item_type === "producto" ? <><AvailBadge a={p.availability} /><div className="meta">{availText(p.availability)}</div></> : <span className="muted">—</span>}</td><td className="num money">{fmtMoney(p.price, p.currency)}{(p.currency || "CRC") !== docCurrency && <div className="meta">se convierte a {docCurrency}</div>}</td><td className="muted">{p.tax_rate ?? 13}%</td>
            </tr>
          ))}</tbody>
        </table>
        {/* en el celular la tabla de 7 columnas no cabe: lista con foto, nombre completo, precio y existencia */}
        <ul className="pick-list">{items.map((p) => (
          <li key={p.id}>
            <button type="button" className={`pick-item ${sel[p.id] ? "is-sel" : ""}`} aria-pressed={!!sel[p.id]} onClick={() => toggle(p)}>
              <Miniatura url={fotoDe(p)} size={52} />
              <span className="pick-item__main">
                <b>{p.name}</b>
                <span className="mono meta">{p.code}{p.cabys_code ? ` · CABYS ${p.cabys_code}` : ""}</span>
                <span className="pick-item__row">
                  <span className="money">{fmtMoney(p.price, p.currency)}</span>
                  <span className="muted">IVA {p.tax_rate ?? 13}%</span>
                  {p.item_type === "producto" ? <AvailBadge a={p.availability} /> : <span className="muted">{p.item_type}</span>}
                </span>
                {p.item_type === "producto" && <span className="meta">{availText(p.availability)}</span>}
                {(p.currency || "CRC") !== docCurrency && <span className="meta">se convierte a {docCurrency}</span>}
              </span>
              <span className="pick-item__check" aria-hidden><Icon d={I.check} size={16} /></span>
            </button>
          </li>
        ))}</ul>
      </>)}
      {cursor && <button className="btn btn--soft btn--sm" style={{ alignSelf: "center" }} onClick={() => load(true, cursor)}>Más resultados</button>}
    </Modal>
  );
}

// Empty local para el picker (evita importar el de components por el estilo compacto)
function Empty({ hint }: { hint: string }) { return <div className="empty" style={{ padding: 24 }}><span className="meta">Sin resultados</span><p className="muted">{hint}</p></div>; }

type FxToday = { sell: string; buy: string; date: string; source: string; source_label: string; stale: boolean; business_days_old: number };

/* De donde sale la tasa (plan 1.12): fecha y fuente reales (BCCR o manual), aviso si esta vieja y, sin ninguna,
   que un documento en dolares no se puede guardar (el sistema nunca convierte con 1). */
function FxSource({ fx, loaded, cur, docFx, base }: { fx: FxToday | null; loaded: boolean; cur: string; docFx: string | number | null | undefined; base?: string }) {
  if (!loaded) return null;
  if (!fx) {
    return <p style={{ fontSize: 11.5, marginTop: 8, color: "var(--warn)" }}>Sin tipo de cambio cargado: un documento en dólares no se puede guardar hasta cargarlo en Ajustes → Facturación.</p>;
  }
  const propia = cur !== "CRC" && docFx != null && Number(docFx) > 0 && Math.abs(Number(docFx) - Number(fx.sell)) > 0.00001;
  return (
    <>
      <p className="muted" style={{ fontSize: 11.5, marginTop: 8, marginBottom: 0 }}>
        {propia ? "El documento guarda la tasa con la que se hizo. " : "Se guarda en el documento al emitir. "}
        Tasa vigente: {fx.source_label} · {fmtDate(fx.date)}{base ? ` · base ${base}` : ""}.
      </p>
      {fx.stale && <p style={{ fontSize: 11.5, marginTop: 6, marginBottom: 0, color: "var(--warn)" }}>La tasa tiene {fx.business_days_old} días hábiles: actualizala en Ajustes → Facturación antes de emitir en dólares.</p>}
    </>
  );
}
