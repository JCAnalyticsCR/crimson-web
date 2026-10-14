/* «Lo que viene» (/propuestas): las 17 propuestas, NINGUNA incluida aun, por etapa o por tema, sobre la base que ya
   existe (H1…H16). Pantalla de DESEO: desde 768 px las tarjetas se asientan en escalon (380 ms, maximo 8); en movil
   nada se mueve y todo es usable desde el primer segundo. «Me interesa» arma un resumen que se COPIA: no se envia nada.
   Consulta: ?vista=etapas|familias · ?filtro=maqueta|interes · ?resumen=1 (abre el resumen) · ?ver=base */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CLAVES, ETAPAS, ETIQUETAS, EXISTENTES, FAMILIAS, PROPUESTAS, type Familia, type Propuesta, type PropuestaId } from "../registro";
import { BarraMonitor, BotonMeInteresa, NavPropuestas, SelloPropuesta } from "../_comun/componentes";
import { useInteres } from "../_comun/hooks";
import { almacen } from "../_comun/pedidos";
import { setActiva, verDondeVa } from "../_capa/estado";
import { sembrarCarritoSiVacio } from "../_capa/carrito";
import { construirResumen, leerEtapa0, leerResumenRentabilidad } from "./resumen";
import "./lqv.css";

const conMaquetaPropia = (p: Propuesta) => !!p.maqueta && !p.maqueta.includes("?");
const N_MAQUETAS = PROPUESTAS.filter(conMaquetaPropia).length;
const primeraEtiqueta = (id: PropuestaId) => ETIQUETAS.find((e) => e.propuesta === id);

const Flecha = () => <svg className="lqv-flecha" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>;

