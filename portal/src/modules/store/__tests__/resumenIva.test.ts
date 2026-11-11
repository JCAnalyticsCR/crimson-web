/* O1-10b: el resumen del checkout y de «Mi pedido» con IVA incluido sale de las lineas del servidor. Montos ficticios. */
import { describe, expect, it } from "vitest";
import { resumenConIva } from "../shopkit";

describe("resumen con IVA incluido (O1-10b)", () => {
  it("productos y envío con IVA suman el total del servidor; el envío es la tarifa final elegida", () => {
    const q = { total: 1695.0, shipping: 350, lines: [{ total: 1130, shipping: false }, { total: 169.5, shipping: false }, { total: 395.5, shipping: true }] };
    const r = resumenConIva(q);
    expect(r).toEqual({ productos: 1299.5, envio: 395.5 });
    expect(r.productos + r.envio).toBe(q.total);
  });
  it("sin línea de envío (retiro o envío gratis): envío 0", () => {
    expect(resumenConIva({ total: 1130, shipping: 0, lines: [{ total: 1130 }] })).toEqual({ productos: 1130, envio: 0 });
  });
  it("API viejo sin marca de envío en las líneas: el envío es total menos productos", () => {
    expect(resumenConIva({ total: 1525.5, shipping: 350, lines: [{ total: 1130 }] })).toEqual({ productos: 1130, envio: 395.5 });
  });
});
