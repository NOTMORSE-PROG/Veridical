const READ_METHODS = new Set(["GET", "HEAD"]);
const REVIEW_DESK_QUEUES = new Set([
  "needs_review",
  "checking",
  "ready_to_decide",
  "complete",
  "not_checked"
]);
const REVIEW_DESK_SORTS = new Set([
  "needs_review_desc",
  "newest",
  "oldest",
  "group_asc"
]);
const REVIEW_DESK_STATUSES = new Set([
  "needs_attention",
  "checking",
  "checked",
  "decided",
  "not_checked"
]);

function hasOnlyKeys(searchParams, allowedKeys) {
  for (const key of searchParams.keys()) {
    if (!allowedKeys.has(key) || searchParams.getAll(key).length !== 1) return false;
  }
  return true;
}

function hasNoQuery(url) {
  return [...url.searchParams.keys()].length === 0;
}

function validBoundedInteger(value, maximum) {
  if (value === null || !/^\d+$/.test(value)) return false;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 1 && parsed <= maximum;
}

function validManuscriptQuery(url, config) {
  const allowed = new Set(["queue", "sort", "page", "page_size", "status", "needs_review"]);
  if (!hasOnlyKeys(url.searchParams, allowed)) return false;
  if (url.searchParams.get("page") !== "1") return false;
  if (!validBoundedInteger(url.searchParams.get("page_size"), config.maxManuscriptPageSize)) {
    return false;
  }
  const queue = url.searchParams.get("queue");
  const sort = url.searchParams.get("sort");
  const status = url.searchParams.get("status");
  const needsReview = url.searchParams.get("needs_review");
  if (
    queue === null
    || !REVIEW_DESK_QUEUES.has(queue)
    || sort === null
    || !REVIEW_DESK_SORTS.has(sort)
    || status === null
    || !REVIEW_DESK_STATUSES.has(status)
  ) return false;
  return (
    (queue === "needs_review"
      && sort === "needs_review_desc"
      && status === "needs_attention"
      && needsReview === null)
    || (queue === "ready_to_decide"
      && sort === "newest"
      && status === "checked"
      && needsReview === "false")
  );
}

function validAuditQuery(url, config) {
  const allowed = new Set(["page", "page_size"]);
  return (
    hasOnlyKeys(url.searchParams, allowed)
    && url.searchParams.get("page") === "1"
    && validBoundedInteger(url.searchParams.get("page_size"), config.maxAuditPageSize)
  );
}

function validReuseQuery(url) {
  const allowed = new Set(["include_reference_list", "include_block_quote"]);
  return (
    hasOnlyKeys(url.searchParams, allowed)
    && url.searchParams.get("include_reference_list") === "false"
    && url.searchParams.get("include_block_quote") === "false"
  );
}

function validClientRead(url, checkRunId, allowedFlagIds, staticPaths) {
  const noQuery = hasNoQuery(url);
  if (staticPaths.has(url.pathname) && noQuery) return true;
  if (
    (url.pathname === "/signin"
      || url.pathname === "/rubric"
      || url.pathname === "/audit")
    && noQuery
  ) return true;
  if (url.pathname === "/dashboard") {
    if (noQuery) return true;
    const allowedSearches = new Set([
      "?queue=needs_review",
      "?queue=ready_to_decide&sort=newest&status=checked&needs_review=false"
    ]);
    return allowedSearches.has(url.search);
  }
  if (url.pathname === `/report/${checkRunId}` && noQuery) return true;
  if (url.pathname === `/report/${checkRunId}/document`) {
    if (noQuery) return true;
    if (!hasOnlyKeys(url.searchParams, new Set(["flag"]))) return false;
    const flag = url.searchParams.get("flag");
    return flag !== null && /^\d+$/.test(flag) && allowedFlagIds.has(Number(flag));
  }
  const flagMatch = url.pathname.match(/^\/flags\/(\d+)$/);
  return Boolean(
    flagMatch
    && noQuery
    && allowedFlagIds.has(Number(flagMatch[1]))
  );
}

