import type { SVGProps } from "react";
import { cx } from "../components/cx";

interface SignalMarkProps extends SVGProps<SVGSVGElement> {
  inverse?: boolean;
}

/**
 * VERIDICAL's own line-and-junction mark. It translates T.I.P.'s visual
 * principles without presenting the school's seal as the product identity.
 */
export function SignalMark({ inverse = false, className, ...props }: SignalMarkProps) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 40 40"
      className={cx("signal-mark", inverse && "signal-mark--inverse", className)}
      {...props}
    >
      <path d="M8 7v26M16 7v26M24 7v26" />
      <path className="signal-mark__branch" d="M24 20h9" />
      <circle className="signal-mark__junction" cx="24" cy="20" r="4" />
    </svg>
  );
}

export function SignalWordmark({ inverse = false }: { inverse?: boolean }) {
  return (
    <span className={cx("signal-wordmark", inverse && "signal-wordmark--inverse")}>VERIDICAL</span>
  );
}
