import { useId, useState } from "react";
import { addrText, parseAddrs, validEmail, type Addr } from "../../lib/mail";
import { Icon } from "../../ui/Icon";

/** To / Cc / Bcc: typed addresses become chips on Enter, comma, semicolon, Tab or leaving the
 * field. Pasting a list splits it. Invalid addresses stay as red chips so they can be fixed. */
export function AddressInput({ label, value, onChange, autoFocus = false }: { label: string; value: Addr[]; onChange: (v: Addr[]) => void; autoFocus?: boolean }) {
  const [text, setText] = useState("");
  const id = useId();
  const commit = (raw = text) => {
    const add = parseAddrs(raw);
    if (add.length) {
      const have = new Set(value.map((a) => a.email.toLowerCase()));
      onChange([...value, ...add.filter((a) => !have.has(a.email.toLowerCase()))]);
    }
    setText("");
  };
  const bad = value.filter((a) => !validEmail(a.email)).length;
  return (
    <div className="flex items-start gap-3 border-b border-line py-1.5 focus-within:border-brand-primary">
      <label htmlFor={id} className="w-12 shrink-0 pt-2 text-sm text-ink-muted">
        {label}
      </label>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5" role="group" aria-label={`${label} recipients`}>
        {value.map((a, i) => {
          const ok = validEmail(a.email);
          return (
            <span key={`${a.email}-${i}`} className={`chip max-w-full py-0.5 pr-1 ${ok ? "border-line bg-subtle text-ink" : "border-danger/40 bg-danger/5 text-danger"}`} title={ok ? addrText(a) : `${a.email} is not a valid email address`}>
              <span className="truncate">{a.name || a.email}</span>
              {!ok && <span className="sr-only"> (not a valid address)</span>}
              <button
                type="button"
                className="grid size-5 cursor-pointer place-items-center rounded-full hover:bg-line"
                aria-label={`Remove ${a.email} from ${label}`}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                <Icon name="x" size={12} />
              </button>
            </span>
          );
        })}
        <input
          id={id}
          type="text"
          inputMode="email"
          autoComplete="off"
          data-autofocus={autoFocus || undefined}
          aria-invalid={bad > 0 || undefined}
          className="min-w-40 flex-1 bg-transparent py-1.5 text-sm outline-none"
          value={text}
          onChange={(e) => {
            const v = e.target.value;
            if (/[,;]\s*$/.test(v)) commit(v);
            else setText(v);
          }}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === "Tab") && text.trim()) {
              if (e.key === "Enter") e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          onPaste={(e) => {
            const t = e.clipboardData.getData("text");
            if (/[,;\n]/.test(t)) {
              e.preventDefault();
              commit(text + t);
            }
          }}
          onBlur={() => text.trim() && commit()}
        />
      </div>
      {bad > 0 && (
        <span className="shrink-0 pt-2 text-xs text-danger" role="alert">
          {bad === 1 ? "1 address is not valid" : `${bad} addresses are not valid`}
        </span>
      )}
    </div>
  );
}
