#!/usr/bin/env bash
# Upstream-sync report for the fork: what an upstream merge means for the fork's own changes.
# Prints Markdown for the sync PR body. Read-only; never reviews the whole upstream diff.
# Usage (from the repo root, remotes `origin` and `upstream` fetched):
#   scripts/fork/sync-report.sh            # compares origin/main with upstream/main
#   scripts/fork/sync-report.sh conflicts  # during a merge: lists the conflicted files
set -euo pipefail

FORK=${FORK_REF:-origin/main}
UP=${UPSTREAM_REF:-upstream/main}

if [[ "${1:-}" == "conflicts" ]]; then
  git diff --name-only --diff-filter=U
  exit 0
fi

base=$(git merge-base "$FORK" "$UP")
new=$(git rev-list --count "$base..$UP")
tag=$(git tag --merged "$UP" -l 'v*' | grep -v -- '-plan\.' | sort -V | tail -1 || true)

echo "## Upstream"
echo "- ${new} new commits, up to \`$(git rev-parse --short "$UP")\`; latest upstream release included: \`${tag:-none}\`"
echo

# Upstream files the fork has changed (as of the last upstream commit already merged).
fork_files=$(git diff --name-only --diff-filter=M "$base" "$FORK" | grep -v '^frontend/src/locales/' || true)
up_files=$(git diff --name-only "$base" "$UP")
overlap=$(comm -12 <(sort -u <<<"$fork_files") <(sort -u <<<"$up_files") || true)
locales=$(grep -c '^frontend/src/locales/' <<<"$up_files" || true)

echo "## Upstream changes in files the fork customizes"
if [[ -z "$overlap" ]]; then
  echo "None."
else
  while read -r f; do
    [[ -z "$f" ]] && continue
    echo "- \`$f\`"
    git log --format='  - %h %s' "$base..$UP" -- "$f"
  done <<<"$overlap"
fi
[[ "$locales" -gt 0 ]] && echo "- ${locales} locale files changed upstream (fork keys must be kept in each)."
echo

echo "## Needs attention when deploying"
mig=$(git diff --name-only --diff-filter=A "$base" "$UP" -- backend/alembic/versions)
ops=$(git diff --name-only "$base" "$UP" -- docker-compose.yml docker-compose.prod.yml .env.example charts)
newapi=$(git diff "$base" "$UP" -- 'backend/app/api/*.py' | grep -E '^\+.*APIRouter\(prefix=' | sed 's/^+//' || true)
[[ -n "$mig" ]] && { echo "- **Database migrations** (back up before updating):"; sed 's/^/  - /' <<<"$mig"; }
[[ -n "$ops" ]] && { echo "- Deployment files changed:"; sed 's/^/  - /' <<<"$ops"; }
[[ -n "$newapi" ]] && { echo "- New API routers (check access-token scopes):"; sed 's/^/  - /' <<<"$newapi"; }
[[ -z "$mig$ops$newapi" ]] && echo "Nothing."
exit 0