export function classifyRequest({
  requestUrl,
  method,
  origin,
  checkRunId,
  rubricFamilyId,
  allowedFlagIds,
  staticPaths,
  config
}) {
  let url;
  try {
    url = new URL(requestUrl);
  } catch {
    return { action: "deny", category: "MALFORMED_URL" };
  }

  if (url.username || url.password) {
    return { action: "deny", category: "URL_USERINFO" };
  }
  if (url.origin !== origin) return { action: "deny", category: "EXTERNAL_ORIGIN" };

  const upperMethod = method.toUpperCase();
  if (!url.pathname.startsWith("/api/")) {
    return READ_METHODS.has(upperMethod)
      && validClientRead(url, checkRunId, allowedFlagIds, staticPaths)
      ? { action: "continue", category: "CLIENT_RESOURCE" }
      : { action: "deny", category: "CLIENT_ROUTE" };
  }

  if (upperMethod === "POST" && hasNoQuery(url)) {
    if (url.pathname === "/api/auth/login") {
      return { action: "continue", category: "AUTH_LOGIN" };
    }
    if (url.pathname === "/api/auth/logout") {
      return { action: "continue", category: "AUTH_LOGOUT" };
    }
    return { action: "deny", category: "API_WRITE" };
  }
  if (upperMethod !== "GET") return { action: "deny", category: "API_METHOD" };

  const exactOwnAccountReads = new Map([
    ["/api/auth/session", "AUTH_SESSION"],
    ["/api/quota", "QUOTA"],
    ["/api/stats", "STATS"],
    ["/api/programs", "PROGRAMS"],
    ["/api/rubric-families", "RUBRIC_FAMILIES"]
  ]);
  const exactCategory = exactOwnAccountReads.get(url.pathname);
  if (exactCategory && hasNoQuery(url)) {
    return { action: "continue", category: exactCategory };
  }

  if (
    url.pathname === `/api/rubric-families/${rubricFamilyId}/versions`
    && hasNoQuery(url)
  ) {
    return { action: "continue", category: "RUBRIC_VERSIONS" };
  }
  if (url.pathname === "/api/manuscripts" && validManuscriptQuery(url, config)) {
    return { action: "continue", category: "MANUSCRIPTS" };
  }
  if (url.pathname === "/api/audit" && validAuditQuery(url, config)) {
    return { action: "continue", category: "AUDIT" };
  }

  const runBase = `/api/check-runs/${checkRunId}`;
  const exactRunReads = new Map([
    [`${runBase}/report`, "RUN_REPORT"],
    [`${runBase}/escalated`, "RUN_ESCALATED"],
    [`${runBase}/flags`, "RUN_FLAGS"],
    [`${runBase}/document`, "RUN_DOCUMENT"],
    [`${runBase}/document/paragraphs`, "RUN_PARAGRAPHS"]
  ]);
  const runCategory = exactRunReads.get(url.pathname);
  if (runCategory && hasNoQuery(url)) {
    return { action: "continue", category: runCategory };
  }

  if (url.pathname === `${runBase}/share` && hasNoQuery(url)) {
    return {
      action: "fulfill",
      category: "SHARE_NEUTRALIZED",
      status: 200,
      contentType: "application/json",
      body: "null"
    };
  }
  if (url.pathname === `${runBase}/document/reuse-matches` && validReuseQuery(url)) {
    return {
      action: "fulfill",
      category: "REUSE_NEUTRALIZED",
      status: 200,
      contentType: "application/json",
      body: '{"passage_archive_size_n":0,"matches":[]}'
    };
  }

  const flagMatch = url.pathname.match(/^\/api\/flags\/(\d+)$/);
  if (flagMatch && hasNoQuery(url) && allowedFlagIds.has(Number(flagMatch[1]))) {
    return { action: "continue", category: "RUN_FLAG_DETAIL" };
  }

  return { action: "deny", category: "API_ROUTE" };
}

export function createRequestGuard({ context, runtime, config }) {
  const allowedFlagIds = new Set();
  const violations = [];
  const observed = new Set();

  function classificationFor(request) {
    return classifyRequest({
      requestUrl: request.url(),
      method: request.method(),
      origin: runtime.origin,
      checkRunId: runtime.checkRunId,
      rubricFamilyId: runtime.rubricFamilyId,
      allowedFlagIds,
      staticPaths: runtime.staticPaths,
      config
    });
  }

  async function install() {
    await context.routeWebSocket(/.*/, async (webSocket) => {
      violations.push("WEBSOCKET");
      await webSocket.close({ code: 1008, reason: "Blocked by verification policy" });
    });
    await context.route("**/*", async (route) => {
      const classification = classificationFor(route.request());
      observed.add(classification.category);
      if (classification.action === "continue") {
        await route.continue();
        return;
      }
      if (classification.action === "fulfill") {
        await route.fulfill({
          status: classification.status,
          contentType: classification.contentType,
          body: classification.body
        });
        return;
      }
      violations.push(classification.category);
      await route.abort("blockedbyclient");
    });
  }

  function authorizeFlagSummary(summary) {
    if (
      summary === null
      || typeof summary !== "object"
      || summary.id !== runtime.flagId
      || !config.safeFindingKinds.includes(summary.check_kind)
    ) {
      throw new Error("configured finding is not an approved non-originality kind");
    }
    allowedFlagIds.add(runtime.flagId);
  }

  function requireAuthorizedFlagHref(href) {
    const url = new URL(href, runtime.origin);
    if (url.origin !== runtime.origin) throw new Error("invalid flag link origin");
    const match = url.pathname.match(/^\/flags\/(\d+)$/);
    if (!match) throw new Error("invalid flag link path");
    const flagId = Number(match[1]);
    if (!Number.isSafeInteger(flagId) || flagId <= 0) throw new Error("invalid flag identifier");
    if (flagId !== runtime.flagId || url.search || url.hash) {
      throw new Error("flag link does not match the configured synthetic finding");
    }
    if (!allowedFlagIds.has(flagId)) {
      throw new Error("configured finding was not authorized from the run summary");
    }
  }

  return {
    install,
    authorizeFlagSummary,
    requireAuthorizedFlagHref,
    configuredFlagIsAuthorized: () => allowedFlagIds.has(runtime.flagId),
    classificationFor,
    violations,
    observed
  };
}
