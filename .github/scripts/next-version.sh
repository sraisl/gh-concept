#!/usr/bin/env bash
# Usage: next-version.sh <major|minor|patch> < tags
#
# Reads existing tags (one per line) from stdin and prints the next
# release-candidate tag, e.g. v1.4.0-rc.1.
#
# The bump is applied to the last final release (vX.Y.Z). If a pending RC
# line already targets an equal or higher version, its RC number is
# incremented instead, so a patch after a minor RC yields v1.4.0-rc.2,
# not v1.3.1-rc.1.
set -euo pipefail

bump=${1:?usage: next-version.sh <major|minor|patch>}
tags=$(cat)

final=$(grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' <<<"$tags" | sed 's/^v//' | sort -V | tail -n1 || true)
final=${final:-0.0.0}

IFS=. read -r ma mi pa <<<"$final"
case $bump in
  major) ma=$((ma + 1)); mi=0; pa=0 ;;
  minor) mi=$((mi + 1)); pa=0 ;;
  patch) pa=$((pa + 1)) ;;
  *) echo "unknown bump: $bump" >&2; exit 1 ;;
esac
candidate="$ma.$mi.$pa"

# Highest RC above the last final release, if any.
latest_rc=$(grep -E '^v[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$' <<<"$tags" | sed 's/^v//' | sort -V | tail -n1 || true)
pending=${latest_rc%-rc.*}
if [ -z "$latest_rc" ] || [ "$(printf '%s\n%s\n' "$final" "$pending" | sort -V | tail -n1)" = "$final" ]; then
  echo "v${candidate}-rc.1"
  exit 0
fi

target=$(printf '%s\n%s\n' "$candidate" "$pending" | sort -V | tail -n1)
if [ "$target" = "$pending" ]; then
  echo "v${pending}-rc.$((${latest_rc##*-rc.} + 1))"
else
  echo "v${target}-rc.1"
fi
