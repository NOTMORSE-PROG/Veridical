#!/usr/bin/env bash
# Deploy the native-compute helper, or show what a deploy would do.
#
#   bash compute-helper/deploy.sh --dry-run   # decide, stage, run both gates; never deploys
#   bash compute-helper/deploy.sh             # the same, then deploy, verify, promote, verify
#
# The helper runs its own bundled copy of backend/app, so a merge the API gets
# through CI did not reach it until someone deployed it by hand, and nothing
# reported which code it ran (BUG-531).
#
# The helper never goes ahead of the API: an old API with a new helper can be
# unsafe. To deploy, API_COMMIT names the commit the API was verified on. The
# target is the latest helper commit at or before it, and the API must still
# serve the same backend/app when the deploy starts. The deployment is checked
# on its own address before the production domain moves to it, so a broken one
# never serves production.
#
# Environment:
#   HELPER_URL         the helper endpoint on the production domain
#   HELPER_ALIAS       the production domain
#   HELPER_PATH        the endpoint's path on a deployment address (default /api/native_extract)
#   API_URL            the API base (its /health reports the commit it serves); to deploy
#   API_COMMIT         the commit the API was verified on; to deploy
#   TARGET_REF         dry run only: where to look for the target (default origin/main)
#   PINS_PYTHON        a Python with ONLY the helper's pins (default: built from the staged pins)
#   BASE_PYTHON        the interpreter that builds that environment (default python3)
#   WORK               scratch directory (default: a new temporary directory)
#   VERCEL_TOKEN, VERCEL_ORG_ID, VERCEL_PROJECT_ID   to deploy
#   VERCEL_CLI         the CLI package (default vercel@65.7.0; the API refuses CLIs older than 47.2.2)
#   VERCEL_API         default https://api.vercel.com
#   HTTP_TIMEOUT, VERIFY_ATTEMPTS, VERIFY_INTERVAL   default 90 seconds, 20 tries, 15 seconds
set -euo pipefail

mode="${1:-}"
case "$mode" in "" | --dry-run) ;; *) echo "usage: deploy.sh [--dry-run]" >&2; exit 2 ;; esac
: "${HELPER_URL:?HELPER_URL is required}"
cd "$(git rev-parse --show-toplevel)"
work="${WORK:-$(mktemp -d)}"
http_timeout="${HTTP_TIMEOUT:-90}"
attempts="${VERIFY_ATTEMPTS:-20}"
interval="${VERIFY_INTERVAL:-15}"
cli="${VERCEL_CLI:-vercel@65.7.0}"
paths=(api backend/app compute-helper/vercel.json compute-helper/requirements.txt)

known() { [ -n "$1" ] && git cat-file -e "$1^{commit}" 2>/dev/null; }
same_tree() { [ "$(git rev-parse "$1:$3" 2>/dev/null)" = "$(git rev-parse "$2:$3")" ]; }
same_files() {
  local path
  for path in "${paths[@]}"; do same_tree "$1" "$2" "$path" || return 1; done
}
reported_commit() {
  curl -sS --retry 2 --retry-delay 10 --max-time "$http_timeout" "$1" | jq -r '.commit // empty' || true
}

if [ "$mode" = "--dry-run" ]; then
  target="$(git log -1 --format=%H "${TARGET_REF:-origin/main}" -- "${paths[@]}")"
else
  : "${API_URL:?API_URL is required to deploy}"
  : "${API_COMMIT:?API_COMMIT (the commit the API was verified on) is required to deploy}"
  # main's latest helper files, unless their backend/app is newer than the
  # API's; then the latest at or before the API's commit, and the newer one
  # ships after its own API deploy.
  target="$(git log -1 --format=%H origin/main -- "${paths[@]}")"
  if ! same_tree "$target" "$API_COMMIT" backend/app; then
    target="$(git log -1 --format=%H "$API_COMMIT" -- "${paths[@]}")"
  fi
fi
live="$(reported_commit "$HELPER_URL")"
echo "helper commit to ship: $target"
echo "helper serving:        ${live:-unknown (no commit reported)}"
current=no
if [ "$live" = "$target" ] || { known "$live" && same_files "$live" "$target"; }; then
  current=yes
  echo "the helper already runs these files"
