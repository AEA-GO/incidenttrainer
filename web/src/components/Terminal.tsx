// The terminal panel for Junior labs: xterm.js on top of ttyd (lib/ttyd.ts).
// Loaded only when a lab has a terminal (React.lazy), so Intern labs never download it.
// Kept mounted while hidden, so switching to the dashboards doesn't lose the shell.

import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import { type SessionState, TtydSession, terminalUrl } from "../lib/ttyd";

// ttyd may still be starting when the page fires; try for about 30 s before giving up.
const RETRY_MS = 2000;
const RETRIES = 15;

const THEME = {
  background: "#141311",
  foreground: "#ece8e1",
  cursor: "#f5822a",
  cursorAccent: "#141311",
  selectionBackground: "#4a3a2a",
  black: "#1b1a17",
  red: "#e5534b",
  green: "#57ab5a",
  yellow: "#e3b341",
  blue: "#6cb6ff",
  magenta: "#dcbdfb",
  cyan: "#56d4dd",
  white: "#ece8e1",
  brightBlack: "#6c675f",
  brightRed: "#ff6a69",
  brightGreen: "#6bc46d",
  brightYellow: "#eac55f",
  brightBlue: "#96d0ff",
  brightMagenta: "#e2c5ff",
  brightCyan: "#76e3ea",
  brightWhite: "#ffffff",
};

type View = "starting" | "open" | "closed" | "gave_up";
type Parts = { term: XTerm; fit: FitAddon; reconnect: () => void; stop: () => void };

export default function Terminal({ gameId, active, ended }: { gameId: string; active: boolean; ended: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const parts = useRef<Parts | null>(null);
  const [view, setView] = useState<View>("starting");

  // One xterm and one connection for the life of the panel.
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: '"JetBrains Mono", ui-monospace, monospace',
      fontSize: 13,
      scrollback: 5000,
      theme: THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(element);
    fit.fit();

    let tries = 0;
    let timer: number | undefined;
    let stopped = false;
    const session = new TtydSession(terminalUrl(gameId), {
      output: (data, drawn) => term.write(data, drawn),
      state: (state: SessionState, everOpened: boolean) => {
        if (stopped) return;
        if (state === "open") {
          tries = 0;
          setView("open");
        } else if (state === "closed" && everOpened) {
          setView("closed");
        } else if (state === "closed" && tries < RETRIES) {
          // Closed before it ever opened: ttyd isn't up yet. Try again in a moment.
          tries += 1;
          setView("starting");
          timer = window.setTimeout(connect, RETRY_MS);
        } else if (state === "closed") {
          setView("gave_up");
        }
      },
    });
    function connect() {
      session.connect({ columns: term.cols, rows: term.rows });
    }
    const stop = () => {
      stopped = true;
      window.clearTimeout(timer);
      session.close();
    };
    const typing = term.onData((text) => session.input(text));
    const resizing = term.onResize(({ cols, rows }) => session.resize({ columns: cols, rows }));
    const observer = new ResizeObserver(() => {
      if (element.offsetParent !== null) fit.fit(); // not while the panel is hidden
    });
    observer.observe(element);
    parts.current = {
      term,
      fit,
      reconnect: () => {
        tries = 0;
        setView("starting");
        connect();
      },
      stop,
    };
    connect();
    return () => {
      observer.disconnect();
      typing.dispose();
      resizing.dispose();
      stop();
      term.dispose();
      parts.current = null;
    };
  }, [gameId]);

  useEffect(() => {
    if (ended) parts.current?.stop();
  }, [ended]);

  useEffect(() => {
    const p = parts.current;
    if (!active || !p) return;
    p.fit.fit();
    p.term.focus();
  }, [active]);

  let notice: React.ReactNode = null;
  if (ended) notice = "The terminal closed with the game.";
  else if (view === "starting") notice = "Terminal is starting…";
  else if (view === "closed" || view === "gave_up")
    notice = (
      <>
        {view === "gave_up" ? "The terminal didn't open." : "The terminal closed."}{" "}
        <button type="button" className="text-accent underline" onClick={() => parts.current?.reconnect()}>
          Open a new one
        </button>
      </>
    );

  return (
    <div className="relative h-full w-full bg-ink" translate="no">
      <div ref={box} className="h-full w-full p-2" />
      {notice && (
        <p className="absolute right-3 bottom-3 rounded border border-line bg-panel px-3 py-1.5 font-mono text-xs text-muted">
          {notice}
        </p>
      )}
    </div>
  );
}
