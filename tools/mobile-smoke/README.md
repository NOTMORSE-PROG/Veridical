# Production mobile-browser verification

This package checks one bounded instructor review journey against the deployed
VERIDICAL frontend in touch-enabled Chromium at `390x844` and `320x480`.
It is browser emulation, not evidence from a physical phone, Safari, Firefox,
iOS, Android, a mobile network, or mobile assistive technology.

## Required configuration

Supply these values only at runtime:

| Name | Purpose |
|---|---|
| `PROD_WEB_URL` | Credential-free HTTPS production origin |
| `PROD_SMOKE_EMAIL` | Dedicated synthetic instructor account |
| `PROD_SMOKE_PASSWORD` | Random password for that account |
| `PROD_SMOKE_CHECK_RUN_ID` | Purpose-seeded synthetic DOCX check run |
| `PROD_SMOKE_FLAG_ID` | One non-originality finding owned by that run |
| `PROD_SMOKE_RUBRIC_FAMILY_ID` | Exact synthetic required-format family UUID |
| `PROD_SMOKE_DIST_DIR` | Exact checked-out frontend build directory |
| `MOBILE_SMOKE_OUTPUT_DIR` | Disposable scratch directory outside the repository |

The account must contain only publication-safe synthetic data, have onboarding
already dismissed, and have no personal Gemini key. The configured finding is
still denied until the run's own flag summary proves that its ID matches and
its kind belongs to `safeFindingKinds` in `smoke.config.json`.
Static requests are authorized only when their paths occur in that local build
manifest; the required-format versions request is authorized only for the
configured synthetic family UUID.

Never point this job at a normal instructor account. The Library, shared-report,
raw-download, export, settings, upload, grading, decision, resolution,
onboarding, and unknown routes are deliberately blocked. Eager share-token and
cross-corpus reuse reads are fulfilled locally with inert schema-valid values
and are excluded from the test claim. Any WebSocket attempt is closed locally
and fails the run.

## Local policy checks

```sh
npm ci --ignore-scripts
npm test
npm run test:production -- --list
```

The authenticated production run is:

```sh
npm run test:production
```

Do not enable traces, screenshots, video, HTML/blob/JSON/JUnit reports, HAR,
storage state, or artifact uploads. The custom reporter intentionally emits
only fixed stage and outcome codes.

## Deployment-byte check

Build `frontend/dist` from the exact checked-out revision with
`VITE_API_BASE_URL` unset, then run from the repository root:

```sh
node tools/mobile-smoke/verify-deployment.mjs frontend/dist
```

This compares every generated file byte-for-byte with the production edge and
prints only the source revision, file count, and aggregate manifest digest. It
does not prove a Vercel control-plane deployment record, the backend source
revision, or every CDN edge. The workflow runs it immediately before and after
the authenticated journey.
