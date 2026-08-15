// Route shell (V-014): auth-gated app routes render inside the one
// AppShell. The dev-only /gallery component-review page (V-002) was
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
import { ArchivePage } from "./archive/Archive";
import { AuditLogPage } from "./audit/AuditLog";
import { RequireAuth } from "./auth/RequireAuth";
import { CheckProgressPage } from "./check/Progress";
import { FlagDetailPage } from "./flags/FlagDetail";
import { LandingRoute } from "./pages/Landing";
import { DashboardPage } from "./pages/Dashboard";
import { SignInPage } from "./pages/SignIn";
import { AdviserViewPage } from "./report/AdviserView";
import { ReportPage } from "./report/Report";
import { ManageRubricPage } from "./rubric/Manage";
import { ReviewCriteriaPage } from "./rubric/ReviewCriteria";
import { SettingsPage } from "./settings/Settings";
import { AppShell } from "./shell/AppShell";

const queryClient = new QueryClient();

const router = createBrowserRouter(
  createRoutesFromElements(
    <>
      <Route path="/" element={<LandingRoute />} />
      <Route path="/signin" element={<SignInPage />} />
      {/* V-040 (screen 4l): deliberately outside RequireAuth/AppShell --
          a public, unauthenticated read reached by a token in the URL,
          not a session. */}
      <Route path="/shared/:token" element={<AdviserViewPage />} />
      <Route
        path="/dashboard"
        element={
          <RequireAuth>
            <AppShell>
              <DashboardPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/rubric"
        element={
          <RequireAuth>
            <AppShell>
              <ManageRubricPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/archive"
        element={
          <RequireAuth>
            <AppShell>
              <ArchivePage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <AppShell>
              <SettingsPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/rubric/:rubricId/review"
        element={
          <RequireAuth>
            <AppShell>
              <ReviewCriteriaPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/checks/:checkRunId"
        element={
          <RequireAuth>
            <AppShell>
              <CheckProgressPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/report/:checkRunId"
        element={
          <RequireAuth>
            <AppShell>
              <ReportPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/flags/:flagId"
        element={
          <RequireAuth>
            <AppShell>
              <FlagDetailPage />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/audit"
        element={
          <RequireAuth>
            <AppShell>
              <AuditLogPage />
            </AppShell>
          </RequireAuth>
        }
      />
    </>,
  ),
);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
