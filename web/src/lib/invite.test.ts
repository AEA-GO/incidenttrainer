import { describe, expect, it } from "vitest";
import { normalise, shown } from "./invite";

describe("invite codes", () => {
  it("accepts any case, with or without the dash", () => {
    expect(normalise("k7qm-4xpa")).toBe("K7QM4XPA");
    expect(normalise(" K7QM 4XPA ")).toBe("K7QM4XPA");
  });
  it("refuses look-alikes and wrong lengths", () => {
    expect(normalise("K7QM-4XP0")).toBeNull();
    expect(normalise("K7QM-4XPAA")).toBeNull();
    expect(normalise("")).toBeNull();
    expect(normalise(null)).toBeNull();
  });
  it("shows codes with a dash", () => {
    expect(shown("K7QM4XPA")).toBe("K7QM-4XPA");
  });
});
