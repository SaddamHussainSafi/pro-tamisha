import { useId, useState } from "react";
import { stack, type DayGroup } from "../../lib/notifications";
import type { AppNotification } from "../../lib/notificationsApi";
import { NotificationCard, type CardActions } from "./NotificationCard";

/** One day (Today / Yesterday / Earlier) of notifications, with related ones stacked. */
export function NotificationGroup({
  group,
  grouped,
  actions,
  onClearStack,
  now,
}: {
  group: DayGroup<AppNotification>;
  grouped: boolean;
  actions: CardActions;
  onClearStack?: (items: AppNotification[]) => void;
  now?: Date;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="space-y-2">
      <h3 id={id} className="px-1 text-sm font-semibold text-ink-muted">
        {group.label}
      </h3>
      <ul className="space-y-2">
        {stack(group.items, grouped).map((e) => (
          <li key={e.key} className="animate-enter">
            {e.type === "single" ? (
              <NotificationCard n={e.item} actions={actions} now={now} />
            ) : (
              <Stack items={e.items} source={e.source} actions={actions} onClear={onClearStack} now={now} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Stack({ items, source, actions, onClear, now }: { items: AppNotification[]; source: string; actions: CardActions; onClear?: (items: AppNotification[]) => void; now?: Date }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const unread = items.filter((n) => !n.read).length;
  const rest = items.length - 1;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 px-1">
        <span className="text-xs font-medium text-ink-muted">
          {source} · {items.length}
          {unread ? ` (${unread} unread)` : ""}
        </span>
        <span className="ml-auto flex gap-1">
          {onClear && (
            <button type="button" className="cursor-pointer rounded-full px-2.5 py-1 text-xs font-medium text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink" onClick={() => onClear(items)}>
              Clear all
            </button>
          )}
          <button
            type="button"
            className="cursor-pointer rounded-full px-2.5 py-1 text-xs font-medium text-brand-primary-hover transition-colors duration-150 hover:bg-subtle"
            aria-expanded={open}
            aria-controls={listId}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Show less" : `Show ${rest} more`}
          </button>
        </span>
      </div>
      <div id={listId}>
        {open ? (
          <ul className="space-y-2" aria-label={`${source} notifications`}>
            {items.map((n) => (
              <li key={n.id} className="animate-pop">
                <NotificationCard n={n} actions={actions} now={now} />
              </li>
            ))}
          </ul>
        ) : (
          // Collapsed: the newest card on top, two slim edges below hint at the rest.
          <div>
            {items[0] && <NotificationCard n={items[0]} actions={actions} now={now} className="z-[1]" />}
            <div aria-hidden className="relative mx-3 h-2 rounded-b-ctl border border-t-0 border-line bg-card" />
            {rest > 1 && <div aria-hidden className="relative mx-6 h-1.5 rounded-b-ctl border border-t-0 border-line bg-subtle" />}
          </div>
        )}
      </div>
    </div>
  );
}
