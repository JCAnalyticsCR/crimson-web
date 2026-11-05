/* Respuesta de Hacienda (DetalleMensaje) en lenguaje claro. Hacienda la manda como tabla de texto
   («codigo, mensaje, fila, columna» y luego `-37, ""texto"", 0, 0`), con avisos aun cuando ACEPTA el comprobante. */

type Item = { code: string; text: string };

/* que hacer con cada codigo que ya vimos; los demas se muestran tal cual */
const QUE_HACER: Record<string, string> = {
  "-37": "Provincia, cantón y distrito en Ajustes → Hacienda → Ubicación no coinciden con el domicilio fiscal inscrito. Cópielos exactamente del Registro Único Tributario en TRIBU-CR (o actualice el domicilio allí).",
  "-111": "Los totales de servicios y mercancías no cuadran con las líneas. Lo corrige Crimson: si vuelve a salir, envíenos la clave del documento.",
};

export function parseHacienda(msg?: string | null): { pruebas: boolean; items: Item[]; texto: string } {
  const raw = (msg || "").replace(/&#13;/g, "\n");
  const pruebas = /ambiente de pruebas/i.test(raw);
  const items: Item[] = [];
  const re = /(-?\d{1,4})\s*,\s*"+([\s\S]*?)"+\s*,\s*-?\d+\s*,\s*-?\d+/g;
  for (let m = re.exec(raw); m; m = re.exec(raw)) items.push({ code: m[1], text: m[2].replace(/\s+/g, " ").trim() });
  const texto = items.length
    ? ""
    : raw.replace(/Este comprobante fue recibido en el ambiente de pruebas, por lo cual no tiene validez para fines tributarios\.?/i, "").replace(/\s+/g, " ").trim();
  return { pruebas, items, texto };
}

export function HaciendaMensaje({ msg, status }: { msg?: string | null; status?: string }) {
  const { pruebas, items, texto } = parseHacienda(msg);
  if (!items.length && !texto && !pruebas) return null;
  const aceptada = status === "aceptada";
  return (
    <div style={{ fontSize: 13, display: "grid", gap: 6, textTransform: "none" }}>
      {items.length > 0 && (
        <div style={{ fontWeight: 600, color: aceptada ? "var(--warn)" : "var(--bad)" }}>
          {aceptada ? "Hacienda la aceptó, con este aviso:" : items.length === 1 ? "Hacienda la rechazó por este motivo:" : `Hacienda la rechazó por ${items.length} motivos:`}
        </div>
      )}
      {items.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6 }}>
          {items.map((it, i) => (
            <li key={i}>
              {it.text} <span className="muted">(código {it.code})</span>
              {QUE_HACER[it.code] && <div className="muted" style={{ marginTop: 2 }}><b>Qué hacer:</b> {QUE_HACER[it.code]}</div>}
            </li>
          ))}
        </ul>
      )}
      {texto && <div>{texto}</div>}
      {pruebas && <div className="muted">Ambiente de pruebas: este comprobante no tiene validez fiscal.</div>}
    </div>
  );
}
