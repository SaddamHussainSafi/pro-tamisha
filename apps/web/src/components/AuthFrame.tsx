import type { ReactNode } from "react";
import { Wordmark } from "./Wordmark";

/** Centered card used by every signed-out screen (sign in, two-step, links). */
export function AuthFrame({ title, subtitle, children, labelledBy = "auth-title" }: { title: string; subtitle?: ReactNode; children: ReactNode; labelledBy?: string }) {
  return (
    <main className="grid min-h-full place-items-center p-6">
      <div className="w-full max-w-sm animate-enter">
        <div className="mb-6 flex justify-center">
          <Wordmark />
        </div>
        <section className="card space-y-4 p-6" aria-labelledby={labelledBy}>
          <div>
            <h1 id={labelledBy} className="text-lg font-semibold">
              {title}
            </h1>
            {subtitle && <p className="text-sm text-ink-muted">{subtitle}</p>}
          </div>
          {children}
        </section>
      </div>
    </main>
  );
}

/** Password + repeat, with the same rule as the server and a clear message. */
export function NewPasswordFields({
  value,
  again,
  onChange,
  onAgain,
  idPrefix = "np",
  label = "New password",
}: {
  value: string;
  again: string;
  onChange: (v: string) => void;
  onAgain: (v: string) => void;
  idPrefix?: string;
  label?: string;
}) {
  return (
    <>
      <div>
        <label className="label" htmlFor={`${idPrefix}-new`}>
          {label}
        </label>
        <input id={`${idPrefix}-new`} type="password" autoComplete="new-password" className="input" value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={`${idPrefix}-hint`} />
        <p id={`${idPrefix}-hint`} className="mt-1 text-sm text-ink-muted">
          At least 12 characters. A short sentence is easy to remember.
        </p>
      </div>
      <div>
        <label className="label" htmlFor={`${idPrefix}-again`}>
          {label} again
        </label>
        <input id={`${idPrefix}-again`} type="password" autoComplete="new-password" className="input" value={again} onChange={(e) => onAgain(e.target.value)} />
      </div>
    </>
  );
}
