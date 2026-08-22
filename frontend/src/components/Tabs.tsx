// First tabs pattern in the app (V-065, `ui-designer` spec 2026-08-22,
// mobile Document/Analysis split) -- built as a small reusable primitive
// since a future screen will likely want this too (no entry existed in
// DESIGN.md's component inventory before this).
//
// WAI-ARIA APG Tabs pattern, MANUAL activation (w3.org/WAI/ARIA/apg/
// patterns/tabs/): switching tabs here swaps a rendered document pane for
// a full flags list, a substantial content change, which the APG names as
// exactly the case manual activation is for -- Left/Right move focus
// between tabs without activating; Enter/Space activates the focused tab.
import { useRef } from "react";
import { cx } from "./cx";

export interface TabDef {
  id: string;
  label: string;
  badge?: string;
}

export function Tabs({
  tabs,
  active,
  onChange,
  label,
  className,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
  label: string;
  className?: string;
}) {
  const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  function focusTab(id: string) {
    tabRefs.current.get(id)?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent, index: number) {
    if (e.key === "ArrowRight") {
      e.preventDefault();
      focusTab(tabs[(index + 1) % tabs.length].id);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      focusTab(tabs[(index - 1 + tabs.length) % tabs.length].id);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusTab(tabs[0].id);
    } else if (e.key === "End") {
      e.preventDefault();
      focusTab(tabs[tabs.length - 1].id);
    }
  }

  return (
    <div role="tablist" aria-label={label} className={cx("flex border-b border-border", className)}>
      {tabs.map((tab, index) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              if (el) tabRefs.current.set(tab.id, el);
              else tabRefs.current.delete(tab.id);
            }}
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`tabpanel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onKeyDown={(e) => onKeyDown(e, index)}
            onClick={() => onChange(tab.id)}
            className={cx(
              "min-h-11 flex-1 border-b-[3px] px-3 text-sm",
              selected
                ? "border-action font-semibold text-ink"
                : "border-transparent text-ink-secondary hover:text-ink",
            )}
          >
            {tab.label}
            {tab.badge && <span className="ml-1.5 text-ink-tertiary">· {tab.badge}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  id,
  label,
  active,
  className,
  children,
}: {
  id: string;
  label: string;
  active: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  // `aria-label`, not `aria-labelledby`: the controlling tab button can be
  // `display:none` at wider breakpoints (the tablist itself goes
  // `lg:hidden`), and `aria-labelledby` pointing at a hidden element
  // resolves to an empty accessible name -- a static label sidesteps that
  // entirely and is an APG-permitted alternative. `role="tabpanel"` is
  // deliberately omitted: above the mobile breakpoint both panels are
  // visible at once with no visible tablist, and asserting "one of an
  // exclusive tab set" stops being true at that width.
  return (
    <div id={`tabpanel-${id}`} aria-label={label} className={cx(active ? "" : "hidden", className)}>
      {children}
    </div>
  );
}
