import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "./Icon";

export type MenuAction =
  | { label: string; icon?: IconName; to: string; newTab?: boolean }
  | { label: string; icon?: IconName; onSelect: () => void | Promise<void> };

/**
 * "⋯" button with a small menu (menu-button pattern): Enter/Space/↓ opens it on the first item,
 * ↑/↓ move, Esc or Tab or a click outside closes it, and focus returns to the button.
 */
export function MoreMenu({ label, actions }: { label: string; actions: MenuAction[] }) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();

  const items = () => Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) btn.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    items()[0]?.focus();
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!menu.current?.contains(t) && !btn.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  function onMenuKey(e: KeyboardEvent) {
    const list = items();
    const i = list.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = list.length;
      list[(i + (e.key === "ArrowDown" ? 1 : -1) + n) % n]?.focus();
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      list[e.key === "Home" ? 0 : list.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  const item = "flex w-full cursor-pointer items-center gap-2.5 rounded-[10px] px-3 py-2 text-left text-[13px] text-ink outline-none transition-colors duration-150 hover:bg-subtle focus-visible:bg-subtle";
  return (
    <div className="relative">
      <button
        ref={btn}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className="grid size-7 cursor-pointer place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink aria-expanded:bg-subtle aria-expanded:text-ink"
      >
        <Icon name="more" size={18} />
      </button>
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKey}
          className="absolute right-0 top-full z-30 mt-1.5 w-48 animate-pop rounded-ctl border border-line bg-card p-1.5 shadow-[0_8px_24px_rgb(24_24_27/0.12)]"
        >
          {actions.map((a) =>
            "to" in a ? (
              <Link
                key={a.label}
                role="menuitem"
                tabIndex={-1}
                to={a.to}
                target={a.newTab ? "_blank" : undefined}
                rel={a.newTab ? "noopener" : undefined}
                className={item}
                onClick={() => close(false)}
              >
                {a.icon && <Icon name={a.icon} size={16} className="text-ink-muted" />}
                {a.label}
              </Link>
            ) : (
              <button
                key={a.label}
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={item}
                onClick={() => {
                  void a.onSelect();
                  close();
                }}
              >
                {a.icon && <Icon name={a.icon} size={16} className="text-ink-muted" />}
                {a.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
