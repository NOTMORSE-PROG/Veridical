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
];
for (const token of REQUIRED_IN_BUILD) {
  if (!builtCss.includes(token)) {
    failures.push(`${token} is declared but absent from the built CSS (tree-shaken? needs @theme static)`);
  }
}

// BUG-085: raw z-index values. Seven lived in src, two of them inline, and
// the skip link tied with the modal overlay. Use the --z-* scale.
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

if (failures.length > 0) {
  console.error(`check-css-tokens: ${failures.length} design-system violation(s):`);
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}

console.log(
  `check-css-tokens: OK — ${expected.size} token classes present, ${REQUIRED_IN_BUILD.length} required tokens emitted, no raw z-index, all animations motion-guarded`,
);
