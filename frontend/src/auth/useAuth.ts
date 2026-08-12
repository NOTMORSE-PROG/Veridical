// Auth state via the query cache — `me` IS the "am I signed in" source of
// truth every guard/shell reads (V-014, F9.1).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { Instructor } from "../api/types";

export const ME_QUERY_KEY = ["auth", "me"] as const;

export function useMe() {
  return useQuery<Instructor | null>({
    queryKey: ME_QUERY_KEY,
    queryFn: async () => {
      try {
        return await api.get<Instructor>("/auth/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      api.post<Instructor>("/auth/login", credentials),
    onSuccess: (instructor) => {
      queryClient.setQueryData(ME_QUERY_KEY, instructor);
    },
  });
}

export function useDismissOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Instructor>("/auth/onboarding/dismiss"),
    onSuccess: (instructor) => {
      queryClient.setQueryData(ME_QUERY_KEY, instructor);
    },
  });
}

// V-057: the coach-mark tour's replay control. Same field as
// useDismissOnboarding (onboarding_dismissed_at -> null) -- one piece
// of persisted state, not a second one; the tour's current-step
// position lives client-side only (a replay always restarts at step 1).
export function useReplayOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Instructor>("/auth/onboarding/replay"),
    onSuccess: (instructor) => {
      queryClient.setQueryData(ME_QUERY_KEY, instructor);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSuccess: () => {
      // BUG-009: a full clear, not a targeted reset — the QueryClient
      // instance lives for the tab's lifetime and query keys aren't
      // scoped by instructor id, so any survivor (dashboard stats, quota,
      // manuscript lists) could otherwise render to the NEXT instructor
      // who signs in on the same browser/tab (a real cross-instructor
      // data leak on a shared machine). Cite: TanStack Query maintainers,
      // GitHub Discussion #7839 "Handle logout and user-dependent
      // queries"; OWASP Session Management Cheat Sheet (invalidate state
      // at logout, not just the auth token).
      queryClient.clear();
      queryClient.setQueryData(ME_QUERY_KEY, null);
    },
  });
}
