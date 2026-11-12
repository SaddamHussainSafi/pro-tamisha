import { useEffect, useState } from "react";
import { has } from "../../api";
import { useAuth } from "../../auth";
import { mail } from "../../lib/mail";

/** Unread emails in the first mailbox given to me, on the sidebar's Email entry. The Email
 * screen fires "pt:mail-unread" after reading something so the number follows at once. */
export function MailBadge({ collapsed }: { collapsed: boolean }) {
  const { session } = useAuth();
  const allowed = !!session && has(session, "mail.use");
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!allowed) return;
    let live = true;
    const load = () => mail.accounts().then((a) => live && setN(a[0]?.unread ?? 0), () => undefined);
    void load();
    const t = setInterval(load, 120_000);
    window.addEventListener("pt:mail-unread", load);
    return () => {
      live = false;
      clearInterval(t);
      window.removeEventListener("pt:mail-unread", load);
    };
  }, [allowed]);
  if (!allowed || n === 0) return null;
  const text = n > 99 ? "99+" : String(n);
  const cls = collapsed
    ? "num absolute right-1 top-1 grid min-w-5 place-items-center rounded-full bg-brand-primary px-1 text-[11px] font-semibold leading-5 text-white ring-2 ring-card"
    : "num ml-auto grid min-w-6 place-items-center rounded-full bg-brand-primary px-1.5 text-xs font-semibold leading-5 text-white";
  return (
    <>
      <span className={cls} aria-hidden data-testid="mail-badge">
        {text}
      </span>
      <span className="sr-only">, {n} unread</span>
    </>
  );
}
