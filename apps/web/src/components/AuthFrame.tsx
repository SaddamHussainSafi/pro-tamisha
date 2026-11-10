import type { ReactNode } from "react";

/**
 * Signed-out layout (sign in, two-step, invitation and reset links): one large card with the
 * Tamisha photo panel on the left and the form on the right. Phones get a short photo strip on
 * top instead. Photo: owner-supplied sign-in design, 2026-11-09 (public/brand/sign-in-hero.webp).
 */
export function AuthShell({ children, labelledBy }: { children: ReactNode; labelledBy: string }) {
  return (
    <main className="grid min-h-full place-items-center bg-canvas px-3 py-6 sm:p-8">
      <section aria-labelledby={labelledBy} className="w-full max-w-[64rem] animate-enter overflow-hidden rounded-card bg-card shadow-[0_8px_24px_rgb(24_24_27/0.12)] lg:grid lg:min-h-[40rem] lg:grid-cols-[1.05fr_1fr]">
        <div className="relative hidden flex-col bg-brand-primary text-white lg:flex">
          <div className="px-10 pb-9 pt-11">
            <p className="text-[34px] font-bold leading-[1.15] tracking-tight">
              Wholesale Produce
              <br />
              Trading &amp; Distribution
            </p>
            <span aria-hidden className="mt-5 block h-1 w-16 rounded-full bg-apple-200" />
            <p className="mt-4 text-sm font-medium tracking-[0.3em] text-white/90">SOURCE · TRADE · SUPPLY</p>
          </div>
          <img src="/brand/sign-in-hero.webp" alt="" width={560} height={412} className="min-h-0 w-full flex-1 object-cover" draggable={false} />
        </div>
        <img src="/brand/sign-in-hero.webp" alt="" width={560} height={412} className="h-32 w-full object-cover sm:h-40 lg:hidden" draggable={false} />
        <div className="flex flex-col justify-center px-6 py-8 sm:px-12 sm:py-12">
          <div className="mx-auto w-full max-w-md">
            <div className="mb-8 flex justify-center">
              <img src="/brand/tamisha-trading-logo.webp" alt="Tamisha Trading Inc." width={800} height={222} className="h-14 w-auto max-w-full select-none sm:h-16" draggable={false} />
            </div>
            {children}
          </div>
          <p className="mt-10 text-center text-xs text-ink-muted">Tamisha Trading Inc. · Pro Trading · Staff only</p>
        </div>
      </section>
    </main>
  );
}

/** Heading block shared by the signed-out screens. */
export function AuthHeading({ id, title, subtitle }: { id: string; title: string; subtitle?: ReactNode }) {
  return (
    <div className="mb-7">
      <h1 id={id} className="text-[32px] font-bold leading-tight tracking-tight sm:text-[36px]">
        {title}
      </h1>
      {subtitle && <p className="mt-1.5 text-base text-ink-muted">{subtitle}</p>}
    </div>
  );
}

/** A signed-out screen: heading, then its form. */
export function AuthFrame({ title, subtitle, children, labelledBy = "auth-title" }: { title: string; subtitle?: ReactNode; children: ReactNode; labelledBy?: string }) {
  return (
    <AuthShell labelledBy={labelledBy}>
      <AuthHeading id={labelledBy} title={title} subtitle={subtitle} />
      <div className="space-y-4">{children}</div>
    </AuthShell>
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
