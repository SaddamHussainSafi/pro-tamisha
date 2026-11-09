// Tamisha Trading Inc. logo supplied by the owner (2026-11-09); files in public/brand/.
// Collapsed sidebar shows the square emblem only. Alt text names the company for screen readers.
export function Wordmark({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  return compact ? (
    <img src="/brand/tamisha-trading-mark.webp" alt="Tamisha Trading Inc." width={48} height={48} className={`size-12 select-none ${className}`} draggable={false} />
  ) : (
    <img src="/brand/tamisha-trading-logo.webp" alt="Tamisha Trading Inc." width={800} height={222} className={`h-12 w-auto max-w-full select-none ${className}`} draggable={false} />
  );
}
