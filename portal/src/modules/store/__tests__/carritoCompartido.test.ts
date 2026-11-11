import { describe, expect, it } from "vitest";
import { parseCarrito } from "../carritoCompartido";

describe("parseCarrito (O2-09)", () => {
  it("lee id x cantidad y suma los repetidos", () => {
    expect(parseCarrito("116x1,241x2,116x3")).toEqual([{ id: 116, qty: 4 }, { id: 241, qty: 2 }]);
  });
  it("sin cantidad es 1 y la cantidad queda entre 1 y 99", () => {
    expect(parseCarrito("5,7x0,9x99")).toEqual([{ id: 5, qty: 1 }, { id: 7, qty: 1 }, { id: 9, qty: 99 }]);
  });
  it("ignora lo que no calza y corta en 12 lineas", () => {
    expect(parseCarrito("abc,12x,<script>,3x2")).toEqual([{ id: 3, qty: 2 }]);
    expect(parseCarrito(Array.from({ length: 20 }, (_, i) => `${i + 1}x1`).join(","))).toHaveLength(12);
    expect(parseCarrito(null)).toEqual([]);
  });
});
