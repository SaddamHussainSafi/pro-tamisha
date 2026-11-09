import { useEffect, useId, useRef, type ReactNode } from "react";
import type { RoleMatrix, SecurityEvent, SessionInfo } from "../../api";
import { deviceLabel, eventLabel, groupPermissions, when } from "../../lib/staff";
import { Icon } from "../../ui/Icon";
import { EmptyState, StatusChip } from "../../ui/primitives";

export type Msg = { tone: "error" | "ok"; text: string } | null;

export function Notice({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
      {msg.text}
    </p>
  );
}

export function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
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

/** Modal form panel (native <dialog>: focus trap, Esc to close, top layer). */
export function Modal({ open, title, onClose, children, wide = false }: { open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby={titleId}
      className={`m-auto max-h-[calc(100dvh-2rem)] ${wide ? "w-[min(44rem,calc(100vw-2rem))]" : "w-[min(32rem,calc(100vw-2rem))]"} rounded-card border-0 bg-card p-0 text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] backdrop:bg-ink/40`}
    >
      {open && (
        <div className="space-y-5 p-6">
          <div className="flex items-start justify-between gap-3">
            <h2 id={titleId} className="text-lg font-semibold">
              {title}
            </h2>
            <button type="button" className="icon-btn size-9" aria-label="Close" onClick={onClose}>
              <Icon name="x" size={16} />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}

/** What a role allows, grouped by area (from the server's role matrix). */
export function PermissionSummary({ matrix, role }: { matrix: RoleMatrix | null; role: string }) {
  if (!matrix) return <p className="text-sm text-ink-muted">Loading what this role can do…</p>;
  const r = matrix.roles.find((x) => x.code === role);
  if (!r) return null;
  const perms = matrix.permissions.filter((p) => r.permissions.includes(p.code));
  const groups = groupPermissions(perms);
  return (
    <details className="rounded-ctl border border-line p-4 text-sm">
      <summary className="cursor-pointer font-medium">
        What {r.name} can do <span className="font-normal text-ink-muted">({perms.length} permissions)</span>
      </summary>
      <p className="mt-2 text-ink-muted">{r.description}</p>
      <dl className="mt-3 space-y-2">
        {groups.map((g) => (
          <div key={g.area}>
            <dt className="font-medium">{g.area}</dt>
            <dd className="text-ink-muted">{g.items.map((p) => p.description).join(" · ")}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/** Active sessions with a sign-out button for each (not the current one). */
export function SessionList({ rows, onRevoke, busy, showPerson = false }: { rows: SessionInfo[]; onRevoke: (s: SessionInfo) => void; busy?: boolean; showPerson?: boolean }) {
  if (!rows.length) return <EmptyState title="No active sessions" icon="clock" />;
  return (
    <ul className="divide-y divide-line" aria-label="Active sessions">
      {rows.map((s) => (
        <li key={s.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
          <span className="grid size-10 place-items-center rounded-full bg-subtle text-ink-muted" aria-hidden>
            <Icon name="user" size={18} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">
              {showPerson && s.employee_name ? `${s.employee_name} · ` : ""}
              {deviceLabel(s.user_agent)}
              {s.current && (
                <span className="ml-2 align-middle">
                  <StatusChip tone="success">This device</StatusChip>
                </span>
              )}
            </p>
            <p className="truncate text-sm text-ink-muted">
              {s.ip_address ?? "Unknown address"} · signed in {when(s.created_at)} · last active {when(s.last_seen_at)}
            </p>
          </div>
          {!s.current && (
            <button type="button" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy} onClick={() => onRevoke(s)} aria-label={`Sign out ${showPerson && s.employee_name ? s.employee_name + " on " : ""}${deviceLabel(s.user_agent)}, last active ${when(s.last_seen_at)}`}>
              Sign out
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Sign-in history / security events, newest first. */
export function EventList({ rows, showPerson = false, label }: { rows: SecurityEvent[]; showPerson?: boolean; label: string }) {
  if (!rows.length) return <EmptyState title="Nothing recorded yet" icon="shield" />;
  return (
    <ol className="divide-y divide-line" aria-label={label}>
      {rows.map((e) => (
        <li key={e.id} className="flex items-start gap-3 py-3">
          <span className={`mt-0.5 grid size-8 shrink-0 place-items-center rounded-full ${e.success ? "bg-success/10 text-success" : "bg-danger/10 text-danger"}`} aria-hidden>
            <Icon name={e.success ? "check" : "alert"} size={15} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">
              {showPerson && <span>{e.actor_name ?? "Unknown email"} · </span>}
              {eventLabel(e)}
              {e.target_name && e.target_name !== e.actor_name && <span className="font-normal text-ink-muted"> · {e.target_name}</span>}
            </p>
            <p className="truncate text-sm text-ink-muted">
              <time dateTime={e.created_at}>{when(e.created_at)}</time>
              {e.ip_address && ` · ${e.ip_address}`}
              {e.user_agent && ` · ${deviceLabel(e.user_agent)}`}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
