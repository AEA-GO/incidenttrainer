// Renders the incident screen with real agent answers (captured from the agent's tests:
// every kind of feed item, a used hint, a failed Resolve) through several polls.
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import scene from "./__fixtures__/agent-scene.json";
import state from "./__fixtures__/agent-state.json";
import { Incident } from "./Incident";

type Json = Record<string, unknown>;
let answers: Json[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  Element.prototype.scrollIntoView = vi.fn(); // jsdom has no layout
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/scene")) return new Response(JSON.stringify(scene));
      if (url.includes("/grafana/")) return new Response("no", { status: 404 });
      const next = answers.length > 1 ? answers.shift() : answers[0];
      return new Response(JSON.stringify(next));
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function poll(times: number) {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
  }
}

describe("incident screen with real agent states", () => {
  it("shows every kind of feed item and keeps working through polls", async () => {
    const later = {
      ...state,
      clock: { ...state.clock, t: 130 },
      feed: { cursor: state.feed.cursor, items: [] },
    };
    answers = [state, later, later];
    const onEnded = vi.fn();
    render(<Incident gameId="Zo6nEXJRVS0qCy4VkP7JIQ" onEnded={onEnded} />);
    await poll(3);
    expect(screen.getByText(/Status update ·/)).toBeTruthy();
    expect(screen.getByText(/Hint 1 ·/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Resolve|Watching/ }).length).toBeGreaterThan(0);
    expect(onEnded).not.toHaveBeenCalled();
  });

  it("states without page, during the watch, and ended don't crash", async () => {
    const quiet = {
      ...state,
      state: "ready",
      clock: { t: null, time_left_s: null, scene_time: null },
      page: { fired: false, title: null, acknowledged: false, acknowledged_t: null },
      hints: { used: 0, total: 2, next_available_in_s: null },
      feed: { cursor: 0, items: [] },
    };
    const watching = { ...state, state: "watching", resolve: { attempts: 2, wrong: 1, watch_ends_in_s: 200 } };
    const ended = { ...state, state: "ended", end_reason: "resolved" };
    answers = [quiet, watching, ended];
    const onEnded = vi.fn();
    render(<Incident gameId="Zo6nEXJRVS0qCy4VkP7JIQ" onEnded={onEnded} />);
    await poll(4);
    expect(onEnded).toHaveBeenCalledTimes(1);
  });
});
