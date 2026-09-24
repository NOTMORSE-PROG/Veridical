import { loadSmokeConfig } from "./lib/runtime.mjs";

async function main() {
  try {
    const config = loadSmokeConfig();
    const response = await fetch(new URL("/health", config.productionApiOrigin), {
      redirect: "manual",
      headers: { "cache-control": "no-cache" },
      signal: AbortSignal.timeout(config.healthFetchTimeoutMs)
    });
    if (response.status !== 200) throw new Error("health response was not successful");
    const body = await response.json();
    if (body?.status !== "ok" || body?.db !== "ok" || body?.env !== "prod") {
      throw new Error("health response was not ready");
    }
    process.stdout.write("V076 WARMUP PASS\n");
    return 0;
  } catch {
    process.stderr.write("V076 WARMUP FAILED\n");
    return 1;
  }
}

process.exitCode = await main();
