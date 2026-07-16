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

console.log(
  `check-css-tokens: OK — all ${expected.size} token classes present in built CSS`,
);
