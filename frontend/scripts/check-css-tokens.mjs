// Purge guard (V-002 edge case): Tailwind only emits classes its scanner
// finds written out whole, so a dynamically composed variant class would
// silently vanish from the build. This script collects every token-utility
// class referenced in component source and asserts each one survived into
// the built CSS. Run after `vite build`; CI fails if any class is missing.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_ROOT = fileURLToPath(new URL("..", import.meta.url));
const SRC_DIR = join(FRONTEND_ROOT, "src");
const DIST_ASSETS_DIR = join(FRONTEND_ROOT, "dist", "assets");
const SIGNAL_CSS_FILE = join(SRC_DIR, "ui", "signal.css");
const SIGNAL_ROUTE_FILES = new Set([
  "pages/Landing.tsx",
  "pages/SignIn.tsx",
  "pages/Dashboard.tsx",
  "check/Progress.tsx",
  "rubric/Manage.tsx",
  "rubric/SignalReviewCriteria.tsx",
  "report/SignalReport.tsx",
  "report/SignalReviewSections.tsx",
  "report/SignalDecisionPanel.tsx",
  "report/SignalShareDialog.tsx",
  "report/AdviserView.tsx",
  "flags/FlagDetail.tsx",
  "document/SignalDocumentViewer.tsx",
  "library/SignalLibrary.tsx",
  "library/SignalLibraryDetail.tsx",
  "library/SignalLibraryCompare.tsx",
  "shell/SignalShell.tsx",
]);
const SIGNAL_SELF_BOUNDARY_FILES = new Set(["pages/Landing.tsx", "pages/SignIn.tsx"]);
const SIGNAL_PAGE_FLOW_FILES = new Set([
  "pages/Dashboard.tsx",
  "check/Progress.tsx",
  "rubric/Manage.tsx",
  "rubric/SignalReviewCriteria.tsx",
  "report/SignalReport.tsx",
  "flags/FlagDetail.tsx",
  "document/SignalDocumentViewer.tsx",
  "library/SignalLibrary.tsx",
  "library/SignalLibraryDetail.tsx",
  "library/SignalLibraryCompare.tsx",
  "settings/Settings.tsx",
  "audit/AuditLog.tsx",
]);

// Utilities bound to VERIDICAL color tokens (see src/tokens.css @theme).
const TOKEN_CLASS_RE =
  /(?:bg|text|border)-(?:status|severity|step)-[a-z0-9]+(?:-[a-z0-9]+)*/g;

const sourceFiles = readdirSync(SRC_DIR, { recursive: true })
  .map(String)
  .filter((name) => /\.(?:ts|tsx)$/.test(name) && !/\.test\./.test(name));

const expected = new Set();
for (const name of sourceFiles) {
  const text = readFileSync(join(SRC_DIR, name), "utf8");
  for (const match of text.matchAll(TOKEN_CLASS_RE)) {
    expected.add(match[0]);
  }
}

if (expected.size === 0) {
  console.error("check-css-tokens: found no token classes in src — regex or layout broke");
  process.exit(1);
}

const cssFiles = readdirSync(DIST_ASSETS_DIR)
  .filter((name) => name.endsWith(".css"))
  .map((name) => readFileSync(join(DIST_ASSETS_DIR, name), "utf8"));

if (cssFiles.length === 0) {
  console.error("check-css-tokens: no built CSS found — run `npm run build` first");
  process.exit(1);
}

const builtCss = cssFiles.join("\n");
const missing = [...expected].filter((cls) => !builtCss.includes(cls)).sort();

