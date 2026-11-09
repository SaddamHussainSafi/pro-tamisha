import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, hasCompanyWide, type RoleCode, type RoleMatrix, type StaffMember, type StaffStatus } from "../../api";
import { useAuth } from "../../auth";
import { filterStaff, roleLabel, ROLES, STATUS_LABEL, staffMessage, tempPassword, when, type StaffFilter } from "../../lib/staff";
import { useViewMode } from "../../lib/viewMode";
import { CardGrid, EntityCard } from "../../ui/CardGrid";
import { DataGrid, type Column } from "../../ui/DataGrid";
import { Icon } from "../../ui/Icon";
import { Avatar, CardTitle, ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, StatusChip } from "../../ui/primitives";
import { ViewToggle } from "../../ui/ViewToggle";
import { Field, Modal, Notice, PermissionSummary, type Msg } from "./shared";

type Confirm =
  | { kind: "suspend" | "reactivate" | "mfa" | "signout" | "invite"; who: StaffMember }
  | { kind: "password"; who: StaffMember; password: string }
  | { kind: "save"; who: StaffMember; body: { display_name?: string; role_code?: RoleCode } }
  | null;

function StatusChips({ u }: { u: StaffMember }) {
  return (
    <span className="inline-flex flex-wrap gap-1.5">
      {u.status === "SUSPENDED" ? (
        <StatusChip tone="neutral">Suspended</StatusChip>
      ) : u.status === "INVITED" ? (
        <StatusChip tone="info">Invited</StatusChip>
      ) : (
        <StatusChip tone="success">Active</StatusChip>
      )}
      {u.locked && <StatusChip tone="warning">Locked out</StatusChip>}
    </span>
  );
}

