import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import type { Rubric } from "../api/types";
import { RubricSourcePane } from "./RubricSourcePane";
import { Button } from "../ui/Button";

type View = "criteria" | "source";

export function FormatReviewWorkbench({ rubric, onUploadNewVersion, children }: {
  rubric: Pick<Rubric, "id" | "rubric_family_id" | "version">;
  onUploadNewVersion: () => void;
  children: ReactNode;
}) {
  const [params] = useSearchParams();
  const [view, setView] = useState<View>(params.get("source") === "original" ? "source" : "criteria");
  const [focusTab, setFocusTab] = useState<View>(view);
  const [split, setSplit] = useState(false);
  const frame = useRef<HTMLDivElement>(null);
  const threshold = useRef<HTMLSpanElement>(null);
  const tablist = useRef<HTMLDivElement>(null);
  const criteriaTab = useRef<HTMLButtonElement>(null);
  const sourceTab = useRef<HTMLButtonElement>(null);
  const criteriaHeading = useRef<HTMLHeadingElement>(null);
  const sourceEntry = useRef<HTMLButtonElement>(null);
  const sourcePanel = useRef<HTMLElement>(null);
  const criteriaPanel = useRef<HTMLElement>(null);
  const lastPaneFocus = useRef<View | null>(null);
  const measuredSplit = useRef(false);
  const pendingReturn = useRef(false);
  const pendingSourceFocus = useRef(false);
  const id = useId();

  useLayoutEffect(() => {
    const element = frame.current;
    if (!element) return;
    const measure = () => {
      const minimum = threshold.current?.getBoundingClientRect().width ?? 0;
      if (minimum <= 0) return;
      const nextSplit = element.getBoundingClientRect().width >= minimum;
      if (measuredSplit.current && !nextSplit) {
        const active = window.document.activeElement;
        const focusedView = sourcePanel.current?.contains(active) ? "source"
          : criteriaPanel.current?.contains(active) ? "criteria" : lastPaneFocus.current;
        if (focusedView) { setView(focusedView); setFocusTab(focusedView); }
      }
      measuredSplit.current = nextSplit;
      setSplit(nextSplit);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (pendingReturn.current && (split || view === "criteria")) {
      pendingReturn.current = false;
      const target = sourceEntry.current?.getClientRects().length ? sourceEntry.current : criteriaHeading.current;
      target?.focus({ preventScroll: true });
    } else if (pendingSourceFocus.current && (split || view === "source")) {
      pendingSourceFocus.current = false;
      sourcePanel.current?.focus({ preventScroll: true });
    } else if (split && tablist.current?.contains(window.document.activeElement)) {
      (view === "source" ? sourcePanel.current : criteriaHeading.current)?.focus({ preventScroll: true });
    }
  }, [split, view]);

  function activate(next: View) { setView(next); setFocusTab(next); }
  function focusOtherTab(event: React.KeyboardEvent, current: View) {
    const next = event.key === "Home" ? "criteria" : event.key === "End" ? "source"
      : event.key === "ArrowLeft" || event.key === "ArrowRight" ? current === "criteria" ? "source" : "criteria" : null;
    if (!next) return;
    event.preventDefault();
    setFocusTab(next);
    (next === "criteria" ? criteriaTab : sourceTab).current?.focus();
  }

  return <div ref={frame} className={`signal-format-workbench${split ? " is-split" : ""}`}>
    <span ref={threshold} className="signal-format-workbench__threshold" aria-hidden="true" />
    <div ref={tablist} role="tablist" aria-label="Format review views" hidden={split} className="signal-format-tabs"
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocusTab(view); }}>
      <button ref={criteriaTab} id={`${id}-criteria-tab`} type="button" role="tab" aria-selected={view === "criteria"}
        aria-controls={`${id}-criteria`} tabIndex={focusTab === "criteria" ? 0 : -1}
        onKeyDown={(event) => focusOtherTab(event, "criteria")} onClick={() => activate("criteria")}>Criteria</button>
      <button ref={sourceTab} id={`${id}-source-tab`} type="button" role="tab" aria-selected={view === "source"}
        aria-controls={`${id}-source`} tabIndex={focusTab === "source" ? 0 : -1}
        onKeyDown={(event) => focusOtherTab(event, "source")} onClick={() => activate("source")}>Original format</button>
    </div>
    <section ref={sourcePanel} tabIndex={-1} onFocusCapture={() => { lastPaneFocus.current = "source"; }} id={`${id}-source`} role={split ? "region" : "tabpanel"} aria-label={split ? "Original format" : undefined}
      aria-labelledby={split ? undefined : `${id}-source-tab`} hidden={!split && view !== "source"}
      className="signal-format-workbench__source">
      {!split && <Button variant="quiet" onClick={() => { pendingReturn.current = true; activate("criteria"); }}>Back to criteria</Button>}
      <RubricSourcePane rubric={rubric} isVisible={split || view === "source"} onUploadNewVersion={onUploadNewVersion} />
    </section>
    <section ref={criteriaPanel} onFocusCapture={() => { lastPaneFocus.current = "criteria"; }} id={`${id}-criteria`} role={split ? "region" : "tabpanel"} aria-label={split ? "Criteria" : undefined}
      aria-labelledby={split ? undefined : `${id}-criteria-tab`} hidden={!split && view !== "criteria"}
      className="signal-format-workbench__criteria">
      <header className="signal-format-workbench__criteria-heading">
        <h2 ref={criteriaHeading} tabIndex={-1}>Criteria</h2>
        {!split && <Button ref={sourceEntry} variant="secondary" onClick={() => { pendingSourceFocus.current = true; activate("source"); }}>Read original format</Button>}
      </header>
      <div className="signal-format-editor-scroll" tabIndex={0} role="region" aria-label="Criteria, scrollable">
        {children}
      </div>
    </section>
  </div>;
}
