// Route shell (V-014): auth-gated app routes render inside the shared
// SignalShell. The dev-only /gallery component-review page (V-002) was
// removed in V-056 — it was the last consumer of the LEGACY token block
// and the four legacy-only components (Button/KpiCard/Panel/Pill); every
// real screen has been on the current token system since V-055.
//
// BUG-037: a data router (createBrowserRouter/RouterProvider), not plain
// <BrowserRouter>, so ReviewCriteria.tsx can use react-router's native
// useBlocker to warn before an unsaved-edits navigation — no
// non-data-router equivalent exists as of react-router v6+ (the old v5
// `history.block`/`<Prompt>` were removed). `createRoutesFromElements`
// keeps this file's JSX shape unchanged; every route's own rendered
// behavior is identical to before.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, createRoutesFromElements, Route, RouterProvider } from "react-router";
import { AuditLogPage } from "./audit/AuditLog";
import { RequireAuth } from "./auth/RequireAuth";
import { CheckProgressPage } from "./check/Progress";
import { SignalDocumentViewerPage } from "./document/SignalDocumentViewer";
import { FlagDetailPage } from "./flags/FlagDetail";
import { SignalLibraryComparePage } from "./library/SignalLibraryCompare";
import { SignalLibraryDetailPage } from "./library/SignalLibraryDetail";
import { SignalLibraryPage } from "./library/SignalLibrary";
import { LandingRoute } from "./pages/Landing";
import { DashboardPage } from "./pages/Dashboard";
import { SignInPage } from "./pages/SignIn";
import { AdviserViewPage } from "./report/AdviserView";
import { SignalReportPage } from "./report/SignalReport";
import { RouteFrame } from "./routing/RouteFrame";
import { ManageRubricPage } from "./rubric/Manage";
import { SignalReviewCriteriaPage } from "./rubric/SignalReviewCriteria";
import { SettingsPage } from "./settings/Settings";
import { SignalShell } from "./shell/SignalShell";

const queryClient = new QueryClient();

const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<RouteFrame />}>
        <Route path="/" element={<LandingRoute />} />
        <Route path="/signin" element={<SignInPage />} />
        {/* V-040 (screen 4l): deliberately outside RequireAuth/SignalShell --
            a public, unauthenticated read reached by a token in the URL,
            not a session. */}
        <Route path="/shared/:token" element={<AdviserViewPage />} />
        <Route
          element={
            <RequireAuth>
              <SignalShell />
            </RequireAuth>
          }
        >
          {/* A single persistent shell keeps destination navigation stable
              while instructors move between authenticated routes. */}
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/rubric" element={<ManageRubricPage />} />
          <Route path="/library" element={<SignalLibraryPage />} />
          <Route path="/library/compare" element={<SignalLibraryComparePage />} />
          <Route path="/library/:manuscriptId" element={<SignalLibraryDetailPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/rubric/:rubricId/review" element={<SignalReviewCriteriaPage />} />
          <Route path="/checks/:checkRunId" element={<CheckProgressPage />} />
          <Route path="/report/:checkRunId" element={<SignalReportPage />} />
          <Route path="/report/:checkRunId/document" element={<SignalDocumentViewerPage />} />
          <Route path="/flags/:flagId" element={<FlagDetailPage />} />
          <Route path="/audit" element={<AuditLogPage />} />
        </Route>
      </Route>
    ,
  ),
);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
