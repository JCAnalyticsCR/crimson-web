/* O1-09: textos del envio. Datos ficticios. */
import { describe, expect, it } from "vitest";
import { freeShipGap, quitarRetiro, rateText, sinParrafosDeRetiro } from "../storeKit";

const sp = (s: string) => s.replace(/\s/g, " ");

describe("envío visible (O1-09)", () => {
  it("cuánto falta para el envío gratis", () => {
    const s = { free_from: 2000, rates: [], pickup: null };
    expect(freeShipGap(s, 1500)).toBe(500);
    expect(freeShipGap(s, 2500)).toBe(0);
    expect(freeShipGap({ ...s, free_from: null }, 100)).toBeNull();
    expect(freeShipGap(null, 100)).toBeNull();
  });

  it("la tarifa se dice completa, con zona, plazo si lo hay y nombre", () => {
    expect(sp(rateText({ name: "Correos", area: "a todo el país", amount: 395 }, "CRC"))).toBe("₡395 a todo el país (Correos)");
    expect(sp(rateText({ name: "Mensajería", area: "en la GAM", amount: 1531, lead: "1 a 2 días hábiles" }, "CRC"))).toBe("₡1 531 en la GAM en 1 a 2 días hábiles (Mensajería)");
  });

  it("sin dirección de retiro, ningún texto lo promete", () => {
    expect(quitarRetiro("Envío a todo el país o retiro coordinado. Te avisamos cuando sale.")).toBe("Envío a todo el país. Te avisamos cuando sale.");
    expect(quitarRetiro("Envío o retiro")).toBe("Envío a todo el país");
    const pagina = "Envíos\nEnviamos a todo el país. El costo depende del método. También podés retirar en nuestras instalaciones.\n\nTiempos\nSale en 1 a 2 días.";
    const limpio = sinParrafosDeRetiro(pagina);
    expect(limpio).not.toMatch(/retir/i);
    expect(limpio).toContain("Enviamos a todo el país. El costo depende del método.");
    expect(limpio).toContain("Tiempos\nSale en 1 a 2 días.");
  });
});
