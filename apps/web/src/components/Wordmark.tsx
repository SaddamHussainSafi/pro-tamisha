// Text-only wordmark. Official Tamisha logo files are pending verification/permission
// (design/BRAND_VERIFICATION.md); no substitute artwork is generated.
export function Wordmark({ inverse = false, compact = false }: { inverse?: boolean; compact?: boolean }) {
  return (
    <div className="flex items-center gap-2 select-none">
      <span aria-hidden className="grid size-11 place-items-center rounded-full bg-brand-primary text-sm font-bold tracking-tight text-white">
        PT
      </span>
      {!compact && (
        <span className={`text-[19px] font-semibold tracking-tight ${inverse ? "text-white" : "text-ink"}`}>
          Pro Trading
        </span>
      )}
    </div>
  );
}
