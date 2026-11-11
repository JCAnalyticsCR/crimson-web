/* O2-01d: el logo del portal (/logo.png, 43 KB sin hash) se cambia por la copia 2x liviana con hash; uno propio no. */
import { describe, expect, it } from "vitest";
import { logoSrc } from "../logo";

describe("logoSrc", () => {
  it("cambia /logo.png del portal por la copia liviana con hash", () => {
    const s = logoSrc("/logo.png");
    expect(s).not.toBe("/logo.png");
    expect(s).toMatch(/logo-tienda.*\.png/);
    expect(logoSrc("/logo.png?v=2")).toBe(s);
  });
  it("deja tal cual un logo subido o de otro sitio, y nada sin logo", () => {
    expect(logoSrc("/api/media/f/abc123")).toBe("/api/media/f/abc123");
    expect(logoSrc("https://cdn.example.com/logo.png")).toBe("https://cdn.example.com/logo.png");
    expect(logoSrc(null)).toBeNull();
    expect(logoSrc("")).toBeNull();
  });
});
