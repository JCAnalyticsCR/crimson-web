/* O4-06: el tema elegido en Apariencia se vuelve variables CSS de .shop; sin tema, nada cambia; el acento con poco
   contraste con texto blanco se detecta antes de guardar. */
import { describe, expect, it } from "vitest";
import { THEME_DEFAULT, accentOk, contrast, themeVars } from "../theme";

describe("tema de la tienda", () => {
  it("sin tema, o con el tema por defecto, la tienda queda como hoy", () => {
    expect(themeVars(undefined)).toEqual({});
    expect(themeVars(null)).toEqual({});
    expect(themeVars(THEME_DEFAULT)).toEqual({});
    expect(themeVars({ preset: "clasico", accent: "#E2233A", radius: 8, background: "crema" })).toEqual({});
  });

  it("acento, radio y fondo se vuelven variables de .shop", () => {
    const v = themeVars({ accent: "#1d4ed8", radius: 10, background: "blanco" });
    expect(v["--crimson"]).toBe("#1d4ed8");
    expect(v["--crimson-press"]).toMatch(/^#[0-9a-f]{6}$/);
    expect(v["--r-1"]).toBe("10px");
    expect(v["--r-2"]).toBe("18px");
    expect(v["--paper"]).toBe("#ffffff");
    expect(v["--font"]).toBeUndefined();  // Archivo solo con el tema Crimson aprobado
  });

  it("valores fuera de rango no se aplican", () => {
    expect(themeVars({ accent: "rojo", radius: 99 })).toEqual({});
  });

  it("contraste con texto blanco: el carmesí de hoy pasa, un amarillo no", () => {
    expect(contrast("#000000", "#ffffff")).toBe(21);
    expect(accentOk("#e2233a")).toBe(true);
    expect(accentOk("#f5d90a")).toBe(false);
  });
});
