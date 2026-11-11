/* O1-11a: la fila de tarifa del panel valida como el API y no publica un envio gratis sin confirmar. */
import { describe, expect, it } from "vitest";
import { checkRate, freeEverywhere, suspiciousAmount } from "../rates";

const fila = (o: Partial<{ name: string; amount: string; per_kg: string; overhead_pct: string; active: boolean }> = {}) => ({ name: "Envío GAM", amount: "4.700", per_kg: "0", overhead_pct: "0", active: true, ...o });

describe("checkRate", () => {
  it("lee los montos en formato de Costa Rica", () => {
    expect(checkRate(fila(), [])).toEqual({ ok: true, rate: { name: "Envío GAM", amount: 4700, per_kg: 0, overhead_pct: 0, active: true } });
    expect(checkRate(fila({ amount: "2 000", per_kg: "1.000", overhead_pct: "10" }), [])).toMatchObject({ ok: true, rate: { amount: 2000, per_kg: 1000, overhead_pct: 10 } });
  });
  it("rechaza nombre corto o repetido sin distinguir mayúsculas", () => {
    expect(checkRate(fila({ name: "X" }), [])).toMatchObject({ ok: false, errors: { name: expect.any(String) } });
    expect(checkRate(fila({ name: "envío gam" }), [{ name: "Envío GAM" }])).toMatchObject({ ok: false, errors: { name: "Ya hay una tarifa con ese nombre" } });
  });
  it("rechaza montos ambiguos y recargos fuera de rango", () => {
    expect(checkRate(fila({ amount: "4.70" }), [])).toMatchObject({ ok: false, errors: { amount: "Escriba el monto en colones, por ejemplo 4.700" } });
    expect(checkRate(fila({ overhead_pct: "150" }), [])).toMatchObject({ ok: false, errors: { overhead_pct: expect.any(String) } });
  });
});

describe("freeEverywhere y suspiciousAmount", () => {
  it("₡0 activa que no es retiro pide confirmación", () => {
    expect(freeEverywhere({ name: "Nuevo envío", amount: 0, active: true })).toBe(true);
    expect(freeEverywhere({ name: "Retiro en tienda", amount: 0, active: true })).toBe(false);
    expect(freeEverywhere({ name: "Nuevo envío", amount: 0, active: false })).toBe(false);
    expect(freeEverywhere({ name: "Correos", amount: 0, per_kg: 1000, active: true })).toBe(false);
  });
  it("menos de ₡100 se marca como sospechoso", () => {
    expect(suspiciousAmount({ amount: 4.7 })).toBe(true);
    expect(suspiciousAmount({ amount: 0 })).toBe(false);
    expect(suspiciousAmount({ amount: 4700 })).toBe(false);
  });
});
