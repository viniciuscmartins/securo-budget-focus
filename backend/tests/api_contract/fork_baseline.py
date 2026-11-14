"""The fork's contract-baseline entries live in baseline.fork.json, so
upstream's baseline.json stays byte-identical to upstream's and merges cleanly.

Entries whose path starts with one of FORK_PATH_PREFIXES belong to the fork.
"""

import json
from pathlib import Path

FORK_BASELINE_PATH = Path(__file__).with_name("baseline.fork.json")
FORK_PATH_PREFIXES = (
    "/api/budgets/year-plan",
    "/api/accounts/{account_id}/cdi-yield",
    "/api/month-outlook",
    "/api/recurring-links",
)

Baseline = dict[str, dict[str, int]]


def is_fork_key(key: str) -> bool:
    """`key` is "<METHOD> <path>"."""
    path = key.split(" ", 1)[-1]
    return path.startswith(FORK_PATH_PREFIXES)


def merge(upstream: Baseline) -> Baseline:
    """Upstream's baseline plus the fork's entries."""
    if not FORK_BASELINE_PATH.exists():
        return upstream
    for variant, statuses in json.loads(FORK_BASELINE_PATH.read_text()).items():
        upstream.setdefault(variant, {}).update(statuses)
    return upstream


def write(upstream_path: Path, ordered: Baseline) -> None:
    """Write upstream's entries to `upstream_path` and the fork's to
    baseline.fork.json, both in the same format as before."""
    ups: Baseline = {}
    fork: Baseline = {}
    for variant, statuses in ordered.items():
        for key, status in statuses.items():
            (fork if is_fork_key(key) else ups).setdefault(variant, {})[key] = status
    upstream_path.write_text(json.dumps(ups, indent=1) + "\n")
    FORK_BASELINE_PATH.write_text(json.dumps(fork, indent=1) + "\n")
