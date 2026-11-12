import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { OPEN_SEARCH_EVENT, openSearch } from "../lib/search";
import { CommandPalette } from "./search/CommandPalette";
import { Icon } from "../ui/Icon";

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);


/**
 * The search box in the top bar. Focusing it (click, Ctrl/⌘ K, or "/" on screens without their
 * own search) opens universal search: records from every module plus the screens it used to
 * jump to ("Go to"). The palette is portalled to <body>, so it also works on phones, where this
 * box is hidden and the header's search button opens it instead.
 */
export const QuickJump = forwardRef<HTMLInputElement>(function QuickJump(_, ref) {
  const input = useRef<HTMLInputElement>(null);
  useImperativeHandle(ref, () => input.current as HTMLInputElement, []);
  const [open, setOpen] = useState(false);
  const [seed, setSeed] = useState("");

  useEffect(() => {
    const show = () => {
      setSeed("");
      setOpen(true);
    };
    window.addEventListener(OPEN_SEARCH_EVENT, show);
    return () => window.removeEventListener(OPEN_SEARCH_EVENT, show);
  }, []);

  return (
    <div className="relative">
      <label className="relative block">
        <span className="sr-only">Search everything</span>
        <Icon name="search" size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-muted" />
        <input
          ref={input}
          type="text"
          className="input w-64 cursor-pointer rounded-full border-transparent py-2.5 pl-11 pr-16 xl:w-72"
          placeholder="Search or go to…"
          value=""
          readOnly
          onFocus={() => {
            setSeed("");
            setOpen(true);
          }}
          onKeyDown={(e) => {
            // A key typed while the box has focus (palette closed) starts the search with it.
            if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
              e.preventDefault();
              setSeed(e.key);
              setOpen(true);
            } else if (e.key === "Enter" || e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
            }
          }}
          onClick={() => setOpen(true)}
        />
        <kbd aria-hidden className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-line bg-subtle px-1.5 py-px text-[11px] text-ink-muted">
          {MAC ? "⌘K" : "Ctrl K"}
        </kbd>
      </label>
      <CommandPalette
        open={open}
        initial={seed}
        onClose={() => {
          setOpen(false);
          input.current?.blur();
        }}
      />
    </div>
  );
});

/** Phone header button (the box above is hidden below md). */
export function SearchButton() {
  return (
    <button type="button" className="icon-btn border-transparent" onClick={openSearch} aria-label="Search">
      <Icon name="search" />
    </button>
  );
}
