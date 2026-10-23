// Audit log data layer (F8.10, screen 4s).
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { api } from "../api/client";
import { ME_QUERY_KEY, useMe } from "../auth/useAuth";
import type { AuditLogDetail, PaginatedAuditLog } from "../api/types";
import { recordedId } from "./instructorEventDetails";

const lifetimes = new WeakMap<object, number>();
let nextLifetime = 0;

export interface AuditOwner {
  instructorId: number;
  lifetime: number;
  query: object;
}

export function useAuditOwner() {
  const client = useQueryClient();
  const access = useMe();
  const query = useSyncExternalStore(
    notify => client.getQueryCache().subscribe(event => {
      if ((event.type === "added" || event.type === "removed")
        && event.query.queryKey.length === ME_QUERY_KEY.length
        && event.query.queryKey.every((part: unknown, index: number) => part === ME_QUERY_KEY[index])) notify();
    }),
    () => client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }),
  );
  let owner: AuditOwner | undefined;
  if (query && access.isSuccess && !access.error && recordedId(access.data?.id)) {
    if (!lifetimes.has(query)) lifetimes.set(query, ++nextLifetime);
    owner = { instructorId: access.data.id, lifetime: lifetimes.get(query)!, query };
  }
  return { owner, access };
}

export function auditOwnerCurrent(client: QueryClient, owner: AuditOwner | undefined) {
  const query = client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true });
  return owner !== undefined && query === owner.query && query.state.status === "success"
    && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === owner.instructorId;
}

export interface AuditLogFilters {
  checkRunId?: number;
  eventType?: string;
  eventTypePrefix?: string;
  dateFrom?: string;
  dateTo?: string;
}

function buildQuery(filters: AuditLogFilters, page: number, pageSize: number): string {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (filters.checkRunId !== undefined) params.set("check_run_id", String(filters.checkRunId));
  if (filters.eventType) params.set("event_type", filters.eventType);
  if (filters.eventTypePrefix) params.set("event_type_prefix", filters.eventTypePrefix);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  return params.toString();
}

export function useAuditLogPage(filters: AuditLogFilters, page: number, owner: AuditOwner, pageSize = 25) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["audit-log", owner.instructorId, owner.lifetime, "page", filters, page, pageSize],
    queryFn: async () => {
      if (!auditOwnerCurrent(client, owner)) throw new Error("Instructor access changed.");
      const result = await api.get<PaginatedAuditLog>(`/audit?${buildQuery(filters, page, pageSize)}`);
      if (!auditOwnerCurrent(client, owner)) throw new Error("Instructor access changed.");
      return result;
    },
    placeholderData: (previous, previousQuery) => {
      const key = previousQuery?.queryKey;
      return auditOwnerCurrent(client, owner) && key?.[1] === owner.instructorId
        && key?.[2] === owner.lifetime && JSON.stringify(key?.[4]) === JSON.stringify(filters)
        ? previous : undefined;
    },
  });
}

export function useAuditLogDetail(id: number, owner: AuditOwner) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["audit-log", owner.instructorId, owner.lifetime, "detail", id],
    queryFn: async () => {
      if (!auditOwnerCurrent(client, owner)) throw new Error("Instructor access changed.");
      const result = await api.get<AuditLogDetail>(`/audit/${id}`);
      if (!auditOwnerCurrent(client, owner)) throw new Error("Instructor access changed.");
      if (result.id !== id) throw new Error("The requested audit entry could not be confirmed.");
      return result;
    },
  });
}
