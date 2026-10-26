import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { renderWithProviders } from "../test/renderWithProviders";
import { useAnnotateFlag, useConfirmCitationSource, useOverrideFlag } from "./useFlag";

afterEach(() => vi.unstubAllGlobals());
describe("flag mutation response lifetime", () => {
  for (const name of ["annotation", "override", "confirmation"] as const) {
    it.each(["current", "other instructor", "same instructor new login", "changed instructor", "wrong flag", "wrong run", "wrong report"])(`${name} binds cache writes to %s`, async (change) => {
      let client!: QueryClient; let release!: (response: Response) => void;
      const held = new Promise<Response>(resolve => { release = resolve; });
      vi.stubGlobal("fetch", vi.fn(() => held));
      function Harness() {
        client = useQueryClient();
        const annotation = useAnnotateFlag(7);
        const override = useOverrideFlag(7);
        const confirmation = useConfirmCitationSource(7);
        const action = name === "annotation" ? annotation : name === "override" ? override : confirmation;
        return <button onClick={() => {
          const body = { reason: "Reason retained for this action", expected_revision: 0 };
          if (name === "annotation") annotation.mutate(body.reason);
          else if (name === "override") override.mutate(body);
          else confirmation.mutate(body);
        }}>{action.isSuccess || action.isError ? "Completed" : "Submit"}</button>;
      }
      renderWithProviders(<Harness />);
      client.setQueryData(ME_QUERY_KEY, { id: 1 });
      client.setQueryData(["flag", 7], { id: 7, check_run_id: 5, annotation: "Current flag" });
      fireEvent.click(screen.getByRole("button", { name: "Submit" }));
      await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
      if (change === "other instructor" || change === "same instructor new login") {
        client.clear(); client.setQueryData(ME_QUERY_KEY, { id: change === "other instructor" ? 2 : 1 });
      }
      if (change === "changed instructor") client.setQueryData(ME_QUERY_KEY, { id: 2 });
      client.setQueryData(["report", 5], { check_run_id: 5, reason: "Current account report" });
      const out = { id: change === "wrong flag" ? 8 : 7, check_run_id: change === "wrong run" ? 6 : 5,
        assessment_revision: 1, overridden: true, confirmed_citation_source: true, override_reason: "Reason retained for this action", annotation: name === "annotation" ? "Reason retained for this action" : "Old response private annotation", report: { assessment_revision: 1, check_run_id: change === "wrong report" ? 6 : 5, reason: "Old private report" } };
      await act(async () => { release(new Response(JSON.stringify(out), { status: 200 })); });
      await screen.findByRole("button", { name: "Completed" });
      const accepted = change === "current" || name === "annotation" && change === "wrong report";
      expect(client.getQueryData<{ annotation: string }>(["flag", 7])?.annotation === out.annotation).toBe(accepted);
      expect(client.getQueryData<{ reason: string }>(["report", 5])?.reason).toBe(accepted && name !== "annotation" ? "Old private report" : "Current account report");
      expect(client.getQueryData(["flag", 8])).toBeUndefined();
      expect(client.getQueryData(["report", 6])).toBeUndefined();
    });
  }
});
