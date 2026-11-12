import { useEffect, useState } from "react";

/** Says so when the computer goes offline, and when it's back. Pages keep what was typed. */
export function ConnectionBanner() {
  const [online, setOnline] = useState(() => navigator.onLine !== false);
  const [back, setBack] = useState(false);
  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    const up = () => {
      setOnline(true);
      setBack(true);
      clearTimeout(t);
      t = setTimeout(() => setBack(false), 4000);
    };
    const down = () => {
      setOnline(false);
      setBack(false);
    };
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      clearTimeout(t);
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  if (!online)
    return (
      <p role="status" data-testid="offline-banner" className="alert-warning mb-4">
        <strong>You're offline.</strong> Pages can't load and changes can't be saved until the connection is back. What you've typed on this page stays here.
      </p>
    );
  if (back)
    return (
      <p role="status" className="alert-success mb-4">
        Back online. You can carry on — if something didn't save while offline, try it again.
      </p>
    );
  return null;
}
