import assert from "node:assert/strict";
import test from "node:test";
import { classifyRequest, createRequestGuard } from "../lib/request-policy.mjs";
import {
  loadSmokeConfig,
  readRuntimeEnvironment,
  validateProductionOrigin
} from "../lib/runtime.mjs";

const config = loadSmokeConfig();
const origin = "https://example.test";
const checkRunId = 41;
const flagId = 7;
const rubricFamilyId = "123e4567-e89b-42d3-a456-426614174000";
const staticPaths = new Set(["/assets/app-123.js", "/favicon.svg", "/tip-logo.png"]);

test("the acceptance viewports remain the required ordered pair", () => {
  assert.deepEqual(config.viewports, [
    { label: "390x844", width: 390, height: 844 },
    { label: "320x480", width: 320, height: 480 }
  ]);
});

test("checked-in production origins must be credential-free HTTPS origins", () => {
  assert.equal(validateProductionOrigin("https://example.test/"), "https://example.test");
  assert.throws(() => validateProductionOrigin("http://example.test"));
  assert.throws(() => validateProductionOrigin("https://user@example.test"));
  assert.throws(() => validateProductionOrigin("https://example.test/path"));
  assert.throws(() => validateProductionOrigin("https://example.test?redirect=elsewhere"));
  assert.throws(() => validateProductionOrigin("https://example.test#fragment"));
});

function classify(requestUrl, method = "GET", allowedFlagIds = new Set()) {
  return classifyRequest({
    requestUrl,
    method,
    origin,
    checkRunId,
    rubricFamilyId,
    allowedFlagIds,
    staticPaths,
    config
  });
}

test("runtime uses the checked-in production origin and ignores URL environment values", () => {
  const hostileOrigins = [
    "https://veridical-app.vercel.app.evil.invalid",
    "https://sub.veridical-app.vercel.app",
    "https://veridical-app.vercel.app:444",
    "https://user:pass@veridical-app.vercel.app",
    "https://veridical-app.vercel.app?redirect=elsewhere",
    "https://veridical-app.vercel.app#fragment"
  ];
  for (const hostileOrigin of hostileOrigins) {
    const runtime = readRuntimeEnvironment({
      PROD_WEB_URL: hostileOrigin,
      PROD_API_URL: hostileOrigin,
      PROD_SMOKE_EMAIL: "synthetic@example.test",
      PROD_SMOKE_PASSWORD: "not-a-real-secret",
      PROD_SMOKE_CHECK_RUN_ID: "41",
      PROD_SMOKE_FLAG_ID: "7",
      PROD_SMOKE_RUBRIC_FAMILY_ID: rubricFamilyId,
      PROD_SMOKE_DIST_DIR: "synthetic-dist"
    });
    assert.equal(runtime.origin, config.productionWebOrigin);
    assert.equal(runtime.baseURL, config.productionWebOrigin);
    assert.equal(runtime.checkRunId, checkRunId);
    assert.equal(runtime.flagId, flagId);
    assert.equal(runtime.rubricFamilyId, rubricFamilyId);
  }
});

test("runtime rejects invalid synthetic fixture identifiers", () => {
  const common = {
    PROD_SMOKE_EMAIL: "synthetic@example.test",
    PROD_SMOKE_PASSWORD: "not-a-real-secret",
    PROD_SMOKE_CHECK_RUN_ID: "41",
    PROD_SMOKE_FLAG_ID: "7",
    PROD_SMOKE_RUBRIC_FAMILY_ID: rubricFamilyId,
    PROD_SMOKE_DIST_DIR: "synthetic-dist"
  };
  assert.throws(() => readRuntimeEnvironment({
    ...common,
    PROD_SMOKE_CHECK_RUN_ID: "not-an-id"
  }));
  assert.throws(() => readRuntimeEnvironment({
    ...common,
    PROD_SMOKE_FLAG_ID: "not-an-id"
  }));
  assert.throws(() => readRuntimeEnvironment({
    ...common,
    PROD_SMOKE_RUBRIC_FAMILY_ID: "not-a-uuid"
  }));
});

test("policy permits only the two named authentication writes", () => {
  assert.equal(classify(`${origin}/api/auth/login`, "POST").category, "AUTH_LOGIN");
  assert.equal(classify(`${origin}/api/auth/logout`, "POST").category, "AUTH_LOGOUT");
  assert.equal(classify(`${origin}/api/check-runs/41/decision`, "POST").action, "deny");
  assert.equal(classify(`${origin}/api/settings/api-key`, "POST").action, "deny");
});

