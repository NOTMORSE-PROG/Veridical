import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

export function validateProductionOrigin(rawUrl) {
  const url = new URL(rawUrl);
  if (
    url.protocol !== "https:"
    || url.username
    || url.password
    || (url.pathname !== "/" && url.pathname !== "")
    || url.search
    || url.hash
  ) {
    throw new Error("production URL must be a credential-free HTTPS origin");
  }
  return url.origin;
}

function digest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function listFiles(directory, root = directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await listFiles(absolute, root));
    } else if (entry.isFile()) {
      files.push(path.relative(root, absolute).split(path.sep).join("/"));
    } else {
      throw new Error("build output contained an unsupported filesystem entry");
    }
  }
  return files.sort();
}

async function fetchSuccessful(fetchImpl, url, timeoutMs) {
  const response = await fetchImpl(url, {
    redirect: "manual",
    cache: "no-store",
    headers: { "cache-control": "no-cache" },
    signal: AbortSignal.timeout(timeoutMs)
  });
  if (response.status !== 200) throw new Error("production resource was not successful");
  return response;
}

export async function buildManifest(distDirectory) {
  const resolvedDist = path.resolve(distDirectory);
  const files = await listFiles(resolvedDist);
  if (!files.includes("index.html") || files.length < 2) {
    throw new Error("frontend build output is incomplete");
  }
  const records = [];
  for (const relativePath of files) {
    const bytes = await readFile(path.join(resolvedDist, ...relativePath.split("/")));
    records.push({ relativePath, bytes, digest: digest(bytes) });
  }
  const aggregate = records
    .map((record) => `${record.relativePath}\0${record.bytes.length}\0${record.digest}`)
    .join("\n");
  return {
    records,
    manifestHash: digest(Buffer.from(aggregate, "utf8"))
  };
}

export async function verifyDeploymentAssets({
  distDirectory,
  productionUrl,
  timeoutMs,
  fetchImpl = fetch
}) {
  const origin = validateProductionOrigin(productionUrl);
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new Error("deployment fetch timeout is invalid");
  }
  const manifest = await buildManifest(distDirectory);
  const cacheKey = manifest.manifestHash.slice(0, 16);

  async function compareRecord(record) {
    const relativeUrl = record.relativePath === "index.html" ? "/" : `/${record.relativePath}`;
    const remoteUrl = new URL(relativeUrl, origin);
    remoteUrl.searchParams.set("v076_probe", cacheKey);
    const remoteResponse = await fetchSuccessful(fetchImpl, remoteUrl, timeoutMs);
    const remoteBytes = Buffer.from(await remoteResponse.arrayBuffer());
    if (record.bytes.length !== remoteBytes.length || record.digest !== digest(remoteBytes)) {
      throw new Error("production bytes differ from the checked-out build");
    }
  }

  const indexRecord = manifest.records.find((record) => record.relativePath === "index.html");
  if (!indexRecord) throw new Error("frontend build output has no index");
  await compareRecord(indexRecord);
  for (const record of manifest.records) {
    if (record !== indexRecord) await compareRecord(record);
  }
  await compareRecord(indexRecord);

  return { fileCount: manifest.records.length, manifestHash: manifest.manifestHash };
}
