/* O4-05: el precio se escribe final con IVA y se guarda neto con 5 decimales; la ida y vuelta da lo escrito. */
import { describe, expect, it } from "vitest";
import { parseCRC } from "../../../../lib/parseCRC";
import { finalDesdeNeto, netoDesdeFinal } from "../precioIva";

describe("precio con IVA", () => {
  it("12.500 con IVA 13 % se guarda 11 061,94690 y vuelve a 12 500", () => {
    const r = parseCRC("12.500");
    expect(r.ok && r.value).toBe(12500);
    const neto = netoDesdeFinal(12500, 13);
    expect(neto).toBe(11061.9469);
    expect(finalDesdeNeto(neto, 13)).toBe(12500);
  });

  it("la ida y vuelta da lo escrito para muchos precios y tarifas", () => {
    for (const iva of [13, 4, 2, 1, 0]) {
      for (let final = 100; final <= 2000; final += 7) expect(finalDesdeNeto(netoDesdeFinal(final, iva), iva)).toBe(final);
    }
  });

  it("igual que el API: 1 950 -> 1 725,66372", () => {
    expect(netoDesdeFinal(1950, 13)).toBe(1725.66372);
  });
});
