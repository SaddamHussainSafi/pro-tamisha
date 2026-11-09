import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, hasCompanyWide, type RoleCode, type StaffMember } from "../api";
import { useAuth } from "../auth";
import { passwordProblem, roleLabel, ROLES, staffMessage, tempPassword } from "../lib/staff";
import { DataGrid, type Column } from "../ui/DataGrid";
import { CardGrid, EntityCard } from "../ui/CardGrid";
import { ViewToggle } from "../ui/ViewToggle";
import { useViewMode } from "../lib/viewMode";
import { Avatar, CardTitle, ConfirmDialog, EmptyState, ErrorState, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

type Msg = { tone: "error" | "ok"; text: string } | null;
const day = (iso: string) => new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

function Notice({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
      {msg.text}
    </p>
  );
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-sm text-ink-muted">{hint}</p>}
    </div>
  );
}

/** Settings: My account for everyone; Users for Owner / Admin. */
export function SettingsPage() {
  const { session } = useAuth();
  const loc = useLocation();
  if (!session) return null;
  const canManage = hasCompanyWide(session, "employee.manage");
  const tab = canManage && loc.pathname === "/settings/users" ? "users" : "account";
  const tabs = [{ to: "/settings", key: "account", label: "My account" }, ...(canManage ? [{ to: "/settings/users", key: "users", label: "Users" }] : [])];
  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle={canManage ? "Your account and the people who can sign in." : "Your account."} />
      {tabs.length > 1 && (
        <nav aria-label="Settings sections" className="flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <Link
              key={t.key}
              to={t.to}
              aria-current={tab === t.key ? "page" : undefined}
              className={`chip px-4 py-2 text-sm ${tab === t.key ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
            >
              {t.label}
            </Link>
          ))}
        </nav>
      )}
      {tab === "users" ? <Users /> : <MyAccount />}
    </div>
  );
}

/* ---------------------------------------------------------------- my account */

function MyAccount() {
  const { session, refresh } = useAuth();
  const [name, setName] = useState(session?.employee.display_name ?? "");
  const [nameMsg, setNameMsg] = useState<Msg>(null);
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const [pwMsg, setPwMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (!session) return null;
  const e = session.employee;

  async function saveName(ev: FormEvent) {
    ev.preventDefault();
    setNameMsg(null);
    if (!name.trim()) return setNameMsg({ tone: "error", text: "Enter your name." });
    setBusy(true);
    try {
      await api.updateMe({ display_name: name.trim() });
      await refresh();
      setNameMsg({ tone: "ok", text: "Name saved." });
    } catch (err) {
      setNameMsg({ tone: "error", text: staffMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function changePw(ev: FormEvent) {
    ev.preventDefault();
    setPwMsg(null);
    const problem = passwordProblem(pw.next, e.email);
    if (problem) return setPwMsg({ tone: "error", text: problem });
    if (pw.next !== pw.again) return setPwMsg({ tone: "error", text: "The two new passwords don't match." });
    setBusy(true);
    try {
      await api.changePassword({ current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", again: "" });
      setPwMsg({ tone: "ok", text: "Password changed. Other devices were signed out." });
    } catch (err) {
      setPwMsg({ tone: "error", text: staffMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <section className="card space-y-4 p-6" aria-labelledby="set-account">
        <CardTitle id="set-account" title="My account" />
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
          <dt className="text-ink-muted">Email</dt>
          <dd>{e.email}</dd>
          <dt className="text-ink-muted">Two-step sign-in</dt>
          <dd>{session.mfa_enrolled ? <StatusChip tone="success">On</StatusChip> : <StatusChip tone="neutral">Off</StatusChip>}</dd>
        </dl>
        <form className="space-y-3" onSubmit={saveName} aria-label="Your name">
          <Field id="me-name" label="Name">
            <input id="me-name" className="input" value={name} maxLength={200} onChange={(ev) => setName(ev.target.value)} />
          </Field>
          <Notice msg={nameMsg} />
          <div className="flex justify-end">
            <button className="btn-primary" disabled={busy || name.trim() === e.display_name}>
              Save name
            </button>
          </div>
        </form>
        <p className="text-sm text-ink-muted">Lost the phone with your authenticator? Ask another Owner / Admin to reset your two-step sign-in.</p>
      </section>
      <section className="card space-y-4 p-6" aria-labelledby="set-password">
        <CardTitle id="set-password" title="Change password" />
        <form className="space-y-3" onSubmit={changePw} aria-labelledby="set-password">
          <Field id="pw-current" label="Current password">
            <input
              id="pw-current"
              type="password"
              autoComplete="current-password"
              className="input"
              value={pw.current}
              onChange={(ev) => setPw({ ...pw, current: ev.target.value })}
            />
          </Field>
          <Field id="pw-new" label="New password" hint="At least 12 characters. A short sentence is easy to remember.">
            <input id="pw-new" type="password" autoComplete="new-password" className="input" value={pw.next} onChange={(ev) => setPw({ ...pw, next: ev.target.value })} />
          </Field>
          <Field id="pw-again" label="New password again">
            <input id="pw-again" type="password" autoComplete="new-password" className="input" value={pw.again} onChange={(ev) => setPw({ ...pw, again: ev.target.value })} />
          </Field>
          <Notice msg={pwMsg} />
          <div className="flex justify-end">
            <button className="btn-primary" disabled={busy || !pw.current || !pw.next}>
              Change password
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

/* ---------------------------------------------------------------- users */

type Dialog = { kind: "disable" | "enable" | "mfa"; who: StaffMember } | { kind: "password"; who: StaffMember; password: string } | null;

function Users() {
  const { session } = useAuth();
  const [rows, setRows] = useState<StaffMember[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", email: "", role: "SALES_OPERATOR" as RoleCode, password: "" });
  const [editing, setEditing] = useState<{ who: StaffMember; name: string; role: RoleCode } | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [shown, setShown] = useState<{ email: string; password: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const me = session?.employee.id;
  const [view, setView] = useViewMode("users");

  const load = useCallback(async () => {
    try {
      setRows(await api.staff());
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    setBusy(true);
    try {
      await fn();
      setMsg({ tone: "ok", text: ok });
      await load();
      return true;
    } catch (err) {
      setMsg({ tone: "error", text: staffMessage(err) });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(ev: FormEvent) {
    ev.preventDefault();
    const problem = !f.name.trim()
      ? "Enter the person's name."
      : !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.trim())
        ? "Enter a valid email."
        : passwordProblem(f.password, f.email);
    if (problem) return setMsg({ tone: "error", text: problem });
    const done = await run(
      () => api.createStaff({ email: f.email.trim(), display_name: f.name.trim(), role_code: f.role, password: f.password }),
      `${f.name.trim()} can now sign in.`,
    );
    if (done) {
      setShown({ email: f.email.trim(), password: f.password });
      setF({ name: "", email: "", role: "SALES_OPERATOR", password: "" });
      setAdding(false);
    }
  }

  async function saveEdit(ev: FormEvent) {
    ev.preventDefault();
    if (!editing) return;
    const b: { display_name?: string; role_code?: RoleCode } = {};
    if (editing.name.trim() !== editing.who.display_name) b.display_name = editing.name.trim();
    if (editing.who.roles.length !== 1 || editing.who.roles[0] !== editing.role) b.role_code = editing.role;
    if (!Object.keys(b).length) return setEditing(null);
    if (await run(() => api.updateStaff(editing.who.id, b), `${editing.name.trim()} saved.${b.role_code ? " They were signed out so the new role applies." : ""}`))
      setEditing(null);
  }

  async function confirm() {
    if (!dialog) return;
    const d = dialog;
    setDialog(null);
    if (d.kind === "disable") await run(() => api.updateStaff(d.who.id, { active: false }), `${d.who.display_name} can no longer sign in.`);
    if (d.kind === "enable") await run(() => api.updateStaff(d.who.id, { active: true }), `${d.who.display_name} can sign in again.`);
    if (d.kind === "mfa") await run(() => api.resetStaffMfa(d.who.id), `${d.who.display_name} will set up two-step sign-in again at their next sign-in.`);
    if (d.kind === "password" && (await run(() => api.resetStaffPassword(d.who.id, d.password), `New temporary password set for ${d.who.display_name}.`)))
      setShown({ email: d.who.email, password: d.password });
  }

  const actionsFor = (u: StaffMember) =>
    u.id === me ? (
      <span className="text-sm text-ink-muted">Use My account</span>
    ) : (
      <span className="flex flex-wrap gap-1.5">
        <button
          type="button"
          className="btn-ghost px-3 py-1.5 text-xs"
          aria-label={`Edit ${u.display_name}`}
          onClick={() => setEditing({ who: u, name: u.display_name, role: (u.roles[0] as RoleCode) ?? "SALES_OPERATOR" })}
        >
          Edit
        </button>
        <button
          type="button"
          className="btn-ghost px-3 py-1.5 text-xs"
          aria-label={`New password for ${u.display_name}`}
          onClick={() => setDialog({ kind: "password", who: u, password: tempPassword() })}
        >
          New password
        </button>
        {u.mfa_enrolled && (
          <button type="button" className="btn-ghost px-3 py-1.5 text-xs" aria-label={`Reset two-step for ${u.display_name}`} onClick={() => setDialog({ kind: "mfa", who: u })}>
            Reset two-step
          </button>
        )}
        <button
          type="button"
          className="btn-ghost px-3 py-1.5 text-xs"
          aria-label={`${u.active ? "Disable" : "Enable"} ${u.display_name}`}
          onClick={() => setDialog({ kind: u.active ? "disable" : "enable", who: u })}
        >
          {u.active ? "Disable" : "Enable"}
        </button>
      </span>
    );

  const columns: Column<StaffMember>[] = [
    {
      key: "n",
      header: "Name",
      cell: (u) => (
        <span className="font-medium">
          {u.display_name}
          {u.id === me && <span className="font-normal text-ink-muted"> (you)</span>}
        </span>
      ),
      sort: (u) => u.display_name.toLowerCase(),
    },
    { key: "e", header: "Email", cell: (u) => u.email, sort: (u) => u.email },
    { key: "r", header: "Role", cell: (u) => u.roles.map(roleLabel).join(", ") || "—", sort: (u) => u.roles.join() },
    { key: "m", header: "Two-step", cell: (u) => (u.mfa_enrolled ? <StatusChip tone="success">On</StatusChip> : <StatusChip tone="neutral">Off</StatusChip>) },
    {
      key: "s",
      header: "Status",
      cell: (u) =>
        !u.active ? (
          <StatusChip tone="neutral">Disabled</StatusChip>
        ) : u.locked ? (
          <StatusChip tone="warning">Locked out</StatusChip>
        ) : (
          <StatusChip tone="success">Active</StatusChip>
        ),
    },
    {
      key: "l",
      header: "Last seen",
      cell: (u) => (u.last_seen_at ? <span className="whitespace-nowrap">{day(u.last_seen_at)}</span> : "Never"),
      sort: (u) => u.last_seen_at ?? "",
    },
    { key: "a", header: "Actions", cell: (u) => actionsFor(u) },
  ];

  return (
    <div className="space-y-5">
      <section className="card space-y-4 p-6" aria-labelledby="set-users">
        <CardTitle
          id="set-users"
          title="Users"
          action={
            <span className="flex flex-wrap items-center gap-2">
              <ViewToggle value={view} onChange={setView} />
              {!adding && (
                <button
                  type="button"
                  className="btn-accent"
                  onClick={() => {
                    setAdding(true);
                    setF((x) => ({ ...x, password: tempPassword() }));
                  }}
                >
                  Add user
                </button>
              )}
            </span>
          }
        />
        <p className="text-sm text-ink-muted">Each person has one role. Disabling someone, or changing their role or password, signs them out everywhere.</p>
        <Notice msg={msg} />
        {shown && (
          <div className="alert-warning space-y-1" data-testid="temp-password">
            <p>
              Give <strong>{shown.email}</strong> this temporary password in person. It is shown only once:
            </p>
            <p className="num select-all text-base font-semibold">{shown.password}</p>
            <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => setShown(null)}>
              Done
            </button>
          </div>
        )}
        {adding && (
          <form className="grid gap-4 rounded-ctl border border-line p-4 md:grid-cols-2" aria-label="Add user" onSubmit={add} noValidate>
            <Field id="nu-name" label="Name">
              <input id="nu-name" className="input" maxLength={200} value={f.name} onChange={(ev) => setF({ ...f, name: ev.target.value })} />
            </Field>
            <Field id="nu-email" label="Email (they sign in with it)">
              <input id="nu-email" type="email" className="input" maxLength={320} value={f.email} onChange={(ev) => setF({ ...f, email: ev.target.value })} />
            </Field>
            <Field id="nu-role" label="Role" hint={ROLES.find((r) => r.code === f.role)?.hint}>
              <select id="nu-role" className="input" value={f.role} onChange={(ev) => setF({ ...f, role: ev.target.value as RoleCode })}>
                {ROLES.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="nu-pw" label="Temporary password" hint="Generated for you. They can change it under Settings › My account.">
              <div className="flex gap-2">
                <input id="nu-pw" className="input num" value={f.password} onChange={(ev) => setF({ ...f, password: ev.target.value })} />
                <button type="button" className="btn-ghost shrink-0" onClick={() => setF({ ...f, password: tempPassword() })}>
                  New
                </button>
              </div>
            </Field>
            <div className="flex justify-end gap-2 md:col-span-2">
              <button type="button" className="btn-ghost" onClick={() => setAdding(false)}>
                Cancel
              </button>
              <button className="btn-primary" disabled={busy}>
                Add user
              </button>
            </div>
          </form>
        )}
        {editing && (
          <form className="grid gap-4 rounded-ctl border border-line p-4 md:grid-cols-2" aria-label={`Edit ${editing.who.display_name}`} onSubmit={saveEdit}>
            <Field id="eu-name" label="Name">
              <input id="eu-name" className="input" maxLength={200} value={editing.name} onChange={(ev) => setEditing({ ...editing, name: ev.target.value })} />
            </Field>
            <Field id="eu-role" label="Role" hint={ROLES.find((r) => r.code === editing.role)?.hint}>
              <select id="eu-role" className="input" value={editing.role} onChange={(ev) => setEditing({ ...editing, role: ev.target.value as RoleCode })}>
                {ROLES.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>
            <div className="flex justify-end gap-2 md:col-span-2">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button className="btn-primary" disabled={busy}>
                Save
              </button>
            </div>
          </form>
        )}
        {loadError ? (
          <ErrorState title="Couldn't load users" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={6} />
        ) : view === "grid" ? (
          <CardGrid
            items={rows}
            keyOf={(u) => u.id}
            caption={`Users (${rows.length})`}
            empty={<EmptyState title="No users" />}
            render={(u) => (
              <EntityCard
                title={
                  <>
                    {u.display_name}
                    {u.id === me && <span className="font-normal text-ink-muted"> (you)</span>}
                  </>
                }
                subtitle={u.email}
                media={<Avatar name={u.display_name} tone={u.active ? "accent" : "ink"} />}
                dim={!u.active}
                facts={[
                  { label: "Role", value: u.roles.map(roleLabel).join(", ") || "—" },
                  { label: "Last seen", value: u.last_seen_at ? day(u.last_seen_at) : "Never" },
                ]}
                chips={
                  <>
                    {!u.active ? (
                      <StatusChip tone="neutral">Disabled</StatusChip>
                    ) : u.locked ? (
                      <StatusChip tone="warning">Locked out</StatusChip>
                    ) : (
                      <StatusChip tone="success">Active</StatusChip>
                    )}
                    {u.mfa_enrolled ? <StatusChip tone="success">Two-step on</StatusChip> : <StatusChip tone="neutral">Two-step off</StatusChip>}
                  </>
                }
                actions={actionsFor(u)}
              />
            )}
          />
        ) : (
          <DataGrid rows={rows} columns={columns} rowKey={(u) => u.id} caption={`Users (${rows.length})`} empty={<EmptyState title="No users" />} />
        )}
      </section>
      <ConfirmDialog
        open={dialog !== null}
        title={
          dialog?.kind === "disable"
            ? `Disable ${dialog.who.display_name}?`
            : dialog?.kind === "enable"
              ? `Enable ${dialog.who.display_name}?`
              : dialog?.kind === "mfa"
                ? `Reset two-step sign-in for ${dialog.who.display_name}?`
                : dialog
                  ? `New password for ${dialog.who.display_name}?`
                  : ""
        }
        confirmLabel={dialog?.kind === "disable" ? "Disable" : dialog?.kind === "enable" ? "Enable" : dialog?.kind === "mfa" ? "Reset two-step" : "Set password"}
        danger={dialog?.kind === "disable"}
        onConfirm={() => void confirm()}
        onClose={() => setDialog(null)}
      >
        {dialog?.kind === "disable" && <p>They are signed out now and can't sign in until enabled again. Their past work stays.</p>}
        {dialog?.kind === "enable" && <p>They can sign in again with their current password.</p>}
        {dialog?.kind === "mfa" && <p>They are signed out and set up a new authenticator at their next sign-in.</p>}
        {dialog?.kind === "password" && <p>A new temporary password is set and they are signed out. You'll see it once after confirming.</p>}
      </ConfirmDialog>
    </div>
  );
}
