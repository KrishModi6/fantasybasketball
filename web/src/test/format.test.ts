import { describe, expect, it } from "vitest";
import { count, fmt1, pct, record, signed, signedPct } from "../lib/format";

describe("format", () => {
  it("formats signed numbers with a true minus sign", () => {
    expect(signed(3.14)).toBe("+3.1");
    expect(signed(-2)).toBe("−2.0");
    expect(signed(0.01)).toBe("0.0");
  });
  it("formats percentages and records", () => {
    expect(pct(0.256)).toBe("26%");
    expect(signedPct(-0.0123)).toBe("−1.2%");
    expect(record(7, 3)).toBe("7–3");
    expect(record(7, 3, 1)).toBe("7–3–1");
    expect(fmt1(null)).toBe("–");
  });
  it("counts with a plural", () => {
    expect(count(1, "team")).toBe("1 team");
    expect(count(7, "team")).toBe("7 teams");
  });
});
