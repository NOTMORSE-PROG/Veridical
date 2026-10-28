// Screen 4i uses the approved manuscript-and-review workspace.
import { Navigate, useLocation, useParams } from "react-router";
import { recoverableReadError } from "../report/reportReadState";
import { reviewDeskDestination } from "../routing/reviewDeskReturn";
import { FlagDetailPage } from "./FlagDetail";
import { useFlag } from "./useFlag";

export function FlagWorkspaceRoute() {
  const { flagId } = useParams<{ flagId: string }>();
  const location = useLocation();
  const id = Number(flagId);
  const query = useFlag(id);
  const flag = query.data;
  if (Number.isSafeInteger(id) && id > 0 && flag?.id === id && Number.isSafeInteger(flag.check_run_id)
    && flag.check_run_id > 0 && recoverableReadError(query.error)) {
    const params = new URLSearchParams({ finding: String(id), view: "finding", module: flag.check_kind });
    return <Navigate replace to={reviewDeskDestination(`/report/${flag.check_run_id}/document?${params}`, location)} state={location.state} />;
  }
  // Preserve the current invalid-address, denied-read and retry presentation.
  return <FlagDetailPage />;
}
