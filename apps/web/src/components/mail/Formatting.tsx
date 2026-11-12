import { useEffect, useRef, useState, type ReactNode } from "react";
import { EMOJI, FONTS, SIZES, SWATCHES } from "../../lib/mail";
import { Icon, type IconName } from "../../ui/Icon";

/* The compose formatting bar. Everything is applied as inline CSS (styleWithCSS) or plain tags
   that the server's allow-list keeps (color, background-color, font-family, font-size,
   text-align, blockquote, ol/ul, s, a), so what you see is what is sent. */

const btn = "grid size-[28px] shrink-0 cursor-pointer place-items-center rounded-md text-ink-muted hover:bg-line/70 hover:text-ink disabled:cursor-default disabled:opacity-40";

/** A popover anchored above its button (the bar sits at the bottom of the window). */
function Pop({ label, button, children, wide = false }: { label: string; button: ReactNode; children: (close: () => void) => ReactNode; wide?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
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
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc, true);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button type="button" className={`${btn} ${wide ? "!flex w-auto items-center gap-0.5 px-1.5 text-xs" : ""}`} aria-label={label} title={label} aria-haspopup="true" aria-expanded={open} onMouseDown={(e) => e.preventDefault()} onClick={() => setOpen((o) => !o)}>
        {button}
      </button>
      {open && (
        <div role="group" aria-label={label} className="absolute bottom-10 left-0 z-50 animate-pop rounded-xl border border-line bg-card p-2 shadow-[0_8px_24px_rgb(24_24_27/0.16)]">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

function B({ icon, label, onClick, children, pressed }: { icon?: IconName; label: string; onClick: () => void; children?: ReactNode; pressed?: boolean }) {
  return (
    <button type="button" className={`${btn} ${pressed ? "bg-line/70 text-ink" : ""}`} aria-label={label} title={label} aria-pressed={pressed} onMouseDown={(e) => e.preventDefault()} onClick={onClick}>
      {children ?? (icon && <Icon name={icon} size={16} />)}
    </button>
  );
}

const Sep = () => <span className="mx-px h-4 w-px shrink-0 bg-line" aria-hidden />;

export function FormatBar({ exec }: { exec: (cmd: string, arg?: string) => void }) {
  return (
    <div className="mx-3 flex flex-wrap items-center gap-px rounded-lg bg-subtle px-1 py-0.5" role="toolbar" aria-label="Formatting">
      <B icon="undo" label="Undo (Ctrl / ⌘ Z)" onClick={() => exec("undo")} />
      <B icon="redo" label="Redo (Ctrl / ⌘ Y)" onClick={() => exec("redo")} />
      <Sep />
      <Pop label="Font" wide button={<><span className="max-w-20 truncate">Sans Serif</span><Icon name="chevronDown" size={12} /></>}>
        {(close) => (
          <ul className="w-44">
            {FONTS.map((f) => (
              <li key={f.label}>
                <button type="button" className="w-full cursor-pointer rounded-md px-3 py-1.5 text-left text-sm hover:bg-subtle" style={{ fontFamily: f.css }} onMouseDown={(e) => e.preventDefault()} onClick={() => (exec("fontName", f.css), close())}>
                  {f.label}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Pop>
      <Pop label="Size" button={<Icon name="textSize" size={16} />}>
        {(close) => (
          <ul className="w-32">
            {SIZES.map((s) => (
              <li key={s.label}>
                <button type="button" className="w-full cursor-pointer rounded-md px-3 py-1 text-left hover:bg-subtle" style={{ fontSize: s.preview }} onMouseDown={(e) => e.preventDefault()} onClick={() => (exec("fontSize", s.value), close())}>
                  {s.label}
                </button>
              </li>
            ))}
          </ul>
        )}
      </Pop>
      <Sep />
      <B label="Bold (Ctrl / ⌘ B)" onClick={() => exec("bold")}>
        <span className="text-sm font-bold">B</span>
      </B>
      <B label="Italic (Ctrl / ⌘ I)" onClick={() => exec("italic")}>
        <span className="font-serif text-sm italic">I</span>
      </B>
      <B label="Underline (Ctrl / ⌘ U)" onClick={() => exec("underline")}>
        <span className="text-sm underline">U</span>
      </B>
      <Pop label="Text colour" button={<span className="text-sm font-semibold underline decoration-2">A</span>}>
        {(close) => (
          <div className="flex gap-4">
            {(["Background colour", "Text colour"] as const).map((which) => (
              <div key={which}>
                <p className="pb-1.5 text-xs text-ink-muted">{which}</p>
                <div className="grid grid-cols-8 gap-1">
                  {SWATCHES.map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-label={`${which} ${c}`}
                      title={c}
                      className="size-5 cursor-pointer rounded-sm border border-line hover:scale-110"
                      style={{ background: c }}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        exec(which === "Text colour" ? "foreColor" : "hiliteColor", c);
                        close();
                      }}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Pop>
      <Sep />
      <Pop label="Align" button={<Icon name="alignLeft" size={16} />}>
        {(close) => (
          <div className="flex gap-0.5">
            <B icon="alignLeft" label="Align left" onClick={() => (exec("justifyLeft"), close())} />
            <B icon="alignCenter" label="Align centre" onClick={() => (exec("justifyCenter"), close())} />
            <B icon="alignRight" label="Align right" onClick={() => (exec("justifyRight"), close())} />
          </div>
        )}
      </Pop>
      <B icon="listNumbered" label="Numbered list" onClick={() => exec("insertOrderedList")} />
      <B icon="menu" label="Bulleted list" onClick={() => exec("insertUnorderedList")} />
      <B icon="outdent" label="Indent less" onClick={() => exec("outdent")} />
      <B icon="indent" label="Indent more" onClick={() => exec("indent")} />
      <B icon="quote" label="Quote" onClick={() => exec("formatBlock", "blockquote")} />
      <B label="Strikethrough" onClick={() => exec("strikeThrough")}>
        <span className="text-sm line-through">S</span>
      </B>
      <B icon="clearFormat" label="Remove formatting" onClick={() => exec("removeFormat")} />
    </div>
  );
}

export function EmojiPicker({ onPick }: { onPick: (e: string) => void }) {
  return (
    <Pop label="Insert emoji" button={<Icon name="smile" size={18} />}>
      {(close) => (
        <div className="grid w-64 grid-cols-8 gap-0.5">
          {EMOJI.map((e) => (
            <button
              key={e}
              type="button"
              className="grid size-7 cursor-pointer place-items-center rounded-md text-lg hover:bg-subtle"
              aria-label={`Insert ${e}`}
              onMouseDown={(ev) => ev.preventDefault()}
              onClick={() => {
                onPick(e);
                close();
              }}
            >
              {e}
            </button>
          ))}
        </div>
      )}
    </Pop>
  );
}
