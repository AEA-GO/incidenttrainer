import { useEffect, useRef, useState } from "react";

// A clock that ticks smoothly between server updates. The server's value (sent every ~2 s,
// a little late) re-anchors it; the browser counts in between. It never steps backwards
// for small differences (network jitter), and jumps when the server is clearly ahead.
// `direction` is +1 for elapsed time, -1 for time left.
export function useTicking(value: number | null | undefined, direction: 1 | -1 = 1, running = true): number | null {
  const anchor = useRef<{ value: number; at: number } | null>(null);
  const seen = useRef<number | null | undefined>(undefined);
  const [, redraw] = useState(0);

  if (value === null || value === undefined) {
    anchor.current = null;
    seen.current = value;
  } else if (value !== seen.current) {
    // Only a new server value re-anchors; our own redraws don't.
    seen.current = value;
    const now = performance.now();
    const current = anchor.current;
    const predicted = current ? current.value + (direction * (now - current.at)) / 1000 : null;
    const behind = predicted === null ? Infinity : direction * (predicted - value);
    // Keep counting if the server is only slightly behind our count (0..2 s), else re-anchor.
    if (predicted === null || behind < 0 || behind >= 2) anchor.current = { value, at: now };
  }

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => redraw((n) => n + 1), 250);
    return () => window.clearInterval(timer);
  }, [running]);

  const a = anchor.current;
  if (!a) return null;
  if (!running) return a.value;
  return Math.max(0, a.value + (direction * (performance.now() - a.at)) / 1000);
}
