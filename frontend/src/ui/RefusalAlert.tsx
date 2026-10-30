import { useEffect, useRef } from "react";
import { Alert } from "./Alert";

// A refusal of the request rather than of one field. The submit button was
// busy, so focus fell to the page and the message can sit below the fold of
// a dialog; focus it and bring it into view. When a field was refused in the
// same answer, the field takes focus instead (its message is read as its
// description) and this alert is still announced.
export function RefusalAlert({ title, message, takeFocus = true }: { title: string; message: string; takeFocus?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (takeFocus) ref.current?.focus();
    ref.current?.scrollIntoView({ block: "nearest" });
  }, [message, takeFocus]);
  return <Alert ref={ref} tabIndex={-1} title={title} tone="error" role="alert">{message}</Alert>;
}
