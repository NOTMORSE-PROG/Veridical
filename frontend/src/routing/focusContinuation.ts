/** One explicit action may focus its continuation until newer instructor intent. */
export function claimFocusContinuation(owner: Element | null = document.activeElement) {
  let allowed = document.hasFocus();
  let disposed = false;
  const retire = () => { allowed = false; };
  const onKey = (event: KeyboardEvent) => {
    if (["Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) retire();
  };
  const onFocus = (event: FocusEvent) => { if (event.target !== owner) retire(); };
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("pointerdown", retire, true);
  document.addEventListener("focusin", onFocus, true);
  window.addEventListener("blur", retire);
  window.addEventListener("wheel", retire, { passive: true });
  function dispose() {
    if (disposed) return;
    disposed = true;
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("pointerdown", retire, true);
    document.removeEventListener("focusin", onFocus, true);
    window.removeEventListener("blur", retire);
    window.removeEventListener("wheel", retire);
  }
  return {
    dispose,
    focus(target: HTMLElement | null) {
      const owns = !disposed && allowed && document.hasFocus()
        && (document.activeElement === owner || (owner?.isConnected === false && document.activeElement === document.body));
      dispose();
      if (owns && target?.isConnected) target.focus();
      return Boolean(owns && target && document.activeElement === target);
    },
  };
}

export type FocusContinuation = ReturnType<typeof claimFocusContinuation>;
