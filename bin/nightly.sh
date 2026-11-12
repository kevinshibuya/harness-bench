#!/bin/bash
# nightly.sh — the scheduled job: extract and report into out/, which is gitignored.
# Spends no Claude usage. launchd runs it at 03:30; logs go to out/extract.log.
# It never stages, commits or pushes: the store holds prompts and details of the target repos,
# so nothing it writes belongs in git.
set -euo pipefail
cd "$(dirname "$0")/.."
UV="$(command -v uv || echo /opt/homebrew/bin/uv)"

echo "== $(date '+%Y-%m-%dT%H:%M:%S') nightly"
"$UV" run --quiet bench extract
"$UV" run --quiet bench report
if ! git check-ignore -q out/report.md; then
  echo "out/ is not gitignored; refusing to leave results where git can see them" >&2
  exit 1
fi
echo "done; results in out/"
