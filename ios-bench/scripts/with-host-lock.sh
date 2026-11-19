#!/bin/bash
# Runs a command while holding this Mac's heavy-build lock (one Swift/Xcode build at a time;
# overlapping builds have frozen this host). Retries mkdir every 30 s; removes only its own lock.
L=/private/tmp/claude-501/wisp-heavy.lock
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
