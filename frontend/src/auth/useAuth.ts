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

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/auth/logout"),
    onSuccess: () => {
      queryClient.setQueryData(ME_QUERY_KEY, null);
    },
  });
}