export function Users() {
  const { session } = useAuth();
  const [rows, setRows] = useState<StaffMember[] | null>(null);
  const [matrix, setMatrix] = useState<RoleMatrix | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [filter, setFilter] = useState<StaffFilter>({ q: "", role: "ALL", status: "ALL" });
  const [adding, setAdding] = useState(false);
  const [managing, setManaging] = useState<StaffMember | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [shownPassword, setShownPassword] = useState<{ email: string; password: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useViewMode("users");
  const me = session?.employee.id;
  const canManage = !!session && hasCompanyWide(session, "employee.manage");

  const load = useCallback(async () => {
    try {
      const [staff, m] = await Promise.all([api.staff(), api.roleMatrix()]);
      setRows(staff);
      setMatrix(m);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);
  useEffect(() => void load(), [load]);

  const shown = useMemo(() => filterStaff(rows ?? [], filter), [rows, filter]);
  const counts = useMemo(() => {
    const c: Record<StaffStatus | "ALL", number> = { ALL: 0, ACTIVE: 0, INVITED: 0, SUSPENDED: 0 };
    for (const u of filterStaff(rows ?? [], { ...filter, status: "ALL" })) {
      c.ALL += 1;
      c[u.status] += 1;
    }
    return c;
  }, [rows, filter]);

  async function run(fn: () => Promise<unknown>, ok: string): Promise<boolean> {
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

  async function doConfirm() {
    const c = confirm;
    setConfirm(null);
    if (!c) return;
    const name = c.who.display_name;
    if (c.kind === "suspend") await run(() => api.updateStaff(c.who.id, { active: false }), `${name} is suspended and was signed out.`);
    if (c.kind === "reactivate") await run(() => api.updateStaff(c.who.id, { active: true }), `${name} can sign in again.`);
    if (c.kind === "mfa") await run(() => api.resetStaffMfa(c.who.id), `Two-step sign-in is off for ${name}. They can turn it on again in My account.`);
    if (c.kind === "signout") await run(() => api.revokeStaffSessions(c.who.id), `${name} was signed out everywhere.`);
    if (c.kind === "invite") await run(() => api.resendInvite(c.who.id), `A new invitation was emailed to ${c.who.email}.`);
    if (c.kind === "password" && (await run(() => api.resetStaffPassword(c.who.id, c.password), `Temporary password set for ${name}.`))) setShownPassword({ email: c.who.email, password: c.password });
    if (c.kind === "save" && (await run(() => api.updateStaff(c.who.id, c.body), `${c.body.display_name ?? name} saved.${c.body.role_code ? " They were signed out so the new role applies." : ""}`))) setManaging(null);
    if (c.kind !== "save") setManaging(null);
  }

  const manageButton = (u: StaffMember) =>
    u.id === me ? (
      <span className="text-sm text-ink-muted">Use My account</span>
    ) : canManage ? (
      <button type="button" className="btn-ghost px-3 py-1.5 text-xs" aria-label={`Manage ${u.display_name}`} onClick={() => setManaging(u)}>
        Manage
      </button>
    ) : null;

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
    { key: "e", header: "Email", cell: (u) => <span className="break-all">{u.email}</span>, sort: (u) => u.email.toLowerCase() },
    { key: "r", header: "Role", cell: (u) => u.roles.map(roleLabel).join(", ") || "—", sort: (u) => u.roles.map(roleLabel).join() },
    { key: "s", header: "Status", cell: (u) => <StatusChips u={u} />, sort: (u) => u.status },
    { key: "m", header: "Two-step", cell: (u) => (u.mfa_enrolled ? "On" : "Off") },
    { key: "l", header: "Last login", cell: (u) => <span className="whitespace-nowrap">{when(u.last_login_at)}</span>, sort: (u) => u.last_login_at ?? "" },
    { key: "a", header: "Actions", cell: manageButton },
  ];

  return (
    <div className="space-y-5">
      <section className="card space-y-4 p-6" aria-labelledby="set-users">
        <CardTitle
          id="set-users"
          title="User management"
          action={
            <span className="flex flex-wrap items-center gap-2">
              <ViewToggle value={view} onChange={setView} />
              {canManage && (
                <button type="button" className="btn-primary" onClick={() => setAdding(true)}>
                  <Icon name="plus" size={16} />
                  Add user
                </button>
              )}
            </span>
          }
        />
        <p className="text-sm text-ink-muted">New people get an email invitation and choose their own password. Suspending someone, or changing their role or password, signs them out everywhere.</p>
        <Notice msg={msg} />
        {shownPassword && (
          <div className="alert-warning space-y-1" data-testid="temp-password">
            <p>
              Give <strong>{shownPassword.email}</strong> this temporary password in person. It is shown only once; they should change it in My account:
            </p>
            <p className="num select-all text-base font-semibold">{shownPassword.password}</p>
            <button type="button" className="btn-ghost px-3 py-1.5 text-xs" onClick={() => setShownPassword(null)}>
              Done
            </button>
          </div>
        )}
        <FilterBar
          search={filter.q}
          onSearch={(q) => setFilter({ ...filter, q })}
          placeholder="Search name or email"
          value={filter.status}
          onChange={(status) => setFilter({ ...filter, status })}
          options={[
            { value: "ALL", label: "All", count: counts.ALL },
            { value: "ACTIVE", label: STATUS_LABEL.ACTIVE, count: counts.ACTIVE },
            { value: "INVITED", label: STATUS_LABEL.INVITED, count: counts.INVITED },
            { value: "SUSPENDED", label: STATUS_LABEL.SUSPENDED, count: counts.SUSPENDED },
          ]}
        >
          <label className="min-w-0 flex-1 sm:flex-none">
            <span className="sr-only">Role</span>
            <select className="input w-full rounded-full py-2 sm:w-44" value={filter.role} onChange={(e) => setFilter({ ...filter, role: e.target.value as StaffFilter["role"] })}>
              <option value="ALL">All roles</option>
              {ROLES.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
        </FilterBar>
        {loadError ? (
          <ErrorState title="Couldn't load users" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={6} />
        ) : view === "grid" ? (
          <CardGrid
            items={shown}
            keyOf={(u) => u.id}
            caption={`Users (${shown.length})`}
            empty={<EmptyState title="No users match" hint="Change the search or filters." icon="users" />}
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
                  { label: "Last login", value: when(u.last_login_at) },
                  { label: "Two-step", value: u.mfa_enrolled ? "On" : "Off" },
                ]}
                chips={<StatusChips u={u} />}
                actions={manageButton(u)}
              />
            )}
          />
        ) : (
          <DataGrid rows={shown} columns={columns} rowKey={(u) => u.id} caption={`Users (${shown.length})`} empty={<EmptyState title="No users match" hint="Change the search or filters." icon="users" />} />
        )}
      </section>

      <AddUser
        open={adding}
        matrix={matrix}
        busy={busy}
        onClose={() => setAdding(false)}
        onSubmit={async (b) => {
          if (await run(() => api.createStaff(b), `Invitation sent to ${b.email}. They'll show as Invited until they set a password.`)) setAdding(false);
        }}
      />
      {managing && <Manage who={managing} matrix={matrix} busy={busy} onClose={() => setManaging(null)} onConfirm={setConfirm} />}

      <ConfirmDialog
        open={confirm !== null}
        title={confirmTitle(confirm)}
        confirmLabel={confirmLabel(confirm)}
        danger={confirm?.kind === "suspend" || confirm?.kind === "signout"}
        onConfirm={() => void doConfirm()}
        onClose={() => setConfirm(null)}
      >
        {confirm && <p>{confirmBody(confirm)}</p>}
      </ConfirmDialog>
    </div>
  );
}

