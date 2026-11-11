/* Descripcion de la ficha (O3-06): las lineas «- Clave: valor» se muestran como lista y el resto como parrafos. Usa
   la misma clase (sh-desc) que antes: no cambia estilos. */
export function Descripcion({ texto }: { texto: string }) {
  const lineas = texto.split("\n").map((l) => l.trim()).filter(Boolean);
  const vineta = (l: string) => /^[-•]\s*/.test(l);
  const bloques: { lista: boolean; lineas: string[] }[] = [];
  for (const l of lineas) {
    const lista = vineta(l);
    const ult = bloques[bloques.length - 1];
    if (ult && ult.lista === lista) ult.lineas.push(l);
    else bloques.push({ lista, lineas: [l] });
  }
  return (
    <div className="sh-desc">
      {bloques.map((b, i) => b.lista
        ? <ul key={i}>{b.lineas.map((l, j) => <li key={j}>{l.replace(/^[-•]\s*/, "")}</li>)}</ul>
        : <p key={i} style={{ margin: "0 0 .6em" }}>{b.lineas.join(" ")}</p>)}
    </div>
  );
}
