/* O1-11a: montos en colones como se escriben en Costa Rica. «4.700» son cuatro mil setecientos, nunca ₡4,70. */
import { describe, expect, it } from "vitest";
import { CRC_ERROR, formatCRCInput, parseCRC } from "../parseCRC";

const ok = (s: string, v: number) => it(`«${s}» -> ${v}`, () => expect(parseCRC(s)).toEqual({ ok: true, value: v }));
const malo = (s: string) => it(`«${s}» se rechaza`, () => expect(parseCRC(s)).toEqual({ ok: false, error: CRC_ERROR }));

describe("parseCRC acepta", () => {
  ok("4.700", 4700);
  ok("4 700", 4700);
  ok("4700", 4700);
  ok("4.700,50", 4700.5);
  ok("1.250.000", 1250000);
  ok("0", 0);
  ok("", 0);
  ok("₡ 4.700", 4700);
  ok("4 700", 4700);
  ok("0,75", 0.75);
});

describe("parseCRC rechaza lo ambiguo", () => {
  malo("4.70");
  malo("4,700");
  malo("4.7");
  malo("47.00");
  malo("-100");
  malo("cuatro mil");
  malo("4.700 000");
  malo("4.700,505");
});

describe("formatCRCInput", () => {
  it("vuelve al formato del campo", () => {
    expect(formatCRCInput(4700)).toBe("4.700");
    expect(formatCRCInput(4700.5)).toBe("4.700,50");
    expect(formatCRCInput(0)).toBe("0");
    expect(parseCRC(formatCRCInput(1250000.25))).toEqual({ ok: true, value: 1250000.25 });
  });
});