function confirmTitle(c: Confirm): string {
  if (!c) return "";
  const n = c.who.display_name;
  switch (c.kind) {
    case "suspend":
      return `Suspend ${n}?`;
    case "reactivate":
      return `Reactivate ${n}?`;
    case "mfa":
      return `Turn off two-step sign-in for ${n}?`;
    case "signout":
      return `Sign ${n} out everywhere?`;
    case "invite":
      return `Send ${n} a new invitation?`;
    case "password":
      return `Set a temporary password for ${n}?`;
    case "save":
      return c.body.role_code ? `Change ${n}'s role to ${roleLabel(c.body.role_code)}?` : `Save changes to ${n}?`;
  }
}

function confirmLabel(c: Confirm): string {
  if (!c) return "Confirm";
  return { suspend: "Suspend", reactivate: "Reactivate", mfa: "Turn off two-step", signout: "Sign out everywhere", invite: "Send invitation", password: "Set password", save: "Save" }[c.kind];
}

function confirmBody(c: NonNullable<Confirm>): string {
  switch (c.kind) {
    case "suspend":
      return "They are signed out now and can't sign in until reactivated. Open invitation and reset links stop working. Their past work stays.";
    case "reactivate":
      return "They can sign in again with their current password.";
    case "mfa":
      return "Use this when they lost their phone and their recovery codes. They are signed out and can sign in with their password alone until they turn it on again.";
    case "signout":
      return "Every browser and device they use has to sign in again.";
    case "invite":
      return "Earlier invitation links stop working.";
    case "password":
      return "Use this only if email doesn't reach them. They are signed out, and you'll see the password once to hand over in person.";
    case "save":
      return c.body.role_code ? "Their access changes right away and they are signed out so the new role applies." : "Their name is updated.";
  }
}

