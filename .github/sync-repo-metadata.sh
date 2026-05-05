#!/usr/bin/env bash
# Push .github/repo-metadata.json to the live GitHub About sidebar.
#
# GitHub Actions' default GITHUB_TOKEN can't write repo metadata
# (description / homepage / topics) — those endpoints require the
# Administration scope, which is only on a real PAT or on a `gh`
# CLI session signed in to the repo owner. So this script runs
# locally, not in CI.
#
# Prereq:
#   gh auth status        → must show you're signed in to chentzuyuan
#                           (or whichever account owns the repo)
#
# Usage:
#   .github/sync-repo-metadata.sh
#
# To change what gets pushed, edit .github/repo-metadata.json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
META="$SCRIPT_DIR/repo-metadata.json"

if [[ ! -f "$META" ]]; then
    echo "ERROR: $META not found" >&2
    exit 1
fi

# Auto-detect owner/repo from the git remote so the script works for
# forks without editing.
REMOTE_URL="$(git -C "$SCRIPT_DIR/.." config --get remote.origin.url)"
SLUG="$(echo "$REMOTE_URL" | sed -E 's#.*[:/]([^/:]+/[^/.]+)(\.git)?$#\1#')"
echo "Repo:        $SLUG"

DESC="$(jq -r '.description' "$META")"
HOMEPAGE="$(jq -r '.homepage' "$META")"

echo "Description: ${DESC:0:80}..."
echo "Homepage:    $HOMEPAGE"

gh api -X PATCH "repos/$SLUG" \
    -f "description=$DESC" \
    -f "homepage=$HOMEPAGE" \
    --silent
echo "  ✓ description / homepage applied"

# `gh api -F` doesn't take an array, so pass each topic as a -f
# names[]=… repetition (which gh translates to a JSON array).
TOPIC_ARGS=()
while IFS= read -r topic; do
    TOPIC_ARGS+=( -f "names[]=$topic" )
done < <(jq -r '.topics[]' "$META")

gh api -X PUT "repos/$SLUG/topics" "${TOPIC_ARGS[@]}" --silent
echo "  ✓ topics replaced ($(jq -r '.topics | length' "$META") tags)"
echo
echo "About sidebar now live: https://github.com/$SLUG"
