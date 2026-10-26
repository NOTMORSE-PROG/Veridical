import { Outlet } from "react-router";
import { RouteAnnouncer } from "./RouteAnnouncer";
import { WorkspaceSampleBar } from "../demo/WorkspaceSampleBar";

/**
 * One router-level frame that persists across public, authentication,
 * authenticated, and shared-report routes.
 */
export function RouteFrame() {
  return <><WorkspaceSampleBar /><Outlet /><RouteAnnouncer /></>;
}
