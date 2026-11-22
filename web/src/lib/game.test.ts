import { afterEach, describe, expect, it, vi } from "vitest";
import { game } from "./game";

afterEach(() => vi.unstubAllGlobals());

describe("game calls through the gateway", () => {
  it("Give up sends the confirmation the agent requires", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ state: "ended" }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await game.giveUp("Zo6nEXJRVS0qCy4VkP7JIQ");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/s/Zo6nEXJRVS0qCy4VkP7JIQ/api/v1/give-up");
    expect(JSON.parse(init.body)).toEqual({ confirm: true });
  });

  it("agent errors come back with their message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "wrong_state", message: "The game is over." } }), { status: 409 }),
      ),
    );
    await expect(game.ack("x")).rejects.toMatchObject({ code: "wrong_state", message: "The game is over." });
  });
});
