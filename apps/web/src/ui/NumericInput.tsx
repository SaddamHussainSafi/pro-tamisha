import { useId } from "react";
import { isDecimal } from "../lib/decimal";
import { Icon } from "./Icon";

/** Text input that accepts only plain decimals (no exponent, no floats), with explicit unit label. */
export function NumericInput({
  label,
  value,
  onChange,
  unit,
  places = 2,
  hideLabel,
  invalidHint,
  confidence,
  className = "",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  unit?: string;
  places?: number;
  hideLabel?: boolean;
  invalidHint?: string;
  /** OCR confidence 0–100; < 80 is flagged for review. */
  confidence?: number;
  className?: string;
}) {
  const id = useId();
  const dp = value.split(".")[1]?.length ?? 0;
  const invalid = value !== "" && (!isDecimal(value) || dp > places);
  const low = confidence !== undefined && confidence < 80;
  const hintId = `${id}-hint`;
  return (
    <div className={className}>
      <label htmlFor={id} className={hideLabel ? "sr-only" : "label"}>{label}</label>
      <div
        className={`flex items-center rounded-[var(--radius-ctl)] bg-card focus-within:border-brand-primary ${
          invalid ? "border-2 border-danger" : low ? "border-2 border-warning" : "border border-line"
        }`}
      >
        <input
          id={id}
          inputMode="decimal"
          autoComplete="off"
          className="num w-full min-w-0 rounded-[var(--radius-ctl)] bg-transparent px-3.5 py-2.5 text-right text-base font-medium focus:outline-none"
          value={value}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid || low ? hintId : undefined}
          onChange={(e) => onChange(e.target.value.replace(/[^\d.]/g, ""))}
        />
        {unit && <span className="pr-3.5 text-sm font-medium text-ink-muted">{unit}</span>}
      </div>
      {invalid && <p id={hintId} className="mt-1 text-xs text-danger">{invalidHint ?? `Up to ${places} decimal places`}</p>}
      {!invalid && low && (
        <p id={hintId} className="mt-2 flex items-start gap-1.5 rounded-ctl bg-warning/12 px-2.5 py-1.5 text-xs font-medium text-ink [&_svg]:text-warning">
          <Icon name="alert" size={14} className="mt-px" />
          <span>OCR {confidence}% — check against original</span>
        </p>
      )}
    </div>
  );
}
