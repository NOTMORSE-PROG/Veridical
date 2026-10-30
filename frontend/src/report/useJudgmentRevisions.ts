// F3.5/F8.3, screens 4h/4w: private revision drafts share the report route lifetime.
import { useLayoutEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { JudgmentContext, JudgmentHistory, JudgmentRecordInput, JudgmentRevisionInput, JudgmentRevisionOutput, JudgmentSnapshot, RevisionResolution } from "../api/judgments";
import type { ReportOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { CRITERION_SCORE_UNITS, INSTRUCTOR_NOTE_MAX_LENGTH, RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";
import { keepNewerAssessment } from "./assessmentRevision";
import { contextInReport, contextMatches, historyEntryMatches, recordAcknowledged, revisionAcknowledged, sameJudgmentGrade, snapshotValid, type JudgmentProposal } from "./judgmentRecord";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { criterionRecordId } from "./savedJudgment";

export interface RevisionDraft {
  kind: "record" | "revise";
  bound: JudgmentContext;
  resolution?: RevisionResolution;
  level?: number;
  reason: string;
  frozen: boolean;
  attempted: boolean;
  saving: boolean;
  sending?: number;
  uncertainRevision?: number;
  error?: string;
}
export interface RevisionRecord {
  unavailable?: boolean;
  context?: JudgmentContext;
  draft?: RevisionDraft;
  receipt?: JudgmentRevisionOutput;
  loading: boolean;
  readOperation: number;
  readError?: string;
  historyOpen: boolean;
  history?: JudgmentHistory;
  historyLoading?: boolean;
  historyError?: string;
  historyOperation?: number;
  abandonedAttempt?: { kind: "record" | "revise"; expectedRevision: number; expectedJudgmentRevision?: number; generation: number; unknown: true };
}
export interface RevisionScope {
  isCurrent: () => boolean;
  currentContext: (context: JudgmentContext) => boolean;
  records: ReadonlyMap<number, RevisionRecord>;
  hasDrafts: boolean;
  busy: boolean;
  uncertain: boolean;
  open: (criterion: number, purpose: "record" | "revise" | "history" | "current") => void;
  closeHistory: (criterion: number) => void;
  history: (criterion: number, more?: boolean) => void;
  change: (criterion: number, change: Pick<Partial<RevisionDraft>, "resolution" | "level" | "reason">) => void;
  discard: (criterion: number) => void;
  canWrite: (draft: RevisionDraft) => boolean;
  canRebind: (criterion: number) => boolean;
  rebind: (criterion: number) => void;
  confirm: (criterion: number) => Promise<boolean>;
  proposal: (draft: RevisionDraft) => JudgmentProposal | undefined;
  review: (criterion: number) => boolean;
  canStop: () => boolean;
  hasUnknownAttempt: boolean;
  captureDrafts: () => ReadonlyMap<number, RevisionDraft>;
  abandon: (criterion: number, captured: RevisionDraft) => boolean;
  abandonAll: (captured: ReadonlyMap<number, RevisionDraft>) => boolean;
}

export function useJudgmentRevisions(checkRunId: number, ownerCurrent: () => boolean,
  report: ReportOut | undefined, reportReady: boolean, refreshReport: () => Promise<{ isSuccess: boolean; data?: ReportOut }>, otherWriteActive: () => boolean): RevisionScope {
  const client = useQueryClient();
  const store = useRef(new Map<number, RevisionRecord>());
  const [records, setRecords] = useState<ReadonlyMap<number, RevisionRecord>>(store.current);
  const sequence = useRef(0);
  const [actor] = useState(() => client.getQueryData<{ id: number }>(ME_QUERY_KEY)?.id);
  const retired = useRef(false);
  const unavailableFocus = useRef(new Map<number, FocusContinuation>());
  const owner = useRef(ownerCurrent);
  owner.current = ownerCurrent;
  const isCurrent = () => !retired.current && owner.current();
  useLayoutEffect(() => client.getQueryCache().subscribe(() => {
    if (!owner.current()) retired.current = true;
  }), [client]);
  useLayoutEffect(() => {
    const continuations = unavailableFocus.current;
    return () => { store.current.clear(); continuations.forEach(claim => claim.dispose()); continuations.clear(); };
  }, []);
  useLayoutEffect(() => {
    unavailableFocus.current.forEach((claim, criterion) => claim.focus(document.getElementById(criterionRecordId(checkRunId, criterion))));
    unavailableFocus.current.clear();
  });
  function unavailable(criterion: number, error: unknown) {
    if (!(error instanceof ApiError) || error.status !== 404) return false;
    const active = document.activeElement;
    const ownDialog = active?.closest("[role='dialog']")?.querySelector(`[data-judgment-dialog="judgment-revision-${checkRunId}-${criterion}"]`);
    if (active?.id === `judgment-revision-${checkRunId}-${criterion}-heading`
      || active?.id === `judgment-revision-${checkRunId}-${criterion}-history-heading` || ownDialog) {
      unavailableFocus.current.set(criterion, claimFocusContinuation(active));
    }
    put(criterion, { unavailable: true, loading: false, readOperation: ++sequence.current, historyOpen: false });
    return true;
  }
  function initialChoice(context: JudgmentContext) {
    const current = context.current;
    const resolution = current?.resolution_type === "accept_majority"
      ? current.level ? "mark_level" : current.score === CRITERION_SCORE_UNITS.full ? "mark_pass"
        : current.outcome === "failed" ? "mark_fail" : undefined
      : current?.resolution_type;
    return { resolution, level: current?.level?.ordinal };
  }
  function changed(draft: RevisionDraft) {
    const baseline: { resolution?: RevisionResolution; level?: number } = draft.kind === "record" ? {} : initialChoice(draft.bound);
    return draft.reason.length > 0 || draft.resolution !== baseline.resolution || draft.level !== baseline.level
      || draft.saving || draft.uncertainRevision !== undefined;
  }
  const canStop = () => isCurrent() && !otherWriteActive() && ![...store.current.values()].some(record => record.draft?.saving);
  function withoutDraft(record: RevisionRecord): RevisionRecord {
    const draft = record.draft;
    return { ...record, draft: undefined, loading: false, readOperation: ++sequence.current,
      historyLoading: false, historyOperation: ++sequence.current,
      abandonedAttempt: draft?.uncertainRevision !== undefined
        ? { kind: draft.kind, expectedRevision: draft.uncertainRevision, expectedJudgmentRevision: draft.bound.current?.judgment_revision,
          generation: draft.sending!, unknown: true } : record.abandonedAttempt };
  }
  function put(id: number, record: RevisionRecord) {
    if (!isCurrent()) return;
    store.current = new Map(store.current).set(id, record);
    setRecords(store.current);
  }
  function authFailure(error: unknown) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      store.current.clear(); setRecords(new Map());
      client.setQueryData(ME_QUERY_KEY, null);
      return true;
    }
    return false;
  }
  const canWrite = (draft: RevisionDraft) => reportReady && report?.decision === null
    && (draft.kind === "record" ? draft.bound.can_record === true && draft.bound.current === null
      : draft.bound.can_revise && draft.bound.current !== null)
    && !draft.frozen && !draft.saving && draft.uncertainRevision === undefined
    && report.assessment_revision === draft.bound.assessment_revision;
  function proposal(draft: RevisionDraft): JudgmentProposal | undefined {
    const current = draft.bound.current;
    if ((draft.kind === "revise" && !current) || !draft.resolution) return;
    if (draft.resolution !== "needs_document" && (draft.bound.choices.kind === "levelled"
      ? draft.resolution !== "mark_level" : draft.resolution === "mark_level")) return;
    const common = { resolution_type: draft.resolution, reason: draft.reason.trim() };
    if (draft.resolution === "needs_document") return { ...common, level: null, outcome: "not_applicable", score: null };
    if (draft.resolution === "mark_level") {
      const rung = draft.bound.choices.levels.find(level => level.level === draft.level);
      if (!rung) return;
      // The draft label is the selected named grade, not client-computed score arithmetic.
      return { ...common, level: { name: rung.name, ordinal: rung.level, points: rung.points,
        max_points: Math.max(...draft.bound.choices.levels.map(level => level.points)) }, outcome: "passed", score: current?.score ?? null };
    }
    return { ...common, level: null, outcome: draft.resolution === "mark_pass" ? "passed" : "failed",
      score: draft.resolution === "mark_pass" ? CRITERION_SCORE_UNITS.full : CRITERION_SCORE_UNITS.none };
  }
  function canRebind(criterion: number) {
    const record = store.current.get(criterion);
    const current = record?.context;
    const draft = record?.draft;
    return Boolean(current && (current.current ? current.can_revise : current.can_record) && reportReady && report?.decision === null
      && !record?.loading && !record?.readError && draft && !draft.saving
      && report.assessment_revision === current.assessment_revision
      && (draft.uncertainRevision === undefined || current.assessment_revision > draft.uncertainRevision));
  }
  function validation(draft: RevisionDraft): string | undefined {
    const proposed = proposal(draft);
    const reason = draft.reason.trim();
    if (!proposed) return draft.kind === "record" ? "Choose your judgment before continuing." : "Choose a supported revised judgment.";
    if (!reason) return draft.kind === "record" ? "Enter a reason for your judgment." : "Enter a new reason for this revision.";
    if (reason.length < RESOLUTION_REASON_MIN_LENGTH) return `Enter at least ${RESOLUTION_REASON_MIN_LENGTH} characters for the new reason.`;
    if (reason.length > INSTRUCTOR_NOTE_MAX_LENGTH) return `Use at most ${INSTRUCTOR_NOTE_MAX_LENGTH} characters for the new reason.`;
    if (draft.bound.current && sameJudgmentGrade(proposed, draft.bound.current) && reason === draft.bound.current.reason.trim()) {
      return "Change the judgment or enter a new reason before saving.";
    }
  }
  async function loadHistory(criterion: number, more = false) {
    const record = store.current.get(criterion);
    const context = record?.context;
    if (!isCurrent() || !record || !context?.current || record.historyLoading || record.loading
      || (more && !record.history?.next_cursor)) return;
    const operation = ++sequence.current;
    const previous = more ? record.history : undefined;
    const query = new URLSearchParams({ expected_revision: String(context.assessment_revision),
      expected_judgment_revision: String(context.current.judgment_revision) });
    if (previous?.next_cursor) query.set("cursor", previous.next_cursor);
    put(criterion, { ...record, historyOpen: true, historyLoading: true, historyError: undefined, historyOperation: operation });
    try {
      const page = await api.get<JudgmentHistory>(`/check-runs/${checkRunId}/criteria/${criterion}/judgment/history?${query}`);
      const latest = store.current.get(criterion);
      if (!isCurrent() || latest?.historyOperation !== operation) return;
      if (page.check_run_id !== checkRunId || page.criterion_id !== criterion || page.check_result_id !== context.check_result_id
        || page.assessment_revision !== context.assessment_revision || page.judgment_revision !== context.current.judgment_revision
        || !snapshotValid(page.current) || !sameJudgmentGrade(page.current, context.current)
        || page.current.judgment_revision !== context.current.judgment_revision
        || page.current.reason !== context.current.reason || !Array.isArray(page.entries)
        || typeof page.history_incomplete !== "boolean"
        || (page.next_cursor !== null && typeof page.next_cursor !== "string")
        || page.entries.some(entry => !historyEntryMatches(entry, context))
        || new Set([...(previous?.entries ?? []), ...page.entries].map(entry => entry.entry_id)).size
          !== (previous?.entries.length ?? 0) + page.entries.length) throw new Error("Invalid history response");
      put(criterion, { ...latest, historyLoading: false, history: { ...page,
        history_incomplete: Boolean(page.history_incomplete || previous?.history_incomplete),
        entries: [...(previous?.entries ?? []), ...page.entries] } });
    } catch (error) {
      const latest = store.current.get(criterion);
      if (!isCurrent() || latest?.historyOperation !== operation || authFailure(error) || unavailable(criterion, error)) return;
      put(criterion, { ...latest, historyLoading: false, historyError: "Judgment history could not be loaded. Review the current judgment before retrying history." });
    }
  }
  async function open(criterion: number, purpose: "record" | "revise" | "history" | "current") {
    if (!isCurrent()) return;
    const previous = store.current.get(criterion);
    if (previous?.draft?.saving) return;
    const operation = ++sequence.current;
    put(criterion, { ...previous, loading: true, readError: undefined, readOperation: operation,
      historyOpen: purpose === "history" || previous?.historyOpen === true,
      draft: previous?.draft ? { ...previous.draft, frozen: true } : undefined });
    try {
      const [context, currentReport] = await Promise.all([
        api.get<JudgmentContext>(`/check-runs/${checkRunId}/criteria/${criterion}/judgment`), refreshReport(),
      ]);
      const latest = store.current.get(criterion);
      if (!isCurrent() || latest?.readOperation !== operation) return;
      if (!contextMatches(context, checkRunId, criterion) || !currentReport.isSuccess
        || !contextInReport(context, currentReport.data)) throw new Error("The current assessment could not be bound.");
      const changed = latest.context?.assessment_revision !== context.assessment_revision
        || latest.context?.current?.judgment_revision !== context.current?.judgment_revision;
      let draft = latest.draft;
      if (purpose === "revise" && !draft && context.can_revise && context.current) {
        draft = { kind: "revise", bound: context, ...initialChoice(context), reason: "",
          frozen: false, attempted: false, saving: false };
      }
      if (purpose === "record" && !draft && context.can_record && context.current === null) {
        draft = { kind: "record", bound: context, reason: "", frozen: false, attempted: false, saving: false };
      }
      put(criterion, { ...latest, context, draft, loading: false, unavailable: false,
        history: changed ? undefined : latest.history,
        historyLoading: false, historyOperation: ++sequence.current, historyError: undefined });
      if (purpose === "history") void loadHistory(criterion);
    } catch (error) {
      const latest = store.current.get(criterion);
      if (!isCurrent() || latest?.readOperation !== operation || authFailure(error) || unavailable(criterion, error)) return;
      put(criterion, { ...latest, loading: false, readError: "The current judgment could not be loaded." });
    }
  }
  return {
    isCurrent,
    currentContext: context => reportReady && report?.assessment_revision === context.assessment_revision,
    records,
    hasDrafts: [...records.values()].some(record => record.draft && changed(record.draft)),
    hasUnknownAttempt: [...records.values()].some(record => record.abandonedAttempt || record.draft?.uncertainRevision !== undefined),
    busy: [...records.values()].some(record => record.draft?.saving),
    uncertain: [...records.values()].some(record => record.draft?.uncertainRevision !== undefined),
    open: (criterion, purpose) => { void open(criterion, purpose); },
    closeHistory: criterion => { const record = store.current.get(criterion); if (record) put(criterion, { ...record, historyOpen: false }); },
    history: (criterion, more) => { void loadHistory(criterion, more); },
    canWrite, canRebind, proposal,
    canStop,
    captureDrafts: () => new Map([...store.current].flatMap(([id, record]) => record.draft ? [[id, record.draft] as const] : [])),
    abandon(criterion, captured) {
      const record = store.current.get(criterion);
      if (!canStop() || record?.draft !== captured || captured.uncertainRevision === undefined) return false;
      put(criterion, withoutDraft(record));
      return true;
    },
    abandonAll(captured) {
      if (!canStop()) return false;
      const currentDrafts = [...store.current].filter(([, record]) => record.draft);
      if (currentDrafts.length !== captured.size || currentDrafts.some(([id, record]) => captured.get(id) !== record.draft)) return false;
      store.current = new Map([...store.current].map(([id, record]) => [id, record.draft ? withoutDraft(record) : record]));
      setRecords(store.current);
      return true;
    },
    review(criterion) {
      const record = store.current.get(criterion);
      if (!isCurrent() || !record?.draft || !canWrite(record.draft)) return false;
      const error = validation(record.draft);
      put(criterion, { ...record, draft: { ...record.draft, attempted: true, error } });
      return !error;
    },
    change(criterion, change) {
      const record = store.current.get(criterion);
      if (record?.draft && canWrite(record.draft)) put(criterion, { ...record, draft: { ...record.draft, ...change, attempted: false, error: undefined } });
    },
    discard(criterion) {
      const record = store.current.get(criterion);
      if (record?.draft && !record.draft.saving && record.draft.uncertainRevision === undefined) {
        put(criterion, { ...record, draft: undefined, readOperation: ++sequence.current });
      }
    },
    rebind(criterion) {
      const record = store.current.get(criterion);
      if (!record?.draft || !record.context || !canRebind(criterion)) return;
      put(criterion, { ...record, draft: { ...record.draft, kind: record.context.current ? "revise" : "record", bound: record.context, frozen: false,
        attempted: false, uncertainRevision: undefined, error: undefined } });
    },
    async confirm(criterion) {
      const record = store.current.get(criterion);
      const draft = record?.draft;
      if (!isCurrent() || !record || !draft || !canWrite(draft)) return false;
      const reason = draft.reason.trim();
      const error = validation(draft);
      if (error) {
        put(criterion, { ...record, draft: { ...draft, attempted: true, error } });
        return false;
      }
      const sending = ++sequence.current;
      const common: JudgmentRecordInput = { expected_revision: draft.bound.assessment_revision,
        resolution: draft.resolution!, level: draft.resolution === "mark_level" ? draft.level : undefined, reason };
      const submission: { kind: "record"; body: JudgmentRecordInput } | { kind: "revise"; body: JudgmentRevisionInput; before: JudgmentSnapshot }
        = draft.kind === "record" ? { kind: "record", body: common }
          : { kind: "revise", body: { ...common, expected_judgment_revision: draft.bound.current!.judgment_revision }, before: draft.bound.current! };
      const body = submission.body;
      put(criterion, { ...record, draft: { ...draft, saving: true, sending, error: undefined } });
      try {
        const out = await api.post<JudgmentRevisionOutput>(`/check-runs/${checkRunId}/criteria/${criterion}/judgment/${submission.kind === "record" ? "records" : "revisions"}`, body);
        const latest = store.current.get(criterion);
        if (!isCurrent() || latest?.draft?.sending !== sending) return false;
        if (actor === undefined || !(submission.kind === "record"
          ? recordAcknowledged(out, checkRunId, criterion, submission.body, draft.bound, actor)
          : revisionAcknowledged(out, checkRunId, criterion, submission.body, submission.before, actor, draft.bound.check_result_id))) throw new Error("Invalid judgment acknowledgment");
        client.setQueryData<ReportOut>(["report", checkRunId], current => keepNewerAssessment(current, out.report));
        put(criterion, { ...latest, context: out.context, draft: undefined, receipt: out, history: undefined,
          historyOperation: ++sequence.current, historyLoading: false });
        void client.invalidateQueries({ queryKey: ["report", checkRunId], exact: true });
        void client.invalidateQueries({ queryKey: ["report", checkRunId, "escalated"] });
        void client.invalidateQueries({ queryKey: ["stats"] });
        return true;
      } catch (error) {
        const latest = store.current.get(criterion);
        if (!isCurrent() || latest?.draft?.sending !== sending || authFailure(error) || unavailable(criterion, error)) return false;
        const refused = error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408;
        put(criterion, { ...latest, draft: { ...latest.draft, saving: false, frozen: true,
          uncertainRevision: refused ? undefined : body.expected_revision,
          error: refused ? "This revision was not accepted. Review the current judgment before using this draft."
            : "Saving could not be confirmed. Your entered revision is kept. Review the current judgment before trying again." } });
        void client.invalidateQueries({ queryKey: ["report", checkRunId], exact: true });
        void client.invalidateQueries({ queryKey: ["report", checkRunId, "escalated"] });
        return false;
      }
    },
  };
}
