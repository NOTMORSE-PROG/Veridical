// Route shell (V-014): auth-gated app routes render inside the one
// AppShell; /gallery stays the ungated V-002 component review page.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { RequireAuth } from "./auth/RequireAuth";
import { GalleryPage } from "./gallery/GalleryPage";
import { DashboardPage } from "./pages/Dashboard";
import { SignInPage } from "./pages/SignIn";
import { ManageRubricPage } from "./rubric/Manage";
import { ReviewCriteriaPage } from "./rubric/ReviewCriteria";
import { AppShell } from "./shell/AppShell";

const queryClient = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/signin" element={<SignInPage />} />
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
            path="/rubric/:rubricId/review"
            element={
              <RequireAuth>
                <AppShell>
                  <ReviewCriteriaPage />
                </AppShell>
              </RequireAuth>
            }
          />
          <Route path="/gallery" element={<GalleryPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
