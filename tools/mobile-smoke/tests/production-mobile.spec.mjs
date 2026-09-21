import { test } from "@playwright/test";
import { buildManifest } from "../lib/deployment-fingerprint.mjs";
import { createRequestGuard } from "../lib/request-policy.mjs";
import { loadSmokeConfig, readRuntimeEnvironment } from "../lib/runtime.mjs";

const smokeConfig = loadSmokeConfig();

function safeLine(stage, status, viewport) {
  const suffix = viewport ? ` viewport=${viewport}` : "";
  process.stdout.write(`V076 STAGE ${stage} ${status}${suffix}\n`);
}

async function runStage(stage, operation, viewport) {
  safeLine(stage, "START", viewport);
  try {
    const result = await operation();
    safeLine(stage, "PASS", viewport);
    return result;
  } catch {
    safeLine(stage, "FAIL", viewport);
    throw new Error(`V076_${stage}`);
  }
}

function ensure(condition) {
  if (!condition) throw new Error("V076_ASSERTION");
}

function isProductionSessionCookie(cookie) {
  return (
    cookie.name === smokeConfig.sessionCookieName
    && cookie.httpOnly
    && cookie.secure
    && cookie.sameSite === "Lax"
  );
}

function startsWithAccessibleName(label) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${escaped}\\b`);
}

async function ensureTapTarget(locator, minimumSize) {
  await locator.waitFor({ state: "visible" });
  await locator.scrollIntoViewIfNeeded();
  const usable = await locator.evaluate((element, minimum) => {
    const rectangle = element.getBoundingClientRect();
    const centerX = rectangle.left + rectangle.width / 2;
    const centerY = rectangle.top + rectangle.height / 2;
    const topElement = document.elementFromPoint(centerX, centerY);
    const style = window.getComputedStyle(element);
    const disabled = element instanceof HTMLButtonElement && element.disabled;
    return Boolean(
      rectangle.width >= minimum
      && rectangle.height >= minimum
      && rectangle.left >= 0
      && rectangle.right <= window.innerWidth
      && rectangle.top >= 0
      && rectangle.bottom <= window.innerHeight
      && topElement
      && element.contains(topElement)
      && style.visibility === "visible"
      && style.pointerEvents !== "none"
      && !disabled
    );
  }, minimumSize);
  ensure(usable);
}

async function tapTarget(locator) {
  await ensureTapTarget(locator, smokeConfig.minimumTargetCssPixels);
  await locator.tap();
}

async function waitForPath(page, runtime, relativePath) {
  const expected = new URL(relativePath, runtime.origin);
  await page.waitForURL((actual) => (
    actual.origin === expected.origin
    && actual.pathname === expected.pathname
    && actual.search === expected.search
  ));
}

async function ensureHeading(page, name) {
  const heading = page.getByRole("heading", { level: 1, name, exact: true });
  await heading.waitFor({ state: "visible" });
  ensure(await heading.count() === 1);
}

async function ensureLayoutInvariants(page, viewport) {
  const state = await page.evaluate(({ width, height }) => {
    const visualWidth = window.visualViewport?.width ?? window.innerWidth;
    const visualHeight = window.visualViewport?.height ?? window.innerHeight;
    return {
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      visualWidth,
      visualHeight,
      touchPoints: navigator.maxTouchPoints,
      coarsePointer: window.matchMedia("(pointer: coarse)").matches,
      mainCount: document.querySelectorAll("main").length,
      documentFits:
        document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      bodyFits: document.body.scrollWidth <= document.body.clientWidth,
      expectedWidth: width,
      expectedHeight: height
    };
  }, viewport);
  ensure(state.innerWidth === state.expectedWidth);
  ensure(state.innerHeight === state.expectedHeight);
  ensure(Math.abs(state.visualWidth - state.expectedWidth) <= 1);
  ensure(Math.abs(state.visualHeight - state.expectedHeight) <= 1);
  ensure(state.touchPoints > 0);
  ensure(state.coarsePointer);
  ensure(state.mainCount === 1);
  ensure(state.documentFits);
  ensure(state.bodyFits);
}

async function ensurePageInvariants(page, viewport, heading) {
  await ensureHeading(page, heading);
  await ensureLayoutInvariants(page, viewport);
}

async function openMobileMenu(page) {
  const trigger = page.getByRole("button", { name: "Menu", exact: true });
  await tapTarget(trigger);
  ensure(await trigger.getAttribute("aria-expanded") === "true");
  const navigation = page.getByRole("navigation", { name: "Mobile navigation" });
  await navigation.waitFor({ state: "visible" });
  return { trigger, navigation };
}

async function ensureMenuDismissal(page) {
  const { trigger, navigation } = await openMobileMenu(page);
  await page.keyboard.press("Escape");
  await navigation.waitFor({ state: "hidden" });
  ensure(await trigger.getAttribute("aria-expanded") === "false");
  ensure(await trigger.evaluate((element) => document.activeElement === element));
}

async function inspectMenuContract(page) {
  const { navigation } = await openMobileMenu(page);
  for (const destination of smokeConfig.navigation) {
    const link = navigation.getByRole("link", {
      name: startsWithAccessibleName(destination.label)
    });
    await ensureTapTarget(link, smokeConfig.minimumTargetCssPixels);
    ensure(await link.getAttribute("href") === destination.path);
  }
  await page.keyboard.press("Escape");
  await navigation.waitFor({ state: "hidden" });
}

async function exerciseSafeNavigation(page, runtime, viewport) {
  await ensureMenuDismissal(page);
  await inspectMenuContract(page);
  const destinations = [
    ...smokeConfig.navigation.filter((item) => item.exercise && item.path !== "/dashboard"),
    ...smokeConfig.navigation.filter((item) => item.exercise && item.path === "/dashboard")
  ];

  for (const destination of destinations) {
    const { navigation } = await openMobileMenu(page);
    const link = navigation.getByRole("link", {
      name: startsWithAccessibleName(destination.label)
    });
    await tapTarget(link);
    await waitForPath(page, runtime, destination.path);
    await navigation.waitFor({ state: "hidden" });
    await ensurePageInvariants(page, viewport, destination.heading);

    if (destination.path === "/rubric") {
      await page.getByRole("button", { name: "Upload new format", exact: true }).waitFor({
        state: "visible"
      });
    }
    if (destination.path === "/audit") {
      await page.locator(".signal-audit-records ul").waitFor({ state: "visible" });
    }
    if (destination.path === "/dashboard") {
      await page.locator(".signal-desk").waitFor({ state: "visible" });
    }
  }
}

async function exerciseSyntheticReview(page, runtime, guard, viewport) {
  await page.goto(smokeConfig.syntheticReviewDeskPath, { waitUntil: "domcontentloaded" });
  await waitForPath(page, runtime, smokeConfig.syntheticReviewDeskPath);
  await page.locator(".signal-desk").waitFor({ state: "visible" });
  await ensurePageInvariants(page, viewport, "Review Desk");

  const documentPath = `/report/${runtime.checkRunId}/document`;
  const seededRecord = page.locator(`a[href="${documentPath}"]`).first();
  const flagSummaryResponse = guard.configuredFlagIsAuthorized()
    ? undefined
    : page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET"
        && url.origin === runtime.origin
        && url.pathname === `/api/check-runs/${runtime.checkRunId}/flags`
        && url.search === ""
      );
    });
  await tapTarget(seededRecord);
  await waitForPath(page, runtime, documentPath);
  if (flagSummaryResponse) {
    const response = await flagSummaryResponse;
    ensure(response.ok());
    const summaries = await response.json();
    ensure(Array.isArray(summaries));
    const configured = summaries.filter((summary) => summary?.id === runtime.flagId);
    ensure(configured.length === 1);
    guard.authorizeFlagSummary(configured[0]);
  }
  await page.getByRole("region", { name: "Full manuscript, scrollable", exact: true }).waitFor({
    state: "visible"
  });
  await ensurePageInvariants(page, viewport, "Manuscript review");

  const fullReport = page.getByRole("link", { name: "Open full readiness report", exact: true });
  await tapTarget(fullReport);
  await waitForPath(page, runtime, `/report/${runtime.checkRunId}`);
  await page.getByRole("link", { name: "Open manuscript", exact: true }).waitFor({
    state: "visible"
  });
  await ensurePageInvariants(page, viewport, "Readiness report");

  const evidence = page
    .locator(`a[href="/flags/${runtime.flagId}"]`)
    .filter({ hasText: /^Review evidence/ });
  ensure(await evidence.count() === 1);
  await ensureTapTarget(evidence, smokeConfig.minimumTargetCssPixels);
  const evidenceHref = await evidence.getAttribute("href");
  ensure(Boolean(evidenceHref));
  guard.requireAuthorizedFlagHref(evidenceHref);
  await evidence.tap();
  const evidenceUrl = new URL(evidenceHref, runtime.origin);
  await waitForPath(page, runtime, `${evidenceUrl.pathname}${evidenceUrl.search}`);
  await page.getByRole("heading", { name: "Recorded system finding", exact: true }).waitFor({
    state: "visible"
  });
  ensure(await page.locator("main h1").count() === 1);
  await ensureLayoutInvariants(page, viewport);

  const source = page.getByRole("link", {
    name: "View this location in the manuscript",
    exact: true
  });
  await tapTarget(source);
  await page.waitForURL((actual) => (
    actual.origin === runtime.origin
    && actual.pathname === documentPath
    && actual.search === `?flag=${runtime.flagId}`
  ));
  await ensurePageInvariants(page, viewport, "Manuscript review");
  await page.getByRole("region", { name: "Full manuscript, scrollable", exact: true }).waitFor({
    state: "visible"
  });
  await page.locator('article[aria-label="Reconstructed manuscript text"]').waitFor({
    state: "visible"
  });

  const reviewTab = page.getByRole("tab", { name: /^Review/ });
  await tapTarget(reviewTab);
  ensure(await reviewTab.getAttribute("aria-selected") === "true");
  const manuscriptTab = page.getByRole("tab", { name: "Manuscript", exact: true });
  await tapTarget(manuscriptTab);
  ensure(await manuscriptTab.getAttribute("aria-selected") === "true");

  const reviewDesk = page.getByRole("link", { name: "Review Desk", exact: true }).first();
  await tapTarget(reviewDesk);
  await waitForPath(page, runtime, "/dashboard?queue=needs_review");
  await ensurePageInvariants(page, viewport, "Review Desk");
}

function installFailureCounters(page, guard, runtime) {
  const counters = {
    consoleErrors: 0,
    pageErrors: 0,
    responseErrors: 0,
    allowedRequestFailures: 0,
    websockets: 0,
    downloads: 0
  };
  page.on("console", (message) => {
    if (message.type() === "error") counters.consoleErrors += 1;
  });
  page.on("pageerror", () => {
    counters.pageErrors += 1;
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === runtime.origin && response.status() >= 400) {
      counters.responseErrors += 1;
    }
  });
  page.on("requestfailed", (request) => {
    if (guard.classificationFor(request).action === "continue") {
      counters.allowedRequestFailures += 1;
    }
  });
  page.on("websocket", () => {
    counters.websockets += 1;
  });
  page.on("download", () => {
    counters.downloads += 1;
  });
  return counters;
}

function ensureNoRuntimeFailures(counters, guard) {
  ensure(guard.violations.length === 0);
  ensure(counters.consoleErrors === 0);
  ensure(counters.pageErrors === 0);
  ensure(counters.responseErrors === 0);
  ensure(counters.allowedRequestFailures === 0);
  ensure(counters.websockets === 0);
  ensure(counters.downloads === 0);
}

test("production instructor review journey under touch-enabled mobile emulation", async ({ browser }) => {
  let runtime;
  let context;
  let page;
  let guard;
  let counters;
  let authenticated = false;
  let failure;

  try {
    runtime = await runStage("CONFIG", async () => {
      const configured = readRuntimeEnvironment();
      const manifest = await buildManifest(configured.distDirectory);
      return {
        ...configured,
        staticPaths: new Set(
          manifest.records
            .filter((record) => record.relativePath !== "index.html")
            .map((record) => `/${record.relativePath}`),
        )
      };
    });
    const initialViewport = smokeConfig.viewports[0];
    context = await runStage("CONTEXT", async () => {
      const created = await browser.newContext({
        baseURL: runtime.baseURL,
        viewport: { width: initialViewport.width, height: initialViewport.height },
        screen: { width: initialViewport.width, height: initialViewport.height },
        isMobile: true,
        hasTouch: true,
        reducedMotion: "reduce",
        acceptDownloads: false,
        serviceWorkers: "block",
        ignoreHTTPSErrors: false,
        bypassCSP: false
      });
      guard = createRequestGuard({ context: created, runtime, config: smokeConfig });
      await guard.install();
      return created;
    });

    page = await context.newPage();
    page.setDefaultTimeout(smokeConfig.assertionTimeoutMs);
    page.setDefaultNavigationTimeout(smokeConfig.navigationTimeoutMs);
    counters = installFailureCounters(page, guard, runtime);

    for (const viewport of [...smokeConfig.viewports].reverse()) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await runStage("SIGNIN_LAYOUT", async () => {
        await page.goto("/signin", { waitUntil: "domcontentloaded" });
        await ensurePageInvariants(page, viewport, "Sign in to VERIDICAL");
        await ensureTapTarget(
          page.getByLabel("Email address", { exact: true }),
          smokeConfig.minimumTargetCssPixels,
        );
        await ensureTapTarget(
          page.getByLabel("Password", { exact: true }),
          smokeConfig.minimumTargetCssPixels,
        );
        await ensureTapTarget(
          page.getByRole("button", { name: "Sign in", exact: true }),
          smokeConfig.minimumTargetCssPixels,
        );
      }, viewport.label);
    }

    await runStage("AUTH", async () => {
      await ensureHeading(page, "Sign in to VERIDICAL");
      await page.getByLabel("Email address", { exact: true }).fill(runtime.email);
      await page.getByLabel("Password", { exact: true }).fill(runtime.password);
      await tapTarget(page.getByRole("button", { name: "Sign in", exact: true }));
      await waitForPath(page, runtime, "/dashboard");
      await page.locator(".signal-desk").waitFor({ state: "visible" });
      const cookies = await context.cookies(runtime.origin);
      ensure(cookies.some(isProductionSessionCookie));
      authenticated = true;
    });

    for (const viewport of smokeConfig.viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      await page.locator(".signal-desk").waitFor({ state: "visible" });
      await runStage(
        "LAYOUT",
        () => ensurePageInvariants(page, viewport, "Review Desk"),
        viewport.label,
      );
      await runStage(
        "NAVIGATION",
        () => exerciseSafeNavigation(page, runtime, viewport),
        viewport.label,
      );
      await runStage(
        "REVIEW_JOURNEY",
        () => exerciseSyntheticReview(page, runtime, guard, viewport),
        viewport.label,
      );
      await runStage(
        "RUNTIME_ERRORS",
        async () => ensureNoRuntimeFailures(counters, guard),
        viewport.label,
      );
    }

    await runStage("PRIVACY_BOUNDARY", async () => {
      ensure(guard.observed.has("SHARE_NEUTRALIZED"));
      ensure(guard.observed.has("REUSE_NEUTRALIZED"));
      ensure(guard.observed.has("RUN_PARAGRAPHS"));
      ensure(guard.observed.has("RUN_FLAG_DETAIL"));
    });
  } catch {
    failure = new Error("V076_FLOW_FAILED");
  } finally {
    if (page && context && runtime && guard) {
      try {
        await runStage("LOGOUT", async () => {
          let uiLogoutFailed = false;
          if (!failure && authenticated) {
            try {
              await openMobileMenu(page);
              const signOut = page.locator("#signal-mobile-menu-panel").getByRole("button", {
                name: "Sign out",
                exact: true
              });
              await tapTarget(signOut);
              await waitForPath(page, runtime, "/signin");
              await ensureHeading(page, "Sign in to VERIDICAL");
            } catch {
              uiLogoutFailed = true;
            }
          }

          // This idempotent call is deliberate even after a successful UI
          // sign-out: a UI failure or cookie-attribute regression must not
          // leave the production session alive until its server-side TTL.
          const loggedOut = await page.evaluate(async () => {
            try {
              const response = await fetch("/api/auth/logout", {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" }
              });
              return response.ok;
            } catch {
              return false;
            }
          });
          ensure(loggedOut);
          ensure(guard.observed.has("AUTH_LOGOUT"));
          const cookies = await context.cookies(runtime.origin);
          ensure(!cookies.some((cookie) => cookie.name === smokeConfig.sessionCookieName));
          ensure(!uiLogoutFailed);
        });
      } catch {
        failure ??= new Error("V076_LOGOUT_FAILED");
      }
    }
    if (page && counters && guard) {
      try {
        ensureNoRuntimeFailures(counters, guard);
      } catch {
        failure ??= new Error("V076_RUNTIME_FAILED");
      }
    }
    if (context) await context.close().catch(() => undefined);
  }

  if (failure) throw failure;
});