fi

bash compute-helper/stage.sh "$target" "$work/helper"
pins_python="${PINS_PYTHON:-}"
if [ -z "$pins_python" ]; then
  # Built from the staged pins, so the gate tests the target with its own pins.
  "${BASE_PYTHON:-python3}" -m venv "$work/pins"
  "$work/pins/bin/pip" install --quiet --disable-pip-version-check -r "$work/helper/requirements.txt"
  pins_python="$work/pins/bin/python"
fi
# The gates run the target's code, so they never see the deploy token.
(cd backend && env -u VERCEL_TOKEN uv run --frozen python ../compute-helper/gate/check_helper.py \
  "$work/helper" --pins-python "$pins_python" --out "$work/gate")

if [ "$mode" = "--dry-run" ]; then
  if [ "$current" = yes ]; then
    echo "dry run: gates passed; nothing to deploy"
  else
    echo "dry run: gates passed; a deploy would ship $target"
  fi
  exit 0
fi
[ "$current" = no ] || exit 0

api_live="$(curl -sS --max-time "$http_timeout" "$API_URL/health" | jq -r '.commit // empty' || true)"
if ! known "$api_live" || ! same_tree "$api_live" "$target" backend/app; then
  echo "::error::the API serves ${api_live:-an unknown commit}, whose backend/app is not the helper target's; not deploying the helper ahead of the API"
  exit 1
fi

: "${HELPER_ALIAS:?HELPER_ALIAS is required to deploy}"
for name in VERCEL_TOKEN VERCEL_ORG_ID VERCEL_PROJECT_ID; do
  [ -n "${!name:-}" ] || { echo "::error::$name is missing"; exit 1; }
done
api="${VERCEL_API:-https://api.vercel.com}/v13/deployments"
deployment_id() {
  curl -sS --max-time "$http_timeout" -H "Authorization: Bearer $VERCEL_TOKEN" \
    "$api/$1?teamId=$VERCEL_ORG_ID" | jq -r '.id // empty'
}
check() {
  # $1 endpoint; the helper must report the target and refuse an unsigned job.
  local commit code
  commit="$(reported_commit "$1")"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
    -d '{}' --max-time "$http_timeout" "$1" || true)"
  echo "  $1: reports ${commit:-no commit}; unsigned job HTTP $code"
  [ "$commit" = "$target" ] && [ "$code" = "401" ]
}

# Deployed without the production domain: production keeps the current helper
# until this deployment has passed its own checks.
url="$(cd "$work/helper" && npx --yes "$cli" deploy --prod --skip-domain --yes \
  --token "$VERCEL_TOKEN" --env "DEPLOYED_COMMIT=$target" | grep -E '^https://' | tail -n 1)"
[ -n "$url" ] || { echo "::error::the Vercel CLI reported no deployment URL"; exit 1; }
new_id="$(deployment_id "${url#https://}")"
[ -n "$new_id" ] || { echo "::error::the Vercel API does not know $url"; exit 1; }
echo "deployed $target as $new_id (not yet on $HELPER_ALIAS)"

ok=no
for attempt in $(seq 1 "$attempts"); do
  if check "$url${HELPER_PATH:-/api/native_extract}"; then ok=yes; break; fi
  sleep "$interval"
done
[ "$ok" = yes ] || {
  echo "::error::the new deployment failed its own checks; production still runs the previous helper"
  exit 1
}

npx --yes "$cli" promote "$url" --yes --token "$VERCEL_TOKEN" >/dev/null
ok=no
for attempt in $(seq 1 "$attempts"); do
  aliased="$(deployment_id "$HELPER_ALIAS")"
  echo "  $HELPER_ALIAS points at ${aliased:-unknown}"
  if [ "$aliased" = "$new_id" ] && check "$HELPER_URL"; then ok=yes; break; fi
  sleep "$interval"
done
[ "$ok" = yes ] || { echo "::error::$HELPER_ALIAS does not serve $new_id ($target)"; exit 1; }
echo "helper serves $target on $HELPER_ALIAS; an unsigned job is refused (401)"
