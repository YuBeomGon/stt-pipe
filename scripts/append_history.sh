#!/usr/bin/env bash
# Append one iter narrative + metric header to runs/_summary/HISTORY.md.
#
# Usage:
#   scripts/append_history.sh <iter> <commit> <metric> <delta> <status>
#
# Behavior:
#   - Writes a header line (iter · commit · cer · Δ · status) and the full
#     git commit body of <commit> into runs/_summary/HISTORY.md (append-only).
#   - <commit> is resolved by `git log -1 <commit>`; any rev (hash/HEAD/tag) ok.
#   - Creates runs/_summary/ on demand.
#
# Single source of truth: commit body (§4.4 PHASE3-PLAN.md). This script
# materializes a time-ordered derived view for human + agent consumption.

set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "Usage: $0 <iter> <commit> <metric> <delta> <status>" >&2
  exit 2
fi

ITER="$1"
COMMIT="$2"
METRIC="$3"
DELTA="$4"
STATUS="$5"

# Resolve commit early so a bad rev fails before we touch HISTORY.md.
FULL_HASH=$(git rev-parse --short "$COMMIT")

mkdir -p runs/_summary
{
  printf '\n## iter %s · %s · cer=%s (Δ%s) · %s\n\n' \
    "$ITER" "$FULL_HASH" "$METRIC" "$DELTA" "$STATUS"
  git log --format='%b' -n 1 "$FULL_HASH"
} >> runs/_summary/HISTORY.md