test("policy bounds own-account list queries", () => {
  const manuscripts = new URL("/api/manuscripts", origin);
  manuscripts.search = new URLSearchParams({
    queue: "needs_review",
    sort: "needs_review_desc",
    page: "1",
    page_size: "20",
    status: "needs_attention"
  });
  assert.equal(classify(manuscripts.href).category, "MANUSCRIPTS");
  manuscripts.searchParams.set("page_size", "999");
  assert.equal(classify(manuscripts.href).action, "deny");

  assert.equal(classify(`${origin}/api/audit?page=1&page_size=25`).category, "AUDIT");
  assert.equal(classify(`${origin}/api/audit?page=2&page_size=25`).action, "deny");
});

test("policy confines report reads to the configured synthetic run", () => {
  assert.equal(classify(`${origin}/api/check-runs/41/report`).category, "RUN_REPORT");
  assert.equal(classify(`${origin}/api/check-runs/42/report`).action, "deny");
  assert.equal(classify(`${origin}/api/check-runs/41/document/file`).action, "deny");
  assert.equal(classify(`${origin}/api/library`).action, "deny");
});

test("share and reuse reads are neutralized locally instead of reaching production", () => {
  assert.equal(classify(`${origin}/api/check-runs/41/share`).action, "fulfill");
  const reuse = `${origin}/api/check-runs/41/document/reuse-matches?include_reference_list=false&include_block_quote=false`;
  assert.equal(classify(reuse).action, "fulfill");
  assert.equal(classify(`${origin}/api/check-runs/41/document/reuse-matches`).action, "deny");
});

test("a flag detail read is allowed only for the configured synthetic finding", () => {
  assert.equal(classify(`${origin}/api/flags/7`).action, "deny");
  assert.equal(classify(`${origin}/api/flags/7`, "GET", new Set([7])).category, "RUN_FLAG_DETAIL");
  assert.equal(classify(`${origin}/api/flags/8`, "GET", new Set([7])).action, "deny");
});

test("request guard blocks WebSockets and authorizes only an approved configured finding", async () => {
  let webSocketHandler;
  const context = {
    routeWebSocket: async (_pattern, handler) => {
      webSocketHandler = handler;
    },
    route: async () => undefined
  };
  const runtime = { origin, checkRunId, flagId, rubricFamilyId, staticPaths };
  const guard = createRequestGuard({ context, runtime, config });
  await guard.install();

  let closeOptions;
  await webSocketHandler({
    close: async (options) => {
      closeOptions = options;
    }
  });
  assert.deepEqual(closeOptions, { code: 1008, reason: "Blocked by verification policy" });
  assert.deepEqual(guard.violations, ["WEBSOCKET"]);

  assert.throws(() => guard.authorizeFlagSummary({
    id: flagId,
    check_kind: "originality_reuse"
  }));
  assert.throws(() => guard.authorizeFlagSummary({
    id: flagId + 1,
    check_kind: "citation_integrity"
  }));
  guard.authorizeFlagSummary({ id: flagId, check_kind: "citation_integrity" });
  assert.equal(guard.configuredFlagIsAuthorized(), true);
  guard.requireAuthorizedFlagHref(`/flags/${flagId}`);
  assert.throws(() => guard.requireAuthorizedFlagHref(`/flags/${flagId + 1}`));
});

test("external origins and unknown API routes fail closed", () => {
  assert.equal(classify("https://elsewhere.test/assets/app.js").action, "deny");
  assert.equal(classify("https://user:pass@example.test/assets/app-123.js").category, "URL_USERINFO");
  assert.equal(classify(`${origin}/api/unknown`).action, "deny");
  assert.equal(classify(`${origin}/assets/app-123.js`).category, "CLIENT_RESOURCE");
  assert.equal(classify(`${origin}/assets/70617373776f7264.js`).action, "deny");
  assert.equal(
    classify(`${origin}/api/rubric-families/${rubricFamilyId}/versions`).category,
    "RUBRIC_VERSIONS",
  );
  assert.equal(classify(`${origin}/api/rubric-families/SECRET123/versions`).action, "deny");
  assert.equal(classify(`${origin}/shared/unknown-token`).action, "deny");
  assert.equal(classify(`${origin}/settings`).action, "deny");
  assert.equal(classify(`${origin}/report/41/document?flag=7`).action, "deny");
  assert.equal(
    classify(`${origin}/report/41/document?flag=7`, "GET", new Set([7])).category,
    "CLIENT_RESOURCE",
  );
});
