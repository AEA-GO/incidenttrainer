export function LevelTag({ level, minutes }: { level: string; minutes: number }) {
  return (
    <span className="font-mono text-xs text-faint">
      {level === "junior" ? "Junior · terminal" : "Intern · buttons"} · ~{minutes} min
    </span>
  );
}
