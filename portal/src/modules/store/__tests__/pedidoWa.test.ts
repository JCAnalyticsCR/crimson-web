/* O1-02: el mensaje de WhatsApp de la venta asistida (ficha «por confirmar»). Datos ficticios. */
import { describe, expect, it } from "vitest";
import { mensajePedido } from "../pedidoWa";

const base = { id: 42, nombre: "Cámara domo de prueba", codigo: "PR-DOMO-1", cantidad: 2, precio: 1808, url: "https://tienda.test/p/camara-domo-42" };
const nbsp = (s: string) => s.replace(/[\u00a0\u202f]/g, " ");

describe("mensajePedido", () => {
  it("lleva producto, código, cantidad, precio con IVA, dirección y referencia", () => {
    expect(nbsp(mensajePedido(base))).toBe(
      "Hola, quiero pedir Cámara domo de prueba (código PR-DOMO-1), cantidad 2. Precio que vi en la web: ₡1 808 con IVA. " +
        "¿Me confirman precio, existencia y entrega? https://tienda.test/p/camara-domo-42 · ref. web P42",
    );
  });
  it("sin código ni precio no deja huecos", () => {
    const m = mensajePedido({ ...base, codigo: null, precio: 0 });
    expect(m).toContain("pedir Cámara domo de prueba, cantidad 2. ¿Me confirman");
    expect(m).not.toContain("código");
    expect(m).not.toContain("Precio que vi");
  });
  it("la cantidad nunca baja de 1 ni lleva decimales", () => {
    expect(mensajePedido({ ...base, cantidad: 0 })).toContain("cantidad 1.");
    expect(mensajePedido({ ...base, cantidad: 3.7 })).toContain("cantidad 3.");
  });
  it("acepta el precio como texto (así llega del API)", () => {
    expect(nbsp(mensajePedido({ ...base, precio: "1808.00" }))).toContain("₡1 808 con IVA");
  });
});
