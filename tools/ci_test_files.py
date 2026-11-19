#!/usr/bin/env python3
"""List the test files the offline CI gate runs, one path per line.

Every tracked file named test_*.py under tests/ or ios-bench/tools/ is run,
so a merged branch's new test files join the gate without anyone editing the
workflow. A file is left out only if it is named in EXCLUDED below, with the
reason. Each file runs in its own pytest process (see the workflow).

    python tools/ci_test_files.py           # the files to run
    python tools/ci_test_files.py --why     # also the excluded files and why

It exits non-zero if an exclusion names a file that no longer exists, so
stale entries get noticed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Where test files are discovered. A bare `pytest` is not used because
# pytest.ini (imported from Harbor) also lists air/tests, which this
# repository does not have.
PATTERNS = ("tests/test_*.py", "ios-bench/tools/test_*.py")

HARBOR_BACKEND = (
    "passes locally, but covers Harbor's assistant backend rather than a "
    "benchmark harness; not part of the benchmark's gate"
)

EXCLUDED = {
    "tests/test_send_recipient_preflight.py": (
        "one test reads the Mac app's Swift source (app/), which is not imported"
    ),
    "tests/test_local_provider_tools.py": HARBOR_BACKEND,
    "tests/test_power_tier.py": HARBOR_BACKEND,
    "tests/test_effect_refusal_no_lock.py": HARBOR_BACKEND,
    "tests/test_harbor_self_reference.py": HARBOR_BACKEND,
    "tests/test_long_email_digest.py": HARBOR_BACKEND,
    "tests/test_model_only_quality.py": HARBOR_BACKEND,
    "tests/test_model_led_integration.py": HARBOR_BACKEND,
    "tests/test_model_led_entrypoint.py": HARBOR_BACKEND,
}


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", *PATTERNS],
        check=True, capture_output=True, text=True,
    ).stdout
    return sorted(line for line in out.splitlines() if line)


def main(argv: list[str]) -> int:
    files = tracked()
    stale = sorted(set(EXCLUDED) - set(files))
    run = [f for f in files if f not in EXCLUDED]
    for f in run:
        print(f)
    if "--why" in argv:
        for f, why in sorted(EXCLUDED.items()):
            print(f"excluded: {f}: {why}", file=sys.stderr)
    if stale:
        print(f"stale exclusions (file not found): {', '.join(stale)}", file=sys.stderr)
        return 1
    if not run:
        print("no test files found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
