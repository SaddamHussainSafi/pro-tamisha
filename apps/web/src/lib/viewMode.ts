import { useState } from "react";

// Grid / List choice per screen, remembered on this device only. Storage can be blocked
// (private windows, cleared data), so every access is guarded and grid is the fallback.

export type ViewMode = "grid" | "list";

type Store = Pick<Storage, "getItem" | "setItem">;

const key = (page: string) => `pt.view.${page}`;

function store(): Store | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readView(page: string, fallback: ViewMode = "grid", s: Store | null = store()): ViewMode {
  try {
    const v = s?.getItem(key(page));
    return v === "grid" || v === "list" ? v : fallback;
  } catch {
    return fallback;
  }
}

export function writeView(page: string, v: ViewMode, s: Store | null = store()): void {
  try {
    s?.setItem(key(page), v);
  } catch {
    /* not remembered; the screen still switches */
  }
}

/** Grid / List choice for one screen, remembered on this device. */
export function useViewMode(page: string, fallback: ViewMode = "grid"): [ViewMode, (v: ViewMode) => void] {
  const [mode, setMode] = useState<ViewMode>(() => readView(page, fallback));
  return [
    mode,
    (v) => {
      setMode(v);
      writeView(page, v);
    },
  ];
}
