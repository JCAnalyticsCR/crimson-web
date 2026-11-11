/* O4-06: el tema elegido en Apariencia se vuelve variables CSS de .shop; sin tema, nada cambia; el acento con poco
   contraste con texto blanco se detecta antes de guardar. */
import { describe, expect, it } from "vitest";
import { accentOk, contrast, themeVars } from "../theme";

describe("tema de la tienda", () => {
  it("sin tema la tienda queda como hoy", () => {
    expect(themeVars(undefined)).toEqual({});
    expect(themeVars(null)).toEqual({});
  });

  it("acento, radio y fondo se vuelven variables de .shop", () => {
    const v = themeVars({ accent: "#1d4ed8", radius: 10, background: "blanco" });
    expect(v["--crimson"]).toBe("#1d4ed8");
    expect(v["--crimson-soft"]).toBe("rgba(29, 78, 216, .10)");
    expect(v["--r"]).toBe("10px");
    expect(v["--bg"]).toBe("#ffffff");
    expect(v["--serif"]).toBeUndefined();  // Archivo solo con el tema Crimson aprobado
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
