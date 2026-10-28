import { ActionLink } from "../ui/ActionLink";
import { isWorkspaceSample } from "./workspaceFixture";

export function CompletedModuleCheckLinks() {
  if (!isWorkspaceSample()) return null;
  return <section className="signal-review-section signal-module-case-links" aria-labelledby="completed-checks-heading"><h2 id="completed-checks-heading">Completed check records</h2><p>These additional published papers demonstrate statistical checks and passage comparison. Their results are separate from the capstone's grades.</p><div className="signal-control-cluster"><ActionLink to="/module-checks/statistics" variant="secondary">Statistics: 3 results checked</ActionLink><ActionLink to="/module-checks/reuse" variant="secondary">Reuse: compare related passages</ActionLink></div></section>;
}
