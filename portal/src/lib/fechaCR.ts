/* Día del calendario de Costa Rica (YYYY-MM-DD). toISOString() da el día UTC: de 18:00 a 24:00 CR ya es mañana, y un
   cobro o un gasto de la noche quedaba con la fecha (o el mes) siguiente y fuera del cierre de su día. Se arma con
   formatToParts para no depender del formato corto de un locale. */
const FMT = new Intl.DateTimeFormat("en-US", { timeZone: "America/Costa_Rica", year: "numeric", month: "2-digit", day: "2-digit" });

export const hoyCR = (d: Date = new Date()): string => {
  const p = Object.fromEntries(FMT.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}`;
};

/* Primer día del mes en curso en Costa Rica. */
export const inicioMesCR = (d: Date = new Date()): string => `${hoyCR(d).slice(0, 7)}-01`;
