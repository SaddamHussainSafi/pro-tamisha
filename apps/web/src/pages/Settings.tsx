import { useAuth } from "../auth";
import { CardTitle, PageHeader } from "../ui/primitives";

/** Settings › My account. Staff users and password changes are added next. */
export function SettingsPage() {
  const { session } = useAuth();
  if (!session) return null;
  const e = session.employee;
  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle="Your account." />
      <section className="card space-y-4 p-6" aria-labelledby="set-account">
        <CardTitle id="set-account" title="My account" />
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-ink-muted">Name</dt>
          <dd>{e.display_name}</dd>
          <dt className="text-ink-muted">Email</dt>
          <dd>{e.email}</dd>
          <dt className="text-ink-muted">Two-step sign-in</dt>
          <dd>{session.mfa_enrolled ? "On" : "Off"}</dd>
        </dl>
      </section>
    </div>
  );
}
