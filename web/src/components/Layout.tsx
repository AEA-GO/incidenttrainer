import type { ReactNode } from "react";
import { Link } from "react-router";

export const GITHUB = "https://github.com/AEA-GO/incidenttrainer";

export function Header() {
  return (
    <header className="border-b border-line">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <Link to="/" className="font-display text-sm font-black tracking-tight">
          IncidentTrainer
        </Link>
        <nav className="flex gap-6 text-sm text-muted">
          <a href="/#how" className="hover:text-text">How it works</a>
          <a href="/#teams" className="hover:text-text">Teams and schools</a>
          <a href={GITHUB} className="hover:text-text">GitHub</a>
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-24 border-t border-line">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6 text-sm text-faint">
        <p>
          <span className="font-display font-black text-text">IncidentTrainer</span>
          <span className="ml-4">Free during the beta.</span>
        </p>
        <a href={GITHUB} className="underline hover:text-text">GitHub</a>
      </div>
    </footer>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <Header />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6">{children}</main>
      <Footer />
    </div>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="font-mono text-xs tracking-wide text-faint">{children}</p>;
}

export function Notice({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "bad" }) {
  const color = tone === "bad" ? "border-bad/50 text-text" : "border-line text-muted";
  return <div className={`rounded border ${color} bg-panel px-4 py-3 text-sm`}>{children}</div>;
}

export function Button({
  children,
  kind = "primary",
  ...rest
}: { kind?: "primary" | "plain" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const look =
    kind === "primary"
      ? "bg-accent text-ink hover:bg-accent-strong disabled:opacity-50"
      : "border border-line text-text hover:border-muted disabled:opacity-50";
  return (
    <button
      {...rest}
      className={`rounded px-5 py-2.5 font-display text-sm font-black ${look} ${rest.className ?? ""}`}
    >
      {children}
    </button>
  );
}
