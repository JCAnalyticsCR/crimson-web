/* Precios escritos con IVA (O4-05). El cliente paga el precio FINAL con IVA; el sistema guarda el neto (sin IVA) con 5
   decimales (Product.price / web_price, models/catalog.py). La cuenta es la misma del API (services/store_bulk) y de la
   tienda (routers/store._gross): neto = final / (1 + IVA), redondeado a 5 decimales; final = neto x (1 + IVA), a 2.
   «12.500» con IVA 13 % -> 11 061,94690 -> 12 500,00. Modulo puro (pruebas en __tests__/precioIva.test.ts). */

const round = (v: number, dec: number) => {
  const f = 10 ** dec;
  return Math.round((v + Number.EPSILON) * f) / f;
};

export const netoDesdeFinal = (final: number, ivaPct: number) => round(final / (1 + ivaPct / 100), 5);
export const finalDesdeNeto = (neto: number, ivaPct: number) => round(neto * (1 + ivaPct / 100), 2);
