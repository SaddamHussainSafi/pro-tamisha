import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { ApiError } from "../../api";
import { useAuth } from "../../auth";
import { CATEGORIES, CATEGORY_ICON } from "../../lib/notifications";
import { notificationsApi, type Category, type NotificationPrefs, type NotificationPrefsIn } from "../../lib/notificationsApi";
import { Icon, type IconName } from "../../ui/Icon";
import { ErrorState, LoadingRows } from "../../ui/primitives";
import { useNotifications } from "./NotificationProvider";

type Msg = { tone: "ok" | "error"; text: string } | null;

function Toggle({ checked, onChange, label, disabled, describedBy }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean; describedBy?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50 ${checked ? "border-brand-primary bg-brand-primary" : "border-line bg-line"}`}
    >
      <span aria-hidden className={`inline-block size-5 rounded-full bg-white transition-transform duration-200 ${checked ? "translate-x-[1.45rem]" : "translate-x-1"}`} />
    </button>
  );
}

function Row({ icon, title, hint, control, children }: { icon: IconName; title: string; hint?: ReactNode; control: (hintId: string | undefined) => ReactNode; children?: ReactNode }) {
  const id = useId();
  return (
    <div className="flex items-start gap-4 py-4">
      <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-ctl bg-subtle text-ink">
        <Icon name={icon} size={19} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        {hint && (
          <div id={id} className="mt-0.5 text-sm text-ink-muted">
            {hint}
          </div>
        )}
        {children}
      </div>
      <div className="pt-1">{control(hint ? id : undefined)}</div>
    </div>
  );
}

const toInput = (p: NotificationPrefs): NotificationPrefsIn => ({
  in_app: p.in_app,
  desktop: p.desktop,
  email: p.email,
  sound: p.sound,
  grouped: p.grouped,
  quiet_hours: p.quiet_hours,
  quiet_start: p.quiet_start,
  quiet_end: p.quiet_end,
  categories: p.categories,
  version: p.version,
});

/** Settings › Notifications: in-app, desktop, email, sound, quiet hours, grouping, per category. */
export function NotificationPreferences() {
  const { session } = useAuth();
  const { setPrefs: share, desktop, requestDesktop } = useNotifications();
  const [saved, setSaved] = useState<NotificationPrefs | null>(null);
  const [draft, setDraft] = useState<NotificationPrefsIn | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  const load = useCallback(() => {
    setState("loading");
    notificationsApi.prefs().then(
      (p) => {
        setSaved(p);
        setDraft(toInput(p));
        setState("ready");
      },
      () => setState("error"),
    );
  }, []);
  useEffect(load, [load]);

  if (state === "loading" || (!draft && state !== "error"))
    return (
      <div className="card p-6">
        <LoadingRows rows={5} cols={2} />
      </div>
    );
  if (state === "error" || !draft || !saved)
    return (
      <div className="card p-6">
        <ErrorState title="Couldn't load your notification settings" onRetry={load} />
      </div>
    );

  const set = (p: Partial<NotificationPrefsIn>) => {
    setMsg(null);
    setDraft({ ...draft, ...p });
  };
  const setCat = (c: Category, ch: "in_app" | "email" | "desktop", v: boolean) => set({ categories: { ...draft.categories, [c]: { ...draft.categories[c], [ch]: v } } });
  const dirty = JSON.stringify(draft) !== JSON.stringify(toInput(saved));

  const save = async (next = draft) => {
    setBusy(true);
    setMsg(null);
    try {
      const p = await notificationsApi.savePrefs(next);
      setSaved(p);
      setDraft(toInput(p));
      share(p);
      setMsg({ tone: "ok", text: "Notification settings saved." });
    } catch (e) {
      if (e instanceof ApiError && e.code === "VERSION_CONFLICT") {
        setMsg({ tone: "error", text: "These settings were changed in another tab. The latest version is loaded; make your change again." });
        load();
      } else setMsg({ tone: "error", text: e instanceof ApiError ? e.message : "Couldn't save. Try again." });
    } finally {
      setBusy(false);
    }
  };

  const allowDesktop = async () => {
    const s = await requestDesktop();
    if (s === "granted") void save({ ...draft, desktop: true });
    else if (s === "denied") setMsg({ tone: "error", text: "Your browser blocked notifications for this site. You can allow them in the browser's site settings." });
  };
  const testDesktop = () => {
    try {
      new Notification("Pro Trading", { body: "Desktop notifications are working.", icon: "/brand/tamisha-icon-192.png", tag: "pt-test" });
    } catch {
      setMsg({ tone: "error", text: "This browser didn't show the test notification." });
    }
  };

  const desktopHint =
    desktop === "unsupported"
      ? "This browser can't show desktop notifications."
      : desktop === "denied"
        ? "Blocked in your browser. Allow notifications for this site in the browser's site settings, then come back."
        : desktop === "default"
          ? "Your browser will ask for permission first. Optional — the inbox works without it."
          : "Shown when Pro Trading is in the background. Quiet hours hold them back.";

  return (
    <div className="space-y-5">
      <section aria-labelledby="np-delivery" className="card p-6">
        <h2 id="np-delivery" className="text-xl font-semibold tracking-tight">
          How you're notified
        </h2>
        <div className="mt-2 divide-y divide-line">
          <Row icon="bell" title="In-app notifications" hint="The bell, the panel and the Notification Center. Critical alerts always arrive." control={(d) => <Toggle checked={draft.in_app} onChange={(v) => set({ in_app: v })} label="In-app notifications" describedBy={d} />} />
          <Row
            icon="monitor"
            title="Desktop notifications"
            hint={desktopHint}
            control={(d) =>
              desktop === "default" && !draft.desktop ? (
                <button type="button" className="btn-ghost whitespace-nowrap px-4 py-2" onClick={() => void allowDesktop()} aria-describedby={d}>
                  Allow…
                </button>
              ) : (
                <div className="flex items-center gap-2">
                  {desktop === "granted" && draft.desktop && (
                    <button type="button" className="cursor-pointer rounded-full px-3 py-1.5 text-sm font-medium text-ink-muted hover:bg-subtle hover:text-ink" onClick={testDesktop}>
                      Test
                    </button>
                  )}
                  <Toggle checked={draft.desktop && desktop === "granted"} disabled={desktop !== "granted"} onChange={(v) => set({ desktop: v })} label="Desktop notifications" describedBy={d} />
                </div>
              )
            }
          />
          <Row icon="mail" title="Email" hint={<>Also send each notification to {session?.employee.email ?? "your email address"}. Quiet hours hold back all but critical ones.</>} control={(d) => <Toggle checked={draft.email} onChange={(v) => set({ email: v })} label="Email notifications" describedBy={d} />} />
          <Row icon="volume" title="Sound" hint="A soft chime when something new arrives." control={(d) => <Toggle checked={draft.sound} onChange={(v) => set({ sound: v })} label="Notification sound" describedBy={d} />} />
          <Row icon="layers" title="Group related alerts" hint="Three or more in a row from one area fold into a stack." control={(d) => <Toggle checked={draft.grouped} onChange={(v) => set({ grouped: v })} label="Group related alerts" describedBy={d} />} />
          <Row
            icon="moon"
            title="Quiet hours"
            hint="No banners, sounds, desktop alerts or emails in this window (company time). Critical alerts still come through."
            control={(d) => <Toggle checked={draft.quiet_hours} onChange={(v) => set({ quiet_hours: v })} label="Quiet hours" describedBy={d} />}
          >
            {draft.quiet_hours && (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
                <label className="flex items-center gap-2">
                  From
                  <input type="time" className="input w-32 py-2" value={draft.quiet_start} onChange={(e) => set({ quiet_start: e.target.value })} required />
                </label>
                <label className="flex items-center gap-2">
                  to
                  <input type="time" className="input w-32 py-2" value={draft.quiet_end} onChange={(e) => set({ quiet_end: e.target.value })} required />
                </label>
              </div>
            )}
          </Row>
        </div>
      </section>

      <section aria-labelledby="np-cats" className="card p-6">
        <h2 id="np-cats" className="text-xl font-semibold tracking-tight">
          What you hear about
        </h2>
        <p className="mt-1 text-sm text-ink-muted">You only ever get notifications for areas your role can see.</p>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[30rem] text-sm">
            <thead>
              <tr className="text-left text-ink-muted">
                <th scope="col" className="pb-2 font-medium">Category</th>
                <th scope="col" className="w-20 pb-2 text-center font-medium">In-app</th>
                <th scope="col" className="w-20 pb-2 text-center font-medium">Email</th>
                <th scope="col" className="w-20 pb-2 text-center font-medium">Desktop</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line border-t border-line">
              {CATEGORIES.map((c) => {
                const label = saved.category_labels[c] ?? c;
                const locked = c === "security" && !saved.can_mute_security;
                return (
                  <tr key={c}>
                    <th scope="row" className="py-3 pr-3 text-left font-medium">
                      <span className="flex items-center gap-2.5">
                        <Icon name={CATEGORY_ICON[c]} size={17} className="text-ink-muted" />
                        {label}
                      </span>
                      {locked && <span className="mt-0.5 block pl-7 text-xs font-normal text-ink-muted">Always on for your account's safety.</span>}
                    </th>
                    {(["in_app", "email", "desktop"] as const).map((ch) => (
                      <td key={ch} className="py-3 text-center">
                        <input
                          type="checkbox"
                          className="size-5 cursor-pointer accent-[var(--color-brand-primary)] disabled:cursor-not-allowed"
                          checked={locked && ch === "in_app" ? true : draft.categories[c][ch]}
                          disabled={(locked && ch === "in_app") || (ch === "email" && !draft.email) || (ch === "desktop" && !draft.desktop) || (ch !== "in_app" && !draft.categories[c].in_app)}
                          onChange={(e) => setCat(c, ch, e.target.checked)}
                          aria-label={`${label}: ${ch === "in_app" ? "in-app" : ch}`}
                        />
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" disabled={!dirty || busy} onClick={() => void save()}>
          {busy ? "Saving…" : "Save changes"}
        </button>
        {dirty && (
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => setDraft(toInput(saved))}>
            Discard
          </button>
        )}
        {msg && (
          <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
            {msg.text}
          </p>
        )}
      </div>
    </div>
  );
}
