#!/usr/bin/env bash
# The project's own tests, plus a run of every adapter whose library is installed.
#
# The unit tests decide the exit status. The adapter runs do not: a library diverging from git
# is the finding this thing exists to produce, not a broken build. If a divergence count here
# ever failed the suite, the temptation would be to make the corpus smaller.
set -uo pipefail
cd "$(dirname "$0")"

fail=0

echo "== unit tests =="
python3 test_gic.py 2>&1 | tail -25
[ "${PIPESTATUS[0]}" -eq 0 ] || fail=1

echo
echo "== adapters, over the frozen corpus (informational) =="
run_adapter() {   # name, availability probe, command...
  local name="$1"; local probe="$2"; shift 2
  if ! eval "$probe" >/dev/null 2>&1; then
    echo "-- $name: not installed here, skipped"
    return
  fi
  local out
  out=$(python3 gic.py --json -- "$@" 2>/dev/null) || true
  printf '%s' "$out" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("-- %-34s %5d cases  %4d divergences  %3d declined"
      % (sys.argv[1], d["checked"], len(d["divergences"]), len(d["declined"])))
' "$name" || echo "-- $name: no report (adapter broken?)"
}

run_adapter "pathspec (GitIgnoreSpec)" "python3 -c 'import pathspec'" \
            python3 adapters/pathspec_adapter.py
run_adapter "gitignore_parser" "python3 -c 'import gitignore_parser'" \
            python3 adapters/gitignore_parser_adapter.py
run_adapter "node-ignore" "node -e \"require('ignore')\"" \
            node adapters/node_ignore_adapter.js
run_adapter "node-ignore --no-ignorecase" "node -e \"require('ignore')\"" \
            node adapters/node_ignore_adapter.js --no-ignorecase

echo
if [ "$fail" -eq 0 ]; then echo "OK"; else echo "FAILED"; fi
exit "$fail"
