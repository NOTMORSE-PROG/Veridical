#!/usr/bin/env bash
# Deploy the native-compute helper from main, or show what a deploy would do.
#
#   bash compute-helper/deploy.sh --dry-run   # decide, stage, run both gates; never deploys
#   bash compute-helper/deploy.sh             # the same, then deploy, promote and verify
#
# The helper runs its own bundled copy of backend/app, so a merge the API gets
# through CI did not reach it until someone deployed it by hand, and nothing
# reported which code it ran (BUG-531). This script deploys the latest commit
# on main that touched the helper's files, only after both gates pass on
# exactly the staged files, and then checks the helper reports that commit.
# Deploy the API first: an old API with a new helper can be unsafe, which is
# why CI runs this only after the backend deploy has finished.
#
# Environment:
#   HELPER_URL         the helper endpoint (GET reports service, version, commit)
#   HELPER_ALIAS       the production domain the deployment must be aliased to
#   PINS_PYTHON        a Python with ONLY compute-helper/requirements.txt installed
#   WORK               scratch directory (default: a new temporary directory)
#   TARGET_REF         where to look for the target commit (default: origin/main)
#   VERCEL_TOKEN, VERCEL_ORG_ID, VERCEL_PROJECT_ID   needed only to deploy
set -euo pipefail

mode="${1:-}"
case "$mode" in "" | --dry-run) ;; *) echo "usage: deploy.sh [--dry-run]" >&2; exit 2 ;; esac
: "${HELPER_URL:?HELPER_URL is required}"
: "${PINS_PYTHON:?PINS_PYTHON is required}"
cd "$(git rev-parse --show-toplevel)"
work="${WORK:-$(mktemp -d)}"
paths=(api backend/app compute-helper/vercel.json compute-helper/requirements.txt)

target="$(git log -1 --format=%H "${TARGET_REF:-origin/main}" -- "${paths[@]}")"
live="$(curl -sS --retry 2 --retry-delay 10 --max-time 90 "$HELPER_URL" | jq -r '.commit // empty' || true)"
echo "latest helper commit: $target"
echo "helper serving:       ${live:-unknown (no commit reported)}"

known() { [ -n "$1" ] && git cat-file -e "$1^{commit}" 2>/dev/null; }
same_files() {
  local path
  for path in "${paths[@]}"; do
    [ "$(git rev-parse "$1:$path" 2>/dev/null)" = "$(git rev-parse "$2:$path")" ] || return 1
  done
}
current=no
if [ "$live" = "$target" ] || { known "$live" && same_files "$live" "$target"; }; then
  current=yes
  echo "the helper already runs these files"
fi

bash compute-helper/stage.sh "$target" "$work/helper"
(cd backend && uv run --frozen python ../compute-helper/gate/check_helper.py "$work/helper" \
  --pins-python "$PINS_PYTHON" --out "$work/gate")

if [ "$mode" = "--dry-run" ]; then
  if [ "$current" = yes ]; then
    echo "dry run: gates passed; nothing to deploy"
  else
    echo "dry run: gates passed; a deploy would ship $target"
  fi
  exit 0
fi
[ "$current" = no ] || exit 0

: "${HELPER_ALIAS:?HELPER_ALIAS is required to deploy}"
for name in VERCEL_TOKEN VERCEL_ORG_ID VERCEL_PROJECT_ID; do
  [ -n "${!name:-}" ] || { echo "::error::$name is missing"; exit 1; }
done
api="https://api.vercel.com/v13/deployments"
deployment_id() {
  curl -sS -H "Authorization: Bearer $VERCEL_TOKEN" "$api/$1?teamId=$VERCEL_ORG_ID" | jq -r '.id // empty'
}

url="$(cd "$work/helper" && npx --yes vercel@65.7.0 deploy --prod --yes --token "$VERCEL_TOKEN" \
  --env "DEPLOYED_COMMIT=$target" | grep -E '^https://' | tail -n 1)"
[ -n "$url" ] || { echo "::error::the Vercel CLI reported no deployment URL"; exit 1; }
new_id="$(deployment_id "${url#https://}")"
echo "deployed $target as $new_id"

# After a rollback Vercel stops moving the production domain to new
# deployments, and the domain keeps answering from the old one.
if [ "$(deployment_id "$HELPER_ALIAS")" != "$new_id" ]; then
  echo "the production domain still points elsewhere; promoting $new_id"
  npx --yes vercel@65.7.0 promote "$url" --yes --token "$VERCEL_TOKEN" >/dev/null
fi

for attempt in $(seq 1 20); do
  aliased="$(deployment_id "$HELPER_ALIAS")"
  live="$(curl -sS --max-time 60 "$HELPER_URL" | jq -r '.commit // empty' || true)"
  if [ "$aliased" = "$new_id" ] && [ "$live" = "$target" ]; then
    break
  fi
  echo "attempt $attempt: domain on ${aliased:-unknown}, helper reports ${live:-unknown}"
  [ "$attempt" -lt 20 ] || { echo "::error::the helper does not report $target"; exit 1; }
  sleep 15
done
code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -d '{}' --max-time 60 "$HELPER_URL")"
[ "$code" = "401" ] || { echo "::error::an unsigned job got HTTP $code, not 401"; exit 1; }
echo "helper serves $target on $HELPER_ALIAS; an unsigned job is refused (401)"
