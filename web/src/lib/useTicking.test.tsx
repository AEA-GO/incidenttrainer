import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTicking } from "./useTicking";

let now = 0;
beforeEach(() => {
  now = 0;
  vi.useFakeTimers();
  vi.spyOn(performance, "now").mockImplementation(() => now);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function Show({ value, direction = 1 }: { value: number | null; direction?: 1 | -1 }) {
  const t = useTicking(value, direction);
  return <span>{t === null ? "none" : Math.floor(t)}</span>;
}

function advance(ms: number) {
  act(() => {
    now += ms;
    vi.advanceTimersByTime(ms);
  });
}

describe("useTicking", () => {
  it("counts every second between server updates", () => {
    const { container } = render(<Show value={10} />);
    expect(container.textContent).toBe("10");
    advance(1000);
    expect(container.textContent).toBe("11");
    advance(1000);
    expect(container.textContent).toBe("12");
  });

  it("ignores a late server value instead of stepping back", () => {
    const { container, rerender } = render(<Show value={10} />);
    advance(2000);
    rerender(<Show value={11} />); // arrived late: we already show 12
    expect(container.textContent).toBe("12");
  });

  it("jumps when the server is clearly ahead", () => {
    const { container, rerender } = render(<Show value={10} />);
    advance(1000);
    rerender(<Show value={20} />);
    expect(container.textContent).toBe("20");
  });

  it("counts down for time left and never below zero", () => {
    const { container } = render(<Show value={2} direction={-1} />);
    advance(1000);
    expect(container.textContent).toBe("1");
    advance(5000);
    expect(container.textContent).toBe("0");
  });
});
