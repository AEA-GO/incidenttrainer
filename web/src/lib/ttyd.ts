// The terminal's wire protocol: ttyd 1.7 (the game server runs Ubuntu's 1.7.4).
// The gateway forwards /s/<game_id>/term/ws to ttyd's /ws, only for labs with a terminal
// and only from our own site (contract 5). The game cookie goes along by itself.
//
// Every frame starts with one command character (ttyd src/server.h):
//   browser -> ttyd: '{' first message {AuthToken, columns, rows} (starts the shell),
//                    '0' keystrokes, '1' resize {columns, rows}, '2' pause, '3' resume
//   ttyd -> browser: '0' output, '1' window title, '2' preferences (both ignored here)
// Each WebSocket gets its own new shell; closing it hangs that shell up.

const encoder = new TextEncoder();

export type Size = { columns: number; rows: number };
export type SessionState = "connecting" | "open" | "closed";

// Pause the shell's output while the screen has this much still to draw (`cat` of a big
// log), resume once it has caught up. Without it the browser tab can freeze.
export const HIGH_WATER = 512 * 1024;
export const LOW_WATER = 128 * 1024;

export function terminalUrl(gameId: string, where: { protocol: string; host: string } = window.location): string {
  const scheme = where.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${where.host}/s/${gameId}/term/ws`;
}

export function frame(command: "0" | "1" | "2" | "3", payload = ""): Uint8Array {
  return encoder.encode(command + payload);
}

export function hello(size: Size): Uint8Array {
  return encoder.encode(JSON.stringify({ AuthToken: "", columns: size.columns, rows: size.rows }));
}

type SocketLike = {
  binaryType: string;
  readyState: number;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  send(data: Uint8Array): void;
  close(): void;
};

export type Handlers = {
  // Draw the bytes, then call `drawn` (xterm's write callback) so the session can
  // pause and resume the shell's output.
  output: (data: Uint8Array, drawn: () => void) => void;
  state: (state: SessionState, everOpened: boolean) => void;
};

const OPEN = 1;

export class TtydSession {
  private socket: SocketLike | null = null;
  private pending = 0;
  private paused = false;
  private opened = false;

  constructor(
    private readonly url: string,
    private readonly handlers: Handlers,
    private readonly makeSocket: (url: string) => SocketLike = (url) =>
      new WebSocket(url, ["tty"]) as unknown as SocketLike,
  ) {}

  connect(size: Size): void {
    this.close();
    this.pending = 0;
    this.paused = false;
    this.opened = false;
    const socket = this.makeSocket(this.url);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.handlers.state("connecting", false);
    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.opened = true;
      socket.send(hello(size));
      this.handlers.state("open", true);
    };
    socket.onmessage = (event) => {
      if (this.socket !== socket || !(event.data instanceof ArrayBuffer)) return;
      const bytes = new Uint8Array(event.data);
      if (bytes.length === 0 || bytes[0] !== 0x30) return; // only '0' (output) matters
      const data = bytes.subarray(1);
      this.pending += data.length;
      if (!this.paused && this.pending > HIGH_WATER) {
        this.paused = true;
        this.send(frame("2"));
      }
      this.handlers.output(data, () => {
        this.pending = Math.max(0, this.pending - data.length);
        if (this.paused && this.pending < LOW_WATER) {
          this.paused = false;
          this.send(frame("3"));
        }
      });
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.handlers.state("closed", this.opened);
    };
    socket.onerror = () => undefined; // onclose follows and says what to show
  }

  input(text: string): void {
    this.send(frame("0", text));
  }

  resize(size: Size): void {
    this.send(frame("1", JSON.stringify({ columns: size.columns, rows: size.rows })));
  }

  close(): void {
    const socket = this.socket;
    this.socket = null;
    socket?.close();
  }

  private send(data: Uint8Array): void {
    if (this.socket?.readyState === OPEN) this.socket.send(data);
  }
}
