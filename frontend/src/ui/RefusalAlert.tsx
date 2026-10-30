import { useEffect, useRef } from "react";
import { Alert } from "./Alert";

// A refusal of the request rather than of one field. The submit button was
// busy, so focus fell to the page and the message can sit below the fold of
// a dialog; focus it and bring it into view.
export function RefusalAlert({ title, message }: { title: string; message: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.scrollIntoView({ block: "nearest" });
  }, [message]);
  return <Alert ref={ref} tabIndex={-1} title={title} tone="error" role="alert">{message}</Alert>;
}
