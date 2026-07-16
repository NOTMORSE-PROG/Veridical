import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL auto-cleanup needs vitest globals; we don't enable them, so register
// cleanup explicitly — without it rendered trees leak across tests.
afterEach(cleanup);
