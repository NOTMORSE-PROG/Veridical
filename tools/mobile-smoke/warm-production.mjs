import { loadSmokeConfig } from "./lib/runtime.mjs";

function healthUrl(rawUrl) {
  const url = new URL(rawUrl);
  if (
    url.protocol !== "https:"
    || url.username
    || url.password
    || (url.pathname !== "/" && url.pathname !== "")
    || url.search
    || url.hash
  ) {
    throw new Error("production API URL must be a credential-free HTTPS origin");
  }
  return new URL("/health", url.origin);
}

async function main(environment = process.env) {
  try {
    if (!environment.PROD_API_URL) throw new Error("production API URL is missing");
    const config = loadSmokeConfig();
    const response = await fetch(healthUrl(environment.PROD_API_URL), {
      redirect: "manual",
      headers: { "cache-control": "no-cache" },
      signal: AbortSignal.timeout(config.healthFetchTimeoutMs)
    });
    if (response.status !== 200) throw new Error("health response was not successful");
    const body = await response.json();
    if (body?.status !== "ok") throw new Error("health response was not ready");
    process.stdout.write("V076 WARMUP PASS\n");
    return 0;
  } catch {
    process.stderr.write("V076 WARMUP FAILED\n");
    return 1;
  }
}

process.exitCode = await main();
