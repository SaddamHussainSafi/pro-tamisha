import { highlight } from "../../lib/search";

/** Text with the searched words marked (accent / case-insensitive). */
export function Highlight({ text, terms }: { text: string; terms: string[] }) {
  return (
    <>
      {highlight(text, terms).map((s, i) =>
        s.hit ? (
          <mark key={i} className="rounded-sm bg-brand-accent/15 px-px font-semibold text-ink">
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}