if (missing.length > 0) {
  console.error(
    `check-css-tokens: ${missing.length} token class(es) referenced in src but absent from built CSS (purged?):`,
  );
  for (const cls of missing) console.error(`  - ${cls}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Guards added 2026-08-16. Each exists because the thing it checks already
// went wrong once; the purge check above could not see any of them.
// ---------------------------------------------------------------------------
const failures = [];

// BUG-101: three motion tokens were declared in tokens.css, documented in
// DESIGN.md as a live system, and silently tree-shaken by Tailwind because
// nothing referenced them. Measured on the running app, the variable
// resolved to "". This single assertion would have caught it that day.
const REQUIRED_IN_BUILD = [
  "--motion-duration-base",
  "--motion-ease-standard",
  "--z-modal",
  "--z-skip-link",
  "--elevation-overlay",
  "--color-scrim",
  "--color-signal-brand",
  "--font-signal-display",
  "--font-signal-interface",
  "--signal-space-4",
  "--signal-radius-control",
  "--signal-shadow-modal",
  "--signal-motion-base",
  "--signal-z-modal",
  "--signal-border-emphasis",
  "--signal-rhythm-attached",
  "--signal-rhythm-control",
  "--signal-rhythm-copy",
  "--signal-rhythm-group",
  "--signal-rhythm-related",
  "--signal-rhythm-related-compact",
  "--signal-rhythm-section",
  "--signal-rhythm-section-compact",
  "--signal-rhythm-dialog",
];
for (const token of REQUIRED_IN_BUILD) {
  if (!builtCss.includes(token)) {
    failures.push(`${token} is declared but absent from the built CSS (tree-shaken? needs @theme static)`);
  }
}

const REQUIRED_RHYTHM_SELECTORS = [
  ".signal-page-flow",
  ".signal-section-flow",
  ".signal-group-flow",
  ".signal-copy-flow",
  ".signal-attached-flow",
  ".signal-control-cluster",
];
for (const selector of REQUIRED_RHYTHM_SELECTORS) {
  if (!builtCss.includes(selector)) {
    failures.push(`built CSS does not emit required rhythm selector ${selector}`);
  }
}

// BUG-196: presence is not behavior. Pin the semantic aliases to the approved
// primitive scale and the flow selectors to their semantic roles, including
// the established compact media block. A selector that survives the build but
// silently regresses to `gap: 0` must fail this guard.
const tokenSource = readFileSync(join(SRC_DIR, "tokens.css"), "utf8");
const rhythmSource = readFileSync(SIGNAL_CSS_FILE, "utf8");
const REQUIRED_RHYTHM_VALUES = new Map([
  ["--signal-rhythm-attached", "var(--signal-space-2)"],
  ["--signal-rhythm-control", "var(--signal-space-3)"],
  ["--signal-rhythm-copy", "var(--signal-space-4)"],
  ["--signal-rhythm-group", "var(--signal-space-5)"],
  ["--signal-rhythm-related", "var(--signal-space-6)"],
  ["--signal-rhythm-related-compact", "var(--signal-space-5)"],
  ["--signal-rhythm-section", "var(--signal-space-10)"],
  ["--signal-rhythm-section-compact", "var(--signal-space-8)"],
  ["--signal-rhythm-dialog", "var(--signal-space-5)"],
]);

function customPropertyValues(source, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [...source.matchAll(new RegExp(`${escaped}\\s*:\\s*([^;]+);`, "g"))]
    .map((match) => match[1].trim());
}

function cssBlockAfter(source, marker) {
  const markerIndex = source.indexOf(marker);
  const openIndex = markerIndex < 0 ? -1 : source.indexOf("{", markerIndex + marker.length);
  if (openIndex < 0) return "";
  let depth = 0;
  for (let index = openIndex; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(openIndex + 1, index);
    }
  }
  return "";
}

function cssBlocksAfterAll(source, marker) {
  const blocks = [];
  let fromIndex = 0;
  let markerIndex = source.indexOf(marker, fromIndex);
  while (markerIndex >= 0) {
    blocks.push(cssBlockAfter(source.slice(markerIndex), marker));
    fromIndex = markerIndex + marker.length;
    markerIndex = source.indexOf(marker, fromIndex);
  }
  return blocks;
}

function rulePropertyValues(source, selector, property) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const escapedProperty = property.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rules = source.matchAll(
    new RegExp(`(?:^|[{},])\\s*${escapedSelector}\\s*\\{([^{}]*)\\}`, "gm"),
  );
  const values = [];
  for (const rule of rules) {
    const value = rule[1].match(new RegExp(`${escapedProperty}\\s*:\\s*([^;]+);`))?.[1].trim();
    if (value !== undefined) values.push(value);
  }
  return values;
}

function validateRhythmContract(candidateTokens, candidateRhythm) {
  const violations = [];

  for (const [name, expectedValue] of REQUIRED_RHYTHM_VALUES) {
    const actualValues = customPropertyValues(candidateTokens, name);
    if (actualValues.length !== 1 || actualValues[0] !== expectedValue) {
      violations.push(
        `${name} must have exactly one ${expectedValue} declaration; found ${actualValues.join(", ") || "missing"}`,
      );
    }
  }

  const requiredFlowGaps = new Map([
    [".signal-page-flow", ["var(--signal-rhythm-section)", "var(--signal-rhythm-section-compact)"]],
    [".signal-section-flow", ["var(--signal-rhythm-related)", "var(--signal-rhythm-related-compact)"]],
    [".signal-group-flow", ["var(--signal-rhythm-group)"]],
    [".signal-copy-flow", ["var(--signal-rhythm-copy)"]],
    [".signal-attached-flow", ["var(--signal-rhythm-attached)"]],
    [".signal-control-cluster", ["var(--signal-rhythm-control)"]],
  ]);
  for (const [selector, expectedValues] of requiredFlowGaps) {
    const actualValues = rulePropertyValues(candidateRhythm, selector, "gap");
    if (
      actualValues.length !== expectedValues.length
      || actualValues.some((value, index) => value !== expectedValues[index])
    ) {
      violations.push(
        `${selector} gap declarations must be ${expectedValues.join(" then ")}; found ${actualValues.join(" then ") || "missing"}`,
      );
    }
  }

  const settingsNavOffsets = rulePropertyValues(
    candidateRhythm,
    ".signal-settings-nav",
    "inset-block-start",
  );
  const expectedSettingsNavOffsets = [
    "var(--signal-workspace-header-height)",
    "var(--signal-workspace-header-compact-height)",
  ];
  if (
    settingsNavOffsets.length !== expectedSettingsNavOffsets.length
    || settingsNavOffsets.some((value, index) => value !== expectedSettingsNavOffsets[index])
  ) {
    violations.push(
      `.signal-settings-nav offsets must clear the desktop then compact workspace headers; found ${settingsNavOffsets.join(" then ") || "missing"}`,
    );
  }

  const workspaceHorizontalOverflow = rulePropertyValues(
    candidateRhythm,
    ".signal-workspace-main",
    "overflow-x",
  );
  if (
    workspaceHorizontalOverflow.length !== 1
    || workspaceHorizontalOverflow[0] !== "clip"
  ) {
    violations.push(
      `.signal-workspace-main must use overflow-x: clip so compact sticky descendants follow the viewport; found ${workspaceHorizontalOverflow.join(", ") || "missing"}`,
    );
  }

  const compactRhythmSource = cssBlocksAfterAll(candidateRhythm, "@media (max-width: 767px)")
    .find((block) => block.includes(".signal-page-flow")) ?? "";
  for (const [selector, expectedValue] of [
    [".signal-page-flow", "var(--signal-rhythm-section-compact)"],
    [".signal-section-flow", "var(--signal-rhythm-related-compact)"],
  ]) {
    const actualValues = rulePropertyValues(compactRhythmSource, selector, "gap");
    if (actualValues.length !== 1 || actualValues[0] !== expectedValue) {
      violations.push(
        `compact ${selector} gap must have exactly one ${expectedValue} declaration; found ${actualValues.join(", ") || "missing"}`,
      );
    }
  }

  return violations;
}

failures.push(...validateRhythmContract(tokenSource, rhythmSource));

// BUG-085: raw z-index values. Eleven layering sites now carry a --z-* token
// (9 class-based + 2 inline in CoachMark); before the migration the skip link
// and the modal backdrop were BOTH z-50, a genuine tie broken only by paint
// order. Use the scale.
//
// GUARD GAP, named by ux-critic 2026-08-16 and not yet closed: this catches a
// raw z-index but NOT a component using --z-raised without `isolation:
// isolate` on its own root, which DESIGN.md §1.9 requires. Stepper.tsx shipped
// exactly that violation on the same change that wrote the rule.
const RAW_Z = /(?:^|[\s"'`])z-(?:\[)?\d+\]?|zIndex:\s*\d/;
// D4: 2 of 12 animation sites shipped with no reduced-motion guard.
//
// KNOWN LIMITATION, found while negative-testing this guard: it works at line
// granularity, so a line that carries one correctly-guarded animation masks an
// unguarded one on the same line. Planting `animate-pulse` onto
// StatusPill.tsx:47 (which already has motion-safe:animate-spin) is NOT caught.
// Both halves are otherwise verified to fail on a planted violation and pass
// when it is removed. Tightening this needs a real JSX class parser; the line
// check catches the realistic case (a new element with a new animation).
const ANIM = /(?:^|[\s"'`])animate-[a-z[]/;

for (const name of sourceFiles) {
  const lines = readFileSync(join(SRC_DIR, name), "utf8").split(/\r?\n/);
  for (const [i, line] of lines.entries()) {
    // Skip comment lines. A comment that mentions a class is prose, not a
    // usage — AppShell.tsx:123 documents a spinner "minus the animate-spin
    // class" and tripped this guard on its first run. Same lesson as
    // tools/check_dashes.py: a naive line grep over source is mostly noise.
    const code = line.trim();
    if (code.startsWith("//") || code.startsWith("*") || code.startsWith("/*")) continue;
    if (RAW_Z.test(line)) {
      failures.push(`${name}:${i + 1} raw z-index — use a --z-* token (DESIGN.md §1.9)`);
    }
    if (
      ANIM.test(line) &&
      !line.includes("motion-safe:") &&
      !line.includes("motion-reduce:")
    ) {
      failures.push(
        `${name}:${i + 1} animate-* with no motion-safe:/motion-reduce: guard (DESIGN.md §1.10)`,
      );
    }
  }
}

// V-073 migration boundary. The retired UI remains only while routes are
// replaced. Signal routes/components get the complete ground-rule-7 gate now,
// so the legacy baseline cannot hide a new violation or a hybrid route.
const RAW_COLOR = /#[0-9a-f]{3,8}\b|\b(?:rgb|hsl|oklch|lab|lch|color)\s*\(/i;
const ARBITRARY_VISUAL_CLASS = /-\[[^\]]+\]/;
const INLINE_STYLE = /\bstyle\s*=\s*\{\{/;
const LEGACY_VISUAL_CLASS =
  /(?:bg|text|border)-(?:tip|accent|action|panel|canvas|ink|neutral|danger|status|severity|step)-/;
const RAW_CSS_SIZE = /-?(?:\d+\.?\d*|\.\d+)(?:px|rem|em|ch|vh|vw)\b/i;
const CSS_Z_DECLARATION = /\bz-index\s*:\s*([^;]+)/i;
const TRANSITION_ALL = /\btransition(?:-property)?\s*:\s*all\b/i;
const ANIMATION_DECLARATION = /\banimation(?:-name)?\s*:/i;
const TOKENIZED_ANIMATION = /var\(--signal-motion-/;
const FONT_FAMILY_DECLARATION = /\bfont-family\s*:\s*([^;]+)/i;
// CSS custom properties cannot be used in media-query conditions. Keep the
// small-screen and navigation-collapse thresholds as the only sanctioned
// literal breakpoints, then reject every one-off breakpoint through this gate.
const MEDIA_BREAKPOINT = /^@media \(max-width: (?:767|1023)px\) \{$/;

function isCommentLine(code) {
  return code.startsWith("//") || code.startsWith("*") || code.startsWith("/*");
}

function scanSignalTsx(name, source, target) {
  for (const [index, line] of source.split(/\r?\n/).entries()) {
    const code = line.trim();
    if (!code || isCommentLine(code)) continue;
    if (RAW_COLOR.test(line)) {
      target.push(`${name}:${index + 1} raw color -- declare it in tokens.css`);
    }
    if (ARBITRARY_VISUAL_CLASS.test(line)) {
      target.push(`${name}:${index + 1} arbitrary visual utility -- use a named Signal token`);
    }
    if (INLINE_STYLE.test(line)) {
      target.push(`${name}:${index + 1} inline style -- use a Signal class and token`);
    }
    if (LEGACY_VISUAL_CLASS.test(line)) {
      target.push(`${name}:${index + 1} legacy visual class inside a Signal route/component`);
    }
  }
}

function scanSignalCss(source, target) {
  let braceDepth = 0;
  let reducedMotionDepth = null;

  for (const [index, line] of source.split(/\r?\n/).entries()) {
    const code = line.trim();
    if (!code || isCommentLine(code)) continue;

    const startsReducedMotion = code.startsWith(
      "@media (prefers-reduced-motion: no-preference)",
    );
    const insideReducedMotion = reducedMotionDepth !== null;

    if (RAW_COLOR.test(line)) {
      target.push(`ui/signal.css:${index + 1} raw color -- declare it in tokens.css`);
    }
    if (RAW_CSS_SIZE.test(line) && !MEDIA_BREAKPOINT.test(code)) {
      target.push(`ui/signal.css:${index + 1} ad hoc size -- declare a named Signal token`);
    }
    const zDeclaration = line.match(CSS_Z_DECLARATION);
    if (zDeclaration && !zDeclaration[1].trim().startsWith("var(")) {
      target.push(`ui/signal.css:${index + 1} raw z-index -- use the Signal z-order scale`);
    }
    if (TRANSITION_ALL.test(line)) {
      target.push(`ui/signal.css:${index + 1} transition all -- name the changing properties`);
    }
    if (
      ANIMATION_DECLARATION.test(line) &&
      (!insideReducedMotion || !TOKENIZED_ANIMATION.test(line))
    ) {
      target.push(
        `ui/signal.css:${index + 1} animation must use a motion token inside the reduced-motion gate`,
      );
    }
    const fontDeclaration = line.match(FONT_FAMILY_DECLARATION);
    if (fontDeclaration && !fontDeclaration[1].trim().startsWith("var(")) {
      target.push(`ui/signal.css:${index + 1} raw font family -- use a Signal font token`);
    }

    const opens = (line.match(/\{/g) ?? []).length;
    const closes = (line.match(/\}/g) ?? []).length;
    braceDepth += opens - closes;
    if (startsReducedMotion) reducedMotionDepth = braceDepth;
    if (reducedMotionDepth !== null && braceDepth < reducedMotionDepth) {
      reducedMotionDepth = null;
    }
  }
}

for (const name of sourceFiles) {
  const normalized = name.replaceAll("\\", "/");
  if (!normalized.startsWith("ui/") && !SIGNAL_ROUTE_FILES.has(normalized)) continue;
  const source = readFileSync(join(SRC_DIR, name), "utf8");
  scanSignalTsx(normalized, source, failures);
  if (
    SIGNAL_SELF_BOUNDARY_FILES.has(normalized) &&
    (!source.includes('data-design="signal"') || !source.includes("signal-theme"))
  ) {
    failures.push(`${normalized} does not declare the Signal route boundary`);
  }
}

for (const normalized of SIGNAL_PAGE_FLOW_FILES) {
  const source = readFileSync(join(SRC_DIR, normalized), "utf8");
  const routeRoots = source.match(/className="signal-route(?:\s[^"]*)?"/g) ?? [];
  const missingFlow = routeRoots.filter((root) => !root.includes("signal-page-flow"));
  if (routeRoots.length === 0 || missingFlow.length > 0) {
    failures.push(`${normalized} has ${missingFlow.length} route root(s) without signal-page-flow`);
  }
}

const adviserSource = readFileSync(join(SRC_DIR, "report", "AdviserView.tsx"), "utf8");
const sharedRouteRoots = adviserSource.match(/className="signal-shared-route[^"]*"/g) ?? [];
if (
  sharedRouteRoots.length === 0
  || sharedRouteRoots.some((root) => !root.includes("signal-page-flow"))
) {
  failures.push("report/AdviserView.tsx has a shared route root without signal-page-flow");
}

const signalCss = readFileSync(SIGNAL_CSS_FILE, "utf8");
scanSignalCss(signalCss, failures);

// Permanent negative controls. Each planted defect is passed through the same
// scanner as production source. A regex rewrite cannot keep printing OK after
// it loses the ability to see the defect it claims to reject.
const negativeControls = [
  ["raw color", () => {
    const found = [];
    scanSignalTsx("negative.tsx", 'className="bg-[#ff0000]"', found);
    return found;
  }],
  ["arbitrary value", () => {
    const found = [];
    scanSignalTsx("negative.tsx", 'className="p-[17px]"', found);
    return found;
  }],
  ["inline style", () => {
    const found = [];
    scanSignalTsx("negative.tsx", "style={{ color: token }}", found);
    return found;
  }],
  ["legacy hybrid", () => {
    const found = [];
    scanSignalTsx("negative.tsx", 'className="bg-tip-yellow"', found);
    return found;
  }],
  ["raw CSS size", () => {
    const found = [];
    scanSignalCss(".x { padding: 17px; }", found);
    return found;
  }],
  ["unapproved CSS breakpoint", () => {
    const found = [];
    scanSignalCss("@media (max-width: 999px) { .x { display: block; } }", found);
    return found;
  }],
  ["raw CSS z-index", () => {
    const found = [];
    scanSignalCss(".x { z-index: 999; }", found);
    return found;
  }],
  ["transition all", () => {
    const found = [];
    scanSignalCss(".x { transition: all 1s; }", found);
    return found;
  }],
  ["unguarded animation", () => {
    const found = [];
    scanSignalCss(".x { animation: pulse 1s infinite; }", found);
    return found;
  }],
  ["later rhythm-token override", () => validateRhythmContract(
    `${tokenSource}\n:root { --signal-rhythm-attached: 0; }`,
    rhythmSource,
  )],
  ["later flow-gap override", () => validateRhythmContract(
    tokenSource,
    `${rhythmSource}\n.signal-page-flow { gap: 0; }`,
  )],
  ["settings navigation header collision", () => validateRhythmContract(
    tokenSource,
    `${rhythmSource}\n.signal-settings-nav { inset-block-start: 0; }`,
  )],
  ["mobile sticky overflow container", () => validateRhythmContract(
    tokenSource,
    `${rhythmSource}\n.signal-workspace-main { overflow-x: hidden; }`,
  )],
];

for (const [label, run] of negativeControls) {
  if (run().length === 0) failures.push(`guard negative control did not reject ${label}`);
}

if (failures.length > 0) {
  console.error(`check-css-tokens: ${failures.length} design-system violation(s):`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}

console.log(
  `check-css-tokens: OK -- ${expected.size} token classes present, ${REQUIRED_IN_BUILD.length} required tokens emitted, Signal source tokenized, ${negativeControls.length} negative controls rejected`,
);
