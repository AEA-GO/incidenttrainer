import { describe, expect, it } from "vitest";
import { HIGH_WATER, type SessionState, TtydSession, frame, hello, terminalUrl } from "./ttyd";

const text = (data: Uint8Array) => new TextDecoder().decode(data);

class FakeSocket {
  binaryType = "blob";
  readyState = 0;
  sent: string[] = [];
  closed = false;
  onopen: ((e: unknown) => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: unknown) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  constructor(public url: string) {}
  send(data: Uint8Array) {
    this.sent.push(text(data));
  }
  close() {
    this.closed = true;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  output(s: string | Uint8Array) {
    const body = typeof s === "string" ? new TextEncoder().encode(s) : s;
    const bytes = new Uint8Array(body.length + 1);
    bytes[0] = 0x30;
    bytes.set(body, 1);
    this.onmessage?.({ data: bytes.buffer });
  }
  drop() {
    this.readyState = 3;
    this.onclose?.({});
  }
}

function setup() {
  const sockets: FakeSocket[] = [];
  const out: string[] = [];
  const drawn: (() => void)[] = [];
  const states: [SessionState, boolean][] = [];
  const session = new TtydSession(
    "wss://x/s/g/term/ws",
    {
      output: (data, done) => {
        out.push(text(data));
        drawn.push(done);
      },
      state: (s, ever) => states.push([s, ever]),
    },
    (url) => {
      sockets.push(new FakeSocket(url));
      return sockets[sockets.length - 1];
    },
  );
  return { session, sockets, out, drawn, states };
}

describe("ttyd protocol", () => {
  it("builds the gateway's terminal address", () => {
    expect(terminalUrl("Zo6nEXJRVS0qCy4VkP7JIQ", { protocol: "https:", host: "drer7le45ultl.cloudfront.net" })).toBe(
      "wss://drer7le45ultl.cloudfront.net/s/Zo6nEXJRVS0qCy4VkP7JIQ/term/ws",
    );
    expect(terminalUrl("g", { protocol: "http:", host: "localhost:5173" })).toBe("ws://localhost:5173/s/g/term/ws");
  });

  it("frames every message with ttyd's command character", () => {
    expect(text(frame("0", "ls\r"))).toBe("0ls\r");
    expect(text(frame("2"))).toBe("2");
    expect(JSON.parse(text(hello({ columns: 120, rows: 30 })))).toEqual({ AuthToken: "", columns: 120, rows: 30 });
  });

  it("says hello on open, then sends keys and sizes", () => {
    const { session, sockets, states } = setup();
    session.connect({ columns: 80, rows: 24 });
    const socket = sockets[0];
    expect(socket.binaryType).toBe("arraybuffer");
    session.input("ignored before open");
    socket.open();
    session.input("df -h\r");
    session.resize({ columns: 100, rows: 40 });
    expect(socket.sent[0]).toBe('{"AuthToken":"","columns":80,"rows":24}');
    expect(socket.sent.slice(1)).toEqual(["0df -h\r", '1{"columns":100,"rows":40}']);
    expect(states).toEqual([
      ["connecting", false],
      ["open", true],
    ]);
  });

  it("passes output on (UTF-8 bytes intact) and ignores titles and preferences", () => {
    const { session, sockets, out } = setup();
    session.connect({ columns: 80, rows: 24 });
    sockets[0].open();
    sockets[0].output("Filesystem  Use%\r\n/dev/nvme1n1 100% ✓");
    sockets[0].onmessage?.({ data: new TextEncoder().encode("1bash (teapot-prod-1)").buffer });
    sockets[0].onmessage?.({ data: new TextEncoder().encode("2{ }").buffer });
    expect(out).toEqual(["Filesystem  Use%\r\n/dev/nvme1n1 100% ✓"]);
  });

  it("pauses the shell while the screen is behind, and resumes once it caught up", () => {
    const { session, sockets, drawn } = setup();
    session.connect({ columns: 80, rows: 24 });
    sockets[0].open();
    const chunk = new Uint8Array(HIGH_WATER / 2 + 1).fill(0x61);
    sockets[0].output(chunk);
    sockets[0].output(chunk);
    expect(sockets[0].sent.at(-1)).toBe("2");
    drawn[0]();
    expect(sockets[0].sent.at(-1)).toBe("2"); // still more than the low mark
    drawn[1]();
    expect(sockets[0].sent.at(-1)).toBe("3");
  });

  it("tells a server that never answered apart from a closed terminal", () => {
    const { session, sockets, states } = setup();
    session.connect({ columns: 80, rows: 24 });
    sockets[0].drop();
    expect(states.at(-1)).toEqual(["closed", false]);
    session.connect({ columns: 80, rows: 24 });
    sockets[1].open();
    sockets[1].drop();
    expect(states.at(-1)).toEqual(["closed", true]);
  });

  it("ignores an old connection after reconnecting", () => {
    const { session, sockets, out, states } = setup();
    session.connect({ columns: 80, rows: 24 });
    sockets[0].open();
    session.connect({ columns: 80, rows: 24 });
    expect(sockets[0].closed).toBe(true);
    sockets[0].output("late");
    sockets[0].drop();
    expect(out).toEqual([]);
    expect(states.at(-1)).toEqual(["connecting", false]);
  });
});