export default function LoQueViene() {
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const interes = useInteres();
  const vista: "etapas" | "familias" = sp.get("vista") === "familias" ? "familias" : "etapas";
  const filtro: "todas" | "maqueta" | "interes" = sp.get("filtro") === "maqueta" ? "maqueta" : sp.get("filtro") === "interes" ? "interes" : "todas";
  const resumen = sp.get("resumen") === "1";
  const h1 = useRef<HTMLHeadingElement>(null);
  const primera = useRef(true);
  const [todasH, setTodasH] = useState(false);
  const abridor = useRef<HTMLElement | null>(null);

  useEffect(() => { primera.current = false; }, []);
  useEffect(() => {
    document.title = "Lo que viene · Propuestas · Tienda Crimson (vista previa)";
    window.scrollTo(0, 0);
    // con ?resumen=1 el dialogo ya tomo el foco (los efectos del hijo corren antes): no se lo quitamos
    if (sp.get("resumen") !== "1") h1.current?.focus({ preventScroll: true });
    if (sp.get("ver") === "base") {
      setTodasH(true);
      window.setTimeout(() => document.getElementById("lqv-base")?.scrollIntoView({ block: "start" }), 60);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const poner = (k: string, v: string | null) => setSp((prev) => { const n = new URLSearchParams(prev); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });
  const abrirResumen = () => { abridor.current = document.activeElement as HTMLElement | null; poner("resumen", "1"); };
  const cerrarResumen = () => {
    poner("resumen", null);
    window.setTimeout(() => { const a = abridor.current; (a && a.isConnected ? a : h1.current)?.focus({ preventScroll: true }); }, 0);
  };

  const pasa = (p: Propuesta) => (filtro === "maqueta" ? conMaquetaPropia(p) : filtro === "interes" ? interes.tiene(p.id) : true);
  const grupos = useMemo(() => {
    if (vista === "etapas") {
      return ETAPAS.map((e) => ({
        clave: `e${e.n}`, ceja: `Etapa ${e.n}`, titulo: e.titulo, porque: e.porque, depende: e.depende, etapa: e.n,
        items: e.ids.map((id) => PROPUESTAS.find((p) => p.id === id)!).filter(Boolean),
      }));
    }
    return (Object.keys(FAMILIAS) as Familia[]).map((f) => ({
      clave: `f${f}`, ceja: `Tema ${f}`, titulo: FAMILIAS[f], porque: undefined as string | undefined, depende: undefined as string | undefined, etapa: undefined as number | undefined,
      items: PROPUESTAS.filter((p) => p.familia === f),
    }));
  }, [vista]);

  let n = 0; // indice global de tarjeta para el escalon (solo las 8 primeras, solo en la primera pintada)
  let primeraLista = true;
  const visibles = grupos.map((g) => ({ ...g, items: g.items.filter(pasa) })).filter((g) => g.items.length);
  const nInteres = interes.ids.length;

  const verSobreLaTienda = () => { setActiva(true); navigate("/"); };

  return (
    <div className="pp lqv">
      <NavPropuestas actual="LQV" />
      <main id="contenido" tabIndex={-1} className="lqv-main">
        {/* ---------- heroe sobre tinta ---------- */}
        <header className="lqv-heroe">
          <div className="lqv-reticula" aria-hidden="true" />
          <BarraMonitor cam="08" rotulo="Lo que viene" />
          <div className="lqv-heroe__in">
            <div className="lqv-heroe__txt">
              <SelloPropuesta tono="oscuro" className="lqv-heroe__sello" />
              <h1 ref={h1} tabIndex={-1} className="lqv-h1">Lo que la tienda puede hacer después</h1>
              <p className="lqv-bajada">Diecisiete ideas para que cada venta abra la siguiente. Ninguna está incluida todavía: son propuestas, marcadas como tales, y seis tienen maqueta para tocarlas.</p>
              <div className="lqv-heroe__acc">
                <button type="button" className="pp-btn pp-btn--crimson" onClick={verSobreLaTienda}>Verlas sobre la tienda</button>
                <Link className="pp-btn lqv-btn--claro" to="/propuestas/a1">Ver la primera maqueta</Link>
                <Link className="pp-btn lqv-btn--linea" to="/rentabilidad">¿Es rentable?</Link>
              </div>
              <p className="lqv-pista">Hacé una compra de prueba y seguila: la vas a ver llegar a Crimson, a tu portal y al tablero.</p>
            </div>
            <dl className="lqv-lectura" aria-label="Resumen de las propuestas">
              <div><dt>Propuestas</dt><dd>{PROPUESTAS.length}</dd></div>
              <div><dt>Con maqueta</dt><dd>{N_MAQUETAS}</dd></div>
              <div><dt>Etapas</dt><dd>{ETAPAS.length}</dd></div>
              <div><dt>Incluidas hoy</dt><dd>Ninguna</dd></div>
            </dl>
          </div>
        </header>

        <div className="lqv-cuerpo">
          {/* ---------- la base que ya existe ---------- */}
          <section id="lqv-base" className="lqv-base" aria-labelledby="lqv-base-t">
            <div className="lqv-sec__cab">
              <p className="lqv-ceja lqv-ceja--ok">Ya construido</p>
              <h2 id="lqv-base-t" className="lqv-h2">La base que ya existe</h2>
              <p className="lqv-sec__baj">Todo lo que viene se apoya en esto. Ya funciona en la plataforma.</p>
            </div>
            <ul className="lqv-hs" data-todas={todasH ? "" : undefined}>
              {EXISTENTES.map((h) => (
                <li key={h.id} className="lqv-h">
                  <span className="lqv-h__id"><span aria-hidden="true">✓ </span>{h.id}</span>
                  <span className="lqv-h__t">{h.titulo}</span>
                  {h.verEn ? (
                    h.preparar === "carrito"
                      ? <button type="button" className="lqv-h__ir" onClick={async () => { await sembrarCarritoSiVacio(); navigate(h.verEn!); }}>Probarlo <span className="pp-sr">: {h.id}</span><Flecha /></button>
                      : <Link className="lqv-h__ir" to={h.verEn}>Probarlo <span className="pp-sr">: {h.id}</span><Flecha /></Link>
                  ) : <span className="lqv-h__no">Existe en la plataforma; no se muestra en esta vista previa.</span>}
                </li>
              ))}
            </ul>
            {!todasH && <button type="button" className="pp-btn pp-btn--fantasma lqv-hs__mas" aria-expanded={false} onClick={() => setTodasH(true)}>Ver las 16</button>}
          </section>

          {/* ---------- controles ---------- */}
          <div className="lqv-controles" role="group" aria-label="Cómo ver las propuestas">
            <fieldset className="lqv-seg">
              <legend className="pp-sr">Agrupar</legend>
              <label><input type="radio" name="lqv-vista" checked={vista === "etapas"} onChange={() => poner("vista", null)} /><span>Por etapa</span></label>
              <label><input type="radio" name="lqv-vista" checked={vista === "familias"} onChange={() => poner("vista", "familias")} /><span>Por tema</span></label>
            </fieldset>
            <fieldset className="lqv-filtros">
              <legend className="pp-sr">Mostrar</legend>
              <label><input type="radio" name="lqv-filtro" checked={filtro === "todas"} onChange={() => poner("filtro", null)} /><span>Todas</span></label>
              <label><input type="radio" name="lqv-filtro" checked={filtro === "maqueta"} onChange={() => poner("filtro", "maqueta")} /><span>Con maqueta ({N_MAQUETAS})</span></label>
              <label><input type="radio" name="lqv-filtro" checked={filtro === "interes"} onChange={() => poner("filtro", "interes")} /><span>Me interesan ({nInteres})</span></label>
            </fieldset>
          </div>

          {/* ---------- propuestas ---------- */}
          <div id={vista === "etapas" ? "lqv-etapas" : "lqv-temas"} className="lqv-grupos">
            {visibles.length === 0 && (
              <div className="lqv-vacio" role="status">
                <b>Todavía no marcaste ninguna propuesta</b>
                <span>Tocá «Me interesa» en las que querás conversar.</span>
                <button type="button" className="pp-btn pp-btn--tinta" onClick={() => poner("filtro", null)}>Ver lo que viene</button>
              </div>
            )}
            {visibles.map((g) => {
              const esPrimera = primeraLista;
              primeraLista = false;
              const etapaIds = g.etapa ? ETAPAS.find((e) => e.n === g.etapa)!.ids : [];
              const etapaMarcada = etapaIds.length > 0 && etapaIds.every((id) => interes.tiene(id));
              return (
                <section key={g.clave} className="lqv-grupo" aria-labelledby={`lqv-${g.clave}`}>
                  <div className="lqv-grupo__cab">
                    <p className="lqv-ceja">{g.ceja} · {g.titulo}</p>
                    <h2 id={`lqv-${g.clave}`} className="lqv-h2">{g.titulo}</h2>
                    {g.porque && <p className="lqv-grupo__porque">{g.porque}</p>}
                    {g.depende && <p className="lqv-grupo__dep"><b>Depende de:</b> {g.depende}</p>}
                    {g.etapa && (
                      <div className="lqv-grupo__acc">
                        <Link className="lqv-link" to={`/rentabilidad?enfoque=etapa${g.etapa}`}>¿Cuánto aporta? <Flecha /></Link>
                        <button type="button" className="pp-interes" data-variante="chip" aria-pressed={etapaMarcada}
                          onClick={() => (etapaMarcada ? interes.quitar(etapaIds) : interes.agregar(etapaIds))}>
                          {etapaMarcada ? <>Te interesan todas <span aria-hidden="true">✓</span></> : "Me interesan todas las de esta etapa"}
                        </button>
                      </div>
                    )}
                  </div>
                  <ul id={esPrimera ? "lqv-lista" : undefined} className="lqv-lista">
                    {g.items.map((p) => {
                      const i = n++;
                      const et = primeraEtiqueta(p.id);
                      return (
                        <li key={p.id} className="lqv-tarjeta" data-interes={interes.tiene(p.id) ? "" : undefined}
                          data-anima={primera.current && i < 8 ? "" : undefined} style={{ ["--i" as string]: i }}>
                          <div className="lqv-tarjeta__barra" aria-hidden="true"><span>{p.id} · {FAMILIAS[p.familia]}</span><span>Etapa {p.etapa}</span></div>
                          <div className="lqv-tarjeta__cuerpo">
                            <SelloPropuesta id={p.id} tamano="chico" />
                            <h3 className="lqv-tarjeta__t">{p.titulo}</h3>
                            <p className="lqv-tarjeta__b">{p.beneficio}</p>
                            {p.hoy && <p className="lqv-tarjeta__hoy"><b>Hoy:</b> {p.hoy}</p>}
                            {p.depende && <p className="lqv-tarjeta__dep"><b>Depende de:</b> {p.depende}</p>}
                            {p.seApoyaEn.length > 0 && (
                              <p className="lqv-tarjeta__apoyo"><span>Se apoya en:</span>
                                {p.seApoyaEn.map((h) => <span key={h} className="lqv-apoyo" title={EXISTENTES.find((x) => x.id === h)?.titulo}><span aria-hidden="true">✓ </span>{h}<span className="pp-sr"> ({EXISTENTES.find((x) => x.id === h)?.titulo})</span></span>)}
                              </p>
                            )}
                            <div className="lqv-tarjeta__acc">
                              {p.maqueta && <Link className="lqv-link" to={p.maqueta}>{conMaquetaPropia(p) ? "Ver maqueta" : "Ver en la maqueta A1"} <span className="pp-sr">de {p.id}</span><Flecha /></Link>}
                              {et && <button type="button" className="lqv-link lqv-link--btn" onClick={() => { void verDondeVa(et, navigate); }}>Ver dónde va <span className="pp-sr">{p.id} en la tienda</span></button>}
                              <BotonMeInteresa id={p.id} variante="chip" />
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </div>

          {/* ---------- cierre ---------- */}
          <div className="lqv-cierre">
            <Link className="lqv-cierre__t" to="/rentabilidad">
              <span className="lqv-ceja">CAM 09 · Números</span>
              <b>¿Es rentable?</b>
              <span>Hacé las cuentas con tus números <Flecha /></span>
            </Link>
            <Link className="lqv-cierre__t lqv-cierre__t--ya" to="/detras-de-escena">
              <span className="lqv-ceja lqv-ceja--ok">Ya existe</span>
              <b>Detrás de escena</b>
              <span>Mirá cómo se maneja por dentro <Flecha /></span>
            </Link>
          </div>
        </div>

        {nInteres > 0 && !resumen && (
          <div className="lqv-barra" data-pp-barra role="region" aria-label="Tu selección">
            <span>Te interesan <b>{nInteres}</b></span>
            <button type="button" className="pp-btn pp-btn--crimson pp-btn--sm" onClick={abrirResumen}>Ver mi resumen</button>
          </div>
        )}
      </main>
      {resumen && <DialogoResumen onCerrar={cerrarResumen} />}
    </div>
  );
}

// ---------------------------------------------------------------- «Tu resumen»
function DialogoResumen({ onCerrar }: { onCerrar: () => void }) {
  const interes = useInteres();
  const [notas, setNotas] = useState(() => almacen.local.get(CLAVES.notas) || "");
  const [msg, setMsg] = useState<string | null>(null);
  const [copiado, setCopiado] = useState(false);
  const caja = useRef<HTMLDivElement>(null);
  const titulo = useRef<HTMLHeadingElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const tId = useId();
  const nId = useId();
  const rent = useMemo(leerResumenRentabilidad, []);
  const etapa0 = useMemo(leerEtapa0, []);
  const texto = construirResumen(interes.ids, notas, rent, new Date(), etapa0);

  useEffect(() => { almacen.local.set(CLAVES.notas, notas.slice(0, 1000)); }, [notas]);
  // onCerrar cambia en cada pintada del padre: se lee de una ref para no repetir el foco inicial
  const cerrarRef = useRef(onCerrar);
  useEffect(() => { cerrarRef.current = onCerrar; });
  useEffect(() => {
    titulo.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); cerrarRef.current(); return; }
      if (e.key !== "Tab" || !caja.current) return;
      const f = Array.from(caja.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), textarea, input"));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === titulo.current)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); html.style.overflow = prev; };
  }, []);

  const copiar = async () => {
    setMsg(null);
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(true);
      window.setTimeout(() => setCopiado(false), 1800);
    } catch {
      area.current?.focus();
      area.current?.select();
      setMsg("Seleccionado: copialo con Ctrl+C");
    }
  };

  return (
    <div className="lqv-dialogo" onClick={onCerrar}>
      <div ref={caja} className="lqv-dialogo__caja" role="dialog" aria-modal="true" aria-labelledby={tId} onClick={(e) => e.stopPropagation()}>
        <div className="lqv-dialogo__cab">
          <h2 ref={titulo} id={tId} tabIndex={-1} className="lqv-h2">Tu resumen</h2>
          <button type="button" className="lqv-dialogo__x" onClick={onCerrar} aria-label="Cerrar el resumen">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg>
          </button>
        </div>
        <p className="lqv-dialogo__baj">Copiá este texto y mandáselo a JC Analytics por correo o WhatsApp. Desde aquí no se envía nada.</p>
        {interes.ids.length === 0 && !etapa0 && (
          <p className="lqv-dialogo__vacio">Todavía no marcaste ninguna propuesta. <button type="button" className="lqv-link lqv-link--btn" onClick={onCerrar}>Ver lo que viene</button></p>
        )}
        <label className="lqv-campo">
          <span>Resumen para copiar</span>
          <textarea ref={area} readOnly value={texto} rows={12} spellCheck={false} />
        </label>
        <label className="lqv-campo">
          <span>Notas (opcional)</span>
          <textarea className="lqv-notas" value={notas} maxLength={1000} rows={3} aria-describedby={nId} onChange={(e) => setNotas(e.target.value.slice(0, 1000))}
            placeholder="Por ejemplo: empezar por la etapa 1 y revisar el precio del plan." />
        </label>
        <span id={nId} className="lqv-dialogo__cuenta">{notas.length} de 1000 caracteres · se guardan solo en este navegador</span>
        <p className="lqv-dialogo__msg" aria-live="polite">{copiado ? "Copiado" : msg}</p>
        <div className="lqv-dialogo__acc">
          <button type="button" className="pp-btn pp-btn--crimson" onClick={copiar}>{copiado ? "Copiado" : "Copiar resumen"}</button>
          <button type="button" className="pp-btn pp-btn--fantasma" disabled={interes.ids.length === 0} onClick={() => interes.quitar(interes.ids)}>Vaciar selección</button>
          <Link className="lqv-link" to="/rentabilidad">Ajustar los números en ¿Es rentable? <Flecha /></Link>
        </div>
      </div>
    </div>
  );
}
