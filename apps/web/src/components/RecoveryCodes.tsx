import { useState } from "react";
import { Icon } from "../ui/Icon";

/** One-time recovery codes, shown once: copy, download as a text file, then confirm saved. */
export function RecoveryCodes({ codes, email, onDone, doneLabel = "I saved my codes" }: { codes: string[]; email: string; onDone: () => void; doneLabel?: string }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = `Pro Trading recovery codes for ${email}\nEach code works once. Keep them somewhere safe.\n\n${codes.join("\n")}\n`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "pro-trading-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4" data-testid="recovery-codes">
      <div className="alert-warning space-y-1">
        <p className="font-medium">Save these recovery codes now. They are shown only once.</p>
        <p>If you lose your phone, each code lets you sign in one time instead of a 6-digit code.</p>
      </div>
      <ul aria-label="Recovery codes" className="grid grid-cols-1 gap-2 rounded-ctl border border-line bg-subtle p-4 sm:grid-cols-2">
        {codes.map((c) => (
          <li key={c} className="num text-center font-mono text-sm tracking-wide select-all">
            {c}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-ghost" onClick={() => void copy()}>
          <Icon name={copied ? "check" : "file"} size={16} />
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" className="btn-ghost" onClick={download}>
          <Icon name="download" size={16} />
          Download
        </button>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="size-4 accent-brand-primary" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        I stored these codes somewhere safe
      </label>
      <button type="button" className="btn-primary w-full sm:w-auto" disabled={!saved} onClick={onDone}>
        {doneLabel}
      </button>
    </div>
  );
}
