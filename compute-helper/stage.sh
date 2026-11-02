#!/usr/bin/env bash
# Stage the native-compute helper from one exact commit, as it is deployed:
# api/, backend/app and the helper's Vercel config and pins, with the config
# and pins at the root where Vercel's builder looks for them.
#   bash compute-helper/stage.sh <commit> <empty-or-missing-dir>
set -euo pipefail

commit="${1:?commit required}"
dest="${2:?destination directory required}"
full="$(git rev-parse --verify "$commit^{commit}")"
if [ -e "$dest" ] && [ -n "$(ls -A "$dest")" ]; then
  echo "refusing to stage into a non-empty directory: $dest" >&2
  exit 1
fi
mkdir -p "$dest"
git archive "$full" api backend/app compute-helper/vercel.json compute-helper/requirements.txt \
  | tar -x -C "$dest"
cp "$dest/compute-helper/vercel.json" "$dest/vercel.json"
cp "$dest/compute-helper/requirements.txt" "$dest/requirements.txt"
echo "$full" > "$dest/.staged-commit"
echo "staged $full in $dest ($(find "$dest/api" "$dest/backend/app" -name '*.py' | wc -l) Python files)"
