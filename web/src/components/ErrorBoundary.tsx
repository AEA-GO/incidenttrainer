import { Component, type ErrorInfo, type ReactNode } from "react";

// A bug on one screen must never leave the player with a blank page. The game itself runs
// on the server, so reloading the page continues it where it was.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("IncidentTrainer screen error:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="mx-auto max-w-xl px-6 py-24">
        <p className="font-mono text-xs text-faint">Screen error</p>
        <h1 className="mt-2 font-display text-2xl font-black">This screen hit a bug.</h1>
        <p className="mt-3 text-muted">
          If you were playing, your game is still running on its server: reload to continue
          where you were.
        </p>
        <pre className="mt-4 overflow-auto rounded border border-line bg-panel p-3 font-mono text-xs text-muted">
          {error.message}
        </pre>
        <button
          onClick={() => window.location.reload()}
          className="mt-6 rounded bg-accent px-5 py-2.5 font-display text-sm font-black text-ink"
        >
          Reload
        </button>
      </div>
    );
  }
}
