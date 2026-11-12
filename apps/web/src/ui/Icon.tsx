// Small inline stroke icon set (no external icon font). Decorative unless `label` is given.
const PATHS = {
  overview: "M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z",
  orders: "M9 5h10M9 12h10M9 19h10M4 5h.01M4 12h.01M4 19h.01",
  picking: "M4 7l8-4 8 4-8 4zM4 7v10l8 4 8-4V7M12 11v10",
  invoices: "M6 2h9l5 5v15H6zM14 2v6h6M9 13h6M9 17h6",
  products: "M12 3c4 0 7 3 7 7 0 5-7 11-7 11S5 15 5 10c0-4 3-7 7-7z",
  pricing: "M20 12l-8 8-9-9V3h8zM7.5 7.5h.01",
  inventory: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10",
  receiving: "M3 7h11v10H3zM14 10h4l3 3v4h-7M7 20a2 2 0 100-4 2 2 0 000 4zM17 20a2 2 0 100-4 2 2 0 000 4z",
  vendors: "M3 21V8l9-5 9 5v13M9 21v-6h6v6",
  customers: "M16 21v-2a4 4 0 00-8 0v2M12 11a4 4 0 100-8 4 4 0 000 8z",
  scan: "M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M4 12h16",
  documents: "M3 6h6l2 2h10v12H3z",
  payments: "M3 6h18v12H3zM3 10h18M7 15h3",
  reports: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-1-3-2 .3-1.5-1.5L17 4l-3-1-1 2h-2L10 3 7 4l.5 2.3L6 7.7 4 7.5 3 10.5l2 1.5v0l-2 1.5 1 3 2-.3 1.5 1.5L7 20l3 1 1-2h2l1 2 3-1-.5-2.3 1.5-1.5 2 .3 1-3z",
  search: "M11 19a8 8 0 100-16 8 8 0 000 16zM21 21l-4.3-4.3",
  bell: "M6 8a6 6 0 0112 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4",
  menu: "M3 6h18M3 12h18M3 18h18",
  check: "M5 12l5 5L20 7",
  alert: "M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  x: "M18 6L6 18M6 6l12 12",
  info: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 16v-4M12 8h.01",
  clock: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2",
  chevron: "M9 18l6-6-6-6",
  sort: "M7 15l5 5 5-5M7 9l5-5 5 5",
  plus: "M12 5v14M5 12h14",
  file: "M6 2h9l5 5v15H6zM14 2v6h6",
  logout: "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",
  calendar: "M4 5h16v16H4zM4 10h16M8 3v4M16 3v4",
  arrowUpRight: "M7 17L17 7M8 7h9v9",
  trendUp: "M3 17l6-6 4 4 8-8M15 7h6v6",
  trendDown: "M3 7l6 6 4-4 8 8M15 17h6v-6",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 100-6 3 3 0 000 6z",
  home: "M3 11l9-8 9 8M5 9.5V21h5v-6h4v6h5V9.5",
  cart: "M3 4h2l2.4 11.2a1 1 0 001 .8h9.2a1 1 0 001-.8L20 8H6.2M9 20.5a1 1 0 100-2 1 1 0 000 2zM17 20.5a1 1 0 100-2 1 1 0 000 2z",
  boxes: "M7 12l-4 2v4l4 2 4-2v-4zM17 12l-4 2v4l4 2 4-2v-4zM12 3L8 5v4l4 2 4-2V5z",
  users: "M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8",
  store: "M3 9l1.5-5h15L21 9M3 9v1a3 3 0 006 0 3 3 0 006 0 3 3 0 006 0V9M3 9h18M5 13v8h14v-8M10 21v-5h4v5",
  chevronDown: "M6 9l6 6 6-6",
  chevronLeft: "M15 18l-6-6 6-6",
  printer: "M6 9V3h12v6M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2M6 14h12v7H6z",
  upload: "M12 16V4M7 9l5-5 5 5M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2",
  download: "M12 4v12M7 11l5 5 5-5M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2",
  refresh: "M21 12a9 9 0 01-15.5 6.2M3 12a9 9 0 0115.5-6.2M21 4v5h-5M3 20v-5h5",
  pause: "M8 5v14M16 5v14",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
  user: "M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8z",
  hash: "M4 9h16M4 15h16M10 3L8 21M16 3l-2 18",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4",
  more: "M4.5 12a1.5 1.5 0 103 0 1.5 1.5 0 00-3 0zM10.5 12a1.5 1.5 0 103 0 1.5 1.5 0 00-3 0zM16.5 12a1.5 1.5 0 103 0 1.5 1.5 0 00-3 0z",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2M5.5 5.1L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.5-6.9A2 2 0 0016.8 4H7.2a2 2 0 00-1.7 1.1z",
  archive: "M3 4h18v4H3zM5 8v11a1 1 0 001 1h12a1 1 0 001-1V8M10 12h4",
  mail: "M3 5h18v14H3zM3 6l9 7 9-7",
  moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
  monitor: "M3 4h18v12H3zM8 20h8M12 16v4",
  volume: "M11 5L6 9H3v6h3l5 4zM15.5 8.5a5 5 0 010 7M18.5 5.5a9 9 0 010 13",
  checkAll: "M2 13l4 4 9-10M9 15l2 2 11-12",
  globe: "M12 22a10 10 0 100-20 10 10 0 000 20zM2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z",
  coins: "M12 7c4.4 0 8-1.3 8-3s-3.6-3-8-3-8 1.3-8 3 3.6 3 8 3zM4 4v5c0 1.7 3.6 3 8 3s8-1.3 8-3V4M4 9v5c0 1.7 3.6 3 8 3s8-1.3 8-3V9",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6",
  reply: "M9 14L4 9l5-5M4 9h10a6 6 0 016 6v5",
  restore: "M3 12a9 9 0 109-9 9.7 9.7 0 00-6.7 2.7L3 8M3 3v5h5",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, label, className = "" }: { name: IconName; size?: number; label?: string; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
