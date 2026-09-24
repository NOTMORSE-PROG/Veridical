import { readFileSync } from "node:fs";

export function validateProductionOrigin(value) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error("invalid production origin configuration");
  }
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("invalid production origin configuration");
  }
  if (
    url.protocol !== "https:"
    || url.username
    || url.password
    || (url.pathname !== "/" && url.pathname !== "")
    || url.search
    || url.hash
  ) {
    throw new Error("invalid production origin configuration");
  }
  return url.origin;
}

export function loadSmokeConfig() {
  const config = JSON.parse(
    readFileSync(new URL("../smoke.config.json", import.meta.url), "utf8"),
  );
  config.productionWebOrigin = validateProductionOrigin(config.productionWebOrigin);
  config.productionApiOrigin = validateProductionOrigin(config.productionApiOrigin);
  if (!Array.isArray(config.viewports) || config.viewports.length !== 2) {
    throw new Error("invalid viewport configuration");
  }
  if (!Array.isArray(config.navigation) || config.navigation.length === 0) {
    throw new Error("invalid navigation configuration");
  }
  if (typeof config.sessionCookieName !== "string" || config.sessionCookieName.length === 0) {
    throw new Error("invalid session cookie configuration");
  }
  if (
    !Array.isArray(config.safeFindingKinds)
    || config.safeFindingKinds.length === 0
    || config.safeFindingKinds.some((kind) => typeof kind !== "string" || kind.length === 0)
  ) {
    throw new Error("invalid safe finding-kind configuration");
  }
  return config;
}

export function readRuntimeEnvironment(environment = process.env) {
  const config = loadSmokeConfig();
  const email = environment.PROD_SMOKE_EMAIL;
  const password = environment.PROD_SMOKE_PASSWORD;
  const rawCheckRunId = environment.PROD_SMOKE_CHECK_RUN_ID;
  const rawFlagId = environment.PROD_SMOKE_FLAG_ID;
  const rubricFamilyId = environment.PROD_SMOKE_RUBRIC_FAMILY_ID;
  const distDirectory = environment.PROD_SMOKE_DIST_DIR;
  if (
    !email
    || !password
    || !rawCheckRunId
    || !rawFlagId
    || !rubricFamilyId
    || !distDirectory
  ) {
    throw new Error("required production smoke configuration is missing");
  }

  if (!/^\d+$/.test(rawCheckRunId)) {
    throw new Error("synthetic check-run identifier must be a positive integer");
  }
  const checkRunId = Number(rawCheckRunId);
  if (!Number.isSafeInteger(checkRunId) || checkRunId <= 0) {
    throw new Error("synthetic check-run identifier must be a positive integer");
  }
  if (!/^\d+$/.test(rawFlagId)) {
    throw new Error("synthetic non-originality flag identifier must be a positive integer");
  }
  const flagId = Number(rawFlagId);
  if (!Number.isSafeInteger(flagId) || flagId <= 0) {
    throw new Error("synthetic non-originality flag identifier must be a positive integer");
  }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    rubricFamilyId,
  )) {
    throw new Error("synthetic rubric-family identifier must be a UUID");
  }

  return {
    baseURL: config.productionWebOrigin,
    origin: config.productionWebOrigin,
    email,
    password,
    checkRunId,
    flagId,
    rubricFamilyId: rubricFamilyId.toLowerCase(),
    distDirectory
  };
}
