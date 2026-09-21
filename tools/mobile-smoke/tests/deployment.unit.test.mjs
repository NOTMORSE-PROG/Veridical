import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  validateProductionOrigin,
  verifyDeploymentAssets
} from "../lib/deployment-fingerprint.mjs";

test("production origin validation rejects paths, credentials, and plaintext HTTP", () => {
  assert.equal(validateProductionOrigin("https://example.test/"), "https://example.test");
  assert.throws(() => validateProductionOrigin("https://example.test/signin"));
  assert.throws(() => validateProductionOrigin("https://user@example.test"));
  assert.throws(() => validateProductionOrigin("http://example.test"));
});

test("deployment verification compares every build file byte-for-byte", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "veridical-v076-"));
  try {
    await mkdir(path.join(root, "assets"));
    const files = new Map([
      ["/", Buffer.from("<script src='/assets/app.js'></script>")],
      ["/assets/app.js", Buffer.from("console.log('synthetic')")],
      ["/favicon.svg", Buffer.from("<svg></svg>")]
    ]);
    await writeFile(path.join(root, "index.html"), files.get("/"));
    await writeFile(path.join(root, "assets", "app.js"), files.get("/assets/app.js"));
    await writeFile(path.join(root, "favicon.svg"), files.get("/favicon.svg"));

    const fetchImpl = async (requestUrl) => {
      const url = new URL(requestUrl);
      const body = files.get(url.pathname);
      return new Response(body ?? "missing", { status: body ? 200 : 404 });
    };
    const result = await verifyDeploymentAssets({
      distDirectory: root,
      productionUrl: "https://example.test",
      timeoutMs: 1000,
      fetchImpl
    });
    assert.equal(result.fileCount, 3);
    assert.match(result.manifestHash, /^[a-f0-9]{64}$/);

    files.set("/favicon.svg", Buffer.from("changed"));
    await assert.rejects(() => verifyDeploymentAssets({
      distDirectory: root,
      productionUrl: "https://example.test",
      timeoutMs: 1000,
      fetchImpl
    }));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