function AddUser({
  open,
  matrix,
  busy,
  onClose,
  onSubmit,
}: {
  open: boolean;
  matrix: RoleMatrix | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (b: { email: string; display_name: string; role_code: RoleCode }) => Promise<void>;
}) {
  const [f, setF] = useState({ name: "", email: "", role: "SALES_OPERATOR" as RoleCode });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setF({ name: "", email: "", role: "SALES_OPERATOR" });
      setError(null);
    }
  }, [open]);

  function submit(ev: FormEvent) {
    ev.preventDefault();
    const problem = !f.name.trim() ? "Enter the person's name." : !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email.trim()) ? "Enter a valid email address." : null;
    setError(problem);
    if (!problem) void onSubmit({ email: f.email.trim(), display_name: f.name.trim(), role_code: f.role });
  }

  return (
    <Modal open={open} title="Add user" onClose={onClose}>
      <form className="space-y-4" aria-label="Add user" onSubmit={submit} noValidate>
        {error && (
          <p role="alert" className="alert-danger">
            {error}
          </p>
        )}
        <Field id="nu-name" label="Name">
          <input id="nu-name" className="input" maxLength={200} autoComplete="off" value={f.name} onChange={(ev) => setF({ ...f, name: ev.target.value })} />
        </Field>
        <Field id="nu-email" label="Email (they sign in with it)">
          <input id="nu-email" type="email" className="input" maxLength={320} autoComplete="off" value={f.email} onChange={(ev) => setF({ ...f, email: ev.target.value })} />
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
        <PermissionSummary matrix={matrix} role={f.role} />
        <p className="flex gap-2 text-sm text-ink-muted">
          <Icon name="info" size={18} />
          We email them an invitation link to choose their own password. Nobody else sees it.
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={busy}>
            {busy ? "Sending…" : "Send invitation"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function Manage({ who, matrix, busy, onClose, onConfirm }: { who: StaffMember; matrix: RoleMatrix | null; busy: boolean; onClose: () => void; onConfirm: (c: Confirm) => void }) {
  const [name, setName] = useState(who.display_name);
  const [role, setRole] = useState<RoleCode>((who.roles[0] as RoleCode) ?? "VIEWER");
  const [error, setError] = useState<string | null>(null);

  function save(ev: FormEvent) {
    ev.preventDefault();
    if (!name.trim()) return setError("Enter a name.");
    const body: { display_name?: string; role_code?: RoleCode } = {};
    if (name.trim() !== who.display_name) body.display_name = name.trim();
    if (who.roles.length !== 1 || who.roles[0] !== role) body.role_code = role;
    if (!Object.keys(body).length) return onClose();
    onConfirm({ kind: "save", who, body });
  }

  const action = (label: string, c: Confirm, hint: string, danger = false) => (
    <li className="flex flex-wrap items-center justify-between gap-2 py-3">
      <span className="min-w-0 flex-1 text-sm text-ink-muted">{hint}</span>
      <button type="button" className={danger ? "btn-ghost px-3 py-1.5 text-xs text-danger" : "btn-ghost px-3 py-1.5 text-xs"} disabled={busy} onClick={() => onConfirm(c)}>
        {label}
      </button>
    </li>
  );

  return (
    <Modal open title={`Manage ${who.display_name}`} onClose={onClose} wide>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <Avatar name={who.display_name} tone={who.active ? "accent" : "ink"} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{who.email}</p>
          <p className="text-ink-muted">
            Last login {when(who.last_login_at)}
            {who.status === "INVITED" && who.invite_expires_at && ` · invitation expires ${when(who.invite_expires_at)}`}
          </p>
        </div>
        <StatusChips u={who} />
      </div>
      <form className="grid gap-4 md:grid-cols-2" aria-label={`Edit ${who.display_name}`} onSubmit={save} noValidate>
        {error && (
          <p role="alert" className="alert-danger md:col-span-2">
            {error}
          </p>
        )}
        <Field id="eu-name" label="Name">
          <input id="eu-name" className="input" maxLength={200} value={name} onChange={(ev) => setName(ev.target.value)} />
        </Field>
        <Field id="eu-role" label="Role" hint={ROLES.find((r) => r.code === role)?.hint}>
          <select id="eu-role" className="input" value={role} onChange={(ev) => setRole(ev.target.value as RoleCode)}>
            {ROLES.map((r) => (
              <option key={r.code} value={r.code}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="md:col-span-2">
          <PermissionSummary matrix={matrix} role={role} />
        </div>
        <div className="flex justify-end gap-2 md:col-span-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={busy}>
            Save
          </button>
        </div>
      </form>
      <section aria-labelledby="mu-actions" className="border-t border-line pt-4">
        <h3 id="mu-actions" className="text-sm font-semibold">
          Account
        </h3>
        <ul className="divide-y divide-line">
          {who.status === "INVITED" && action("Resend invitation", { kind: "invite", who }, "Email a new invitation link. Earlier links stop working.")}
          {who.active && action("Sign out everywhere", { kind: "signout", who }, "End every session they have open.")}
          {who.mfa_enrolled && action("Turn off two-step", { kind: "mfa", who }, "They lost their phone and recovery codes.")}
          {action("Set temporary password", { kind: "password", who, password: tempPassword() }, "Only if email doesn't reach them.")}
          {who.active
            ? action("Suspend", { kind: "suspend", who }, "Block sign-in. Their past work stays.", true)
            : action("Reactivate", { kind: "reactivate", who }, "Allow sign-in again.")}
        </ul>
      </section>
    </Modal>
  );
}
