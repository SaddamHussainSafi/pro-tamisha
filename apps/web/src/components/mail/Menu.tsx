import { useEffect, useId, useRef, useState, type ReactNode } from "react";

export type MenuItem = { label: string; onSelect: () => void; checked?: boolean; hint?: string } | { heading: string } | "divider";

/** Small popover menu (button + role="menu"), closes on outside click, Escape or a choice. */
export function Menu({ label, button, items, align = "left", className = "", up = false }: { label: string; button: ReactNode; items: MenuItem[]; align?: "left" | "right"; className?: string; up?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc, true);
    ref.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus();
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc, true);
    };
  }, [open]);
  const move = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const all = Array.from(ref.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? []);
    const i = all.indexOf(document.activeElement as HTMLElement);
    all[(i + (e.key === "ArrowDown" ? 1 : all.length - 1)) % all.length]?.focus();
  };
  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        title={label}
        className="grid h-9 min-w-9 cursor-pointer place-items-center rounded-full px-1 text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink"
        onClick={() => setOpen((o) => !o)}
      >
        {button}
      </button>
      {open && (
        <div id={id} role="menu" aria-label={label} onKeyDown={move} className={`absolute ${up ? "bottom-10" : "top-10"} z-50 min-w-52 animate-pop rounded-xl border border-line bg-card py-1.5 shadow-[0_8px_24px_rgb(24_24_27/0.14)] ${align === "right" ? "right-0" : "left-0"}`}>
          {items.map((it, i) =>
            it === "divider" ? (
              <div key={i} role="separator" className="my-1 h-px bg-line" />
            ) : "heading" in it ? (
              <p key={i} className="px-4 pb-1 pt-2 text-xs font-medium text-ink-muted">
                {it.heading}
              </p>
            ) : (
              <button
                key={i}
                type="button"
                role={it.checked === undefined ? "menuitem" : "menuitemradio"}
                aria-checked={it.checked}
                className="flex w-full cursor-pointer items-center gap-3 px-4 py-2 text-left text-sm text-ink hover:bg-subtle focus-visible:bg-subtle focus-visible:outline-none"
                onClick={() => {
                  setOpen(false);
                  it.onSelect();
                }}
              >
                {it.checked !== undefined && <span className={`size-2 rounded-full ${it.checked ? "bg-brand-primary" : "bg-transparent"}`} aria-hidden />}
                <span className="flex-1">{it.label}</span>
                {it.hint && <kbd className="font-sans text-xs text-ink-muted">{it.hint}</kbd>}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
