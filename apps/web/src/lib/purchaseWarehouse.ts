import { useEffect, useState } from "react";
import { api, canInWarehouse, type Session } from "../api";

/** The first warehouse this person may enter purchases in (no warehouse chooser, D-47);
 * "" while loading or when they may not. */
export function usePurchaseWarehouse(session: Session | null): string {
  const [wh, setWh] = useState("");
  useEffect(() => {
    if (!session) return;
    api.warehouses().then((w) => setWh(w.find((x) => x.active && canInWarehouse(session, "grn.record", x.id))?.id ?? ""), () => setWh(""));
  }, [session]);
  return wh;
}
