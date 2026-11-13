/* Respuesta de Hacienda (DetalleMensaje) en lenguaje claro. Hacienda la manda como tabla de texto
   («codigo, mensaje, fila, columna» y luego `-37, ""texto"", 0, 0`), con avisos aun cuando ACEPTA el comprobante.
   05/11: lo que contesta el ambiente de Pruebas (modo="pruebas") nunca se pinta en rojo: Pruebas no conoce las facturas
   reales de los proveedores y su -29 no es un rechazo de la Hacienda real. */

type Item = { code: string; text: string };

/* que hacer con cada codigo que ya vimos; los demas se muestran tal cual */
const QUE_HACER: Record<string, string> = {
  "-37": "Es un aviso, no impide la factura. Revise que provincia, cantón y distrito en Ajustes → Hacienda → Ubicación sean los del domicilio fiscal en TRIBU-CR (el código postal los da: 20110 = provincia 2, cantón 01, distrito 10). Si ya coinciden y el aviso sigue, el registro de comprobantes de Hacienda no tiene aún su domicilio actualizado: consúltelo con Hacienda.",
  "-29": "Es normal en Pruebas: la factura del proveedor es real y Pruebas no la conoce. Su respuesta está bien armada: use «Enviar en Producción» en la ficha para mandarla a la Hacienda real antes de que venza el plazo.",
  "-111":"Los totales de servicios y mercancías no cuadran con las líneas. Lo corrige Crimson: si vuelve a salir, envíenos la clave del documento.",
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

/** El motivo en una linea, para listas: el primer error de Hacienda (y cuantos mas hay); el texto entero va en el title. */
export function motivoCorto(msg?: string | null): { corto: string; completo: string } | null {
  const { items, texto } = parseHacienda(msg);
  if (items.length) return { corto: items[0].text + (items.length > 1 ? ` (+${items.length - 1} más)` : ""), completo: items.map((i) => `${i.text} (código ${i.code})`).join("\n") };
  return texto ? { corto: texto, completo: texto } : null;
}

export function HaciendaMensaje({ msg, status, modo }: { msg?: string | null; status?: string; modo?: string | null }) {
  const { pruebas, items, texto } = parseHacienda(msg);
  if (!items.length && !texto && !pruebas) return null;
  const aceptada = status === "aceptada";
  const enPruebas = modo === "pruebas" || pruebas;
  const titulo = enPruebas
    ? "Respuesta del ambiente de Pruebas (no es la Hacienda real):"
    : aceptada ? "Hacienda la aceptó, con este aviso:" : items.length === 1 ? "Hacienda la rechazó por este motivo:" : `Hacienda la rechazó por ${items.length} motivos:`;
  return (
    <div style={{ fontSize: 13, display: "grid", gap: 6, textTransform: "none" }}>
      {items.length > 0 && <div style={{ fontWeight: 600, color: enPruebas ? "var(--text-2)" : aceptada ? "var(--warn)" : "var(--bad)" }}>{titulo}</div>}
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
      {enPruebas && <div className="muted">Ambiente de Pruebas: esta respuesta no tiene validez fiscal.</div>}
    </div>
  );
}
