#!/bin/bash
# Runs a command while holding a build lock, so only one Swift/Xcode build runs at a time
# (parallel builds can exhaust memory on a smaller Mac). Retries mkdir every 30 s; removes only
# its own lock. Set BENCH_BUILD_LOCK to share a lock directory with other projects.
L="${BENCH_BUILD_LOCK:-${TMPDIR:-/tmp}}"
[ -n "${BENCH_BUILD_LOCK:-}" ] || L="${L%/}/tinyagent-bench-build.lock"
tries=${LOCK_TRIES:-360}
for ((i = 0; i < tries; i++)); do
    if mkdir "$L" 2>/dev/null; then
        trap 'rmdir "$L"' EXIT
        echo "[lock] acquired $(date '+%H:%M:%S')"
        "$@"
        rc=$?
        echo "[lock] releasing $(date '+%H:%M:%S') rc=$rc"
        exit $rc
    fi
    ((i % 10 == 0)) && echo "[lock] held by another job, waiting ($(date '+%H:%M:%S'))"
    sleep 30
done
echo "LOCK HELD"
exit 1
