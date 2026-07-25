// Segmented control — DESIGN.md §2 (wireframe .pseg)
import { cx } from "./cx";

interface SegmentedToggleProps<T extends string> {
  value: T;
  options: readonly T[];
  labels: Record<T, string>;
  onChange: (value: T) => void;
  disabled?: boolean;
}

export function SegmentedToggle<T extends string>({
  value,
  options,
  labels,
  onChange,
  disabled,
}: SegmentedToggleProps<T>) {
  return (
    <span className="inline-flex overflow-hidden rounded-control border border-border-input">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          disabled={disabled}
          aria-pressed={option === value}
          onClick={() => onChange(option)}
          className={cx(
            "px-2 py-1 text-xs whitespace-nowrap disabled:opacity-60",
            option === value ? "bg-primary text-on-primary" : "bg-panel text-ink-soft",
          )}
        >
          {labels[option]}
        </button>
      ))}
    </span>
  );
}
