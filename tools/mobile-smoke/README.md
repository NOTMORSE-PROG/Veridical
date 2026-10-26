# Archived mobile-browser verification — retired

**Owner scope decision D-030 (2026-10-13) supersedes this campaign.**
VERIDICAL targets desktop/laptop browsers. Phone/tablet compatibility and
touch-emulation acceptance are not current product or release requirements.
Desktop keyboard access, visible focus, contrast, native browser zoom and
resizable-window usability remain required; see [the current scope override](../../FEATURES.md).

This package retains historical policy code and offline unit controls. It does
not establish current desktop acceptance, physical-device behavior or human UAT.
The former production workflow is preserved in
[`archive/production-mobile.yml`](archive/production-mobile.yml), outside
GitHub's `.github/workflows/` discovery directory. It is not scheduled or manually
dispatchable as an active workflow.

## Supported offline checks

From this directory:

```sh
npm test
```

These tests exercise archived request-policy, deployment-fingerprint and
sanitized-reporting logic using local fixtures and mocked transports, plus the
retirement guards. They do not authenticate, start a browser or contact
production. They are archival controls, not a mobile acceptance gate.

## Retired execution paths

`npm run test:production` and `npm run verify:deployment` reject execution with a
fixed retirement error. The Playwright configuration rejects direct loading
before reading environment/configuration or collecting tests. The historical
production spec, warmup and deployment-verification CLI entry points also reject
execution; an alternate Playwright configuration cannot reactivate the spec.
The summary CLI cannot append a new successful production result.

No credential, environment variable, flag or opt-in bypass enables this retired
campaign. Do not supply production credentials or use it as a current gate.
Original executable source and operating notes are preserved as non-executable
text under `archive/`; they describe historical procedures, not current setup
instructions. Policy/helper exports remain for offline unit controls; importing
them into a new caller does not authorize a production run.

Current acceptance should exercise the actual desktop/laptop application with
keyboard/focus, native browser zoom and resized windows. It must report local,
deployed, simulated and human-study evidence separately.
