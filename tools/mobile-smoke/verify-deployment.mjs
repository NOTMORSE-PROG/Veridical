import { pathToFileURL } from "node:url";
import { verifyDeploymentAssets } from "./lib/deployment-fingerprint.mjs";
import { loadSmokeConfig } from "./lib/runtime.mjs";

export async function main(arguments_ = process.argv.slice(2), environment = process.env) {
  const [distDirectory] = arguments_;
  if (!distDirectory || !environment.PROD_WEB_URL) {
    process.stderr.write("V076 DEPLOYMENT FAILED\n");
    return 1;
  }
  try {
    const config = loadSmokeConfig();
    const result = await verifyDeploymentAssets({
      distDirectory,
      productionUrl: environment.PROD_WEB_URL,
      timeoutMs: config.deploymentFetchTimeoutMs
    });
    const source = /^[a-f0-9]{40}$/.test(environment.GITHUB_SHA ?? "")
      ? environment.GITHUB_SHA
      : "local";
    process.stdout.write(
      `V076 DEPLOYMENT BYTE_EQUIVALENT source=${source} files=${result.fileCount} manifest=${result.manifestHash}\n`,
    );
    return 0;
  } catch {
    process.stderr.write("V076 DEPLOYMENT FAILED\n");
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
