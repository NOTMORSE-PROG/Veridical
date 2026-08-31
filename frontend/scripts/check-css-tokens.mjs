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
];
for (const token of REQUIRED_IN_BUILD) {
  if (!builtCss.includes(token)) {
    failures.push(`${token} is declared but absent from the built CSS (tree-shaken? needs @theme static)`);
  }
}

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
