#!/bin/bash
# Builds mirrors/tiny-src: a two-file repo whose base commit has one failing pytest
# and whose reference commit fixes it. Fixed dates and identity keep the SHAs stable.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=mirrors/tiny-src
rm -rf "$DIR" mirrors/tiny.git
mkdir -p "$DIR/tests"
export GIT_AUTHOR_NAME=bench GIT_AUTHOR_EMAIL=bench@example.invalid
export GIT_COMMITTER_NAME=bench GIT_COMMITTER_EMAIL=bench@example.invalid
export GIT_AUTHOR_DATE="2026-09-01T12:00:00+00:00" GIT_COMMITTER_DATE="2026-09-01T12:00:00+00:00"
git -C "$DIR" init -q -b main
cat > "$DIR/calc.py" <<'PY'
def add(a, b):
    return a - b
PY
cat > "$DIR/tests/test_calc.py" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calc import add


def test_add():
    assert add(2, 3) == 5
PY
printf '__pycache__/\n.pytest_cache/\n' > "$DIR/.gitignore"
git -C "$DIR" add -A
git -C "$DIR" -c commit.gpgsign=false commit -q -m "base: add is broken"
export GIT_AUTHOR_DATE="2026-09-01T12:05:00+00:00" GIT_COMMITTER_DATE="2026-09-01T12:05:00+00:00"
sed -i '' 's/a - b/a + b/' "$DIR/calc.py"
git -C "$DIR" -c commit.gpgsign=false commit -q -am "fix: add adds"
echo "base=$(git -C "$DIR" rev-parse HEAD^)"
echo "reference=$(git -C "$DIR" rev-parse HEAD)"
