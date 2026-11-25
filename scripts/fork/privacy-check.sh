#!/usr/bin/env bash
# Fails if the fork's own commits would publish personal data.
#
# Usage: scripts/fork/privacy-check.sh [BASE] [HEAD]
#   BASE defaults to origin/main, HEAD to HEAD. Commits already in upstream
#   (ref `upstream/main` or `refs/remotes/upstream/main`, when fetched) are
#   skipped: their authors published them there.
#
# Checks every commit in BASE..HEAD that is not in upstream:
#   1. author and committer emails are GitHub or Anthropic no-reply addresses;
#   2. no chat/session links (claude.ai/code, Claude-Session) in messages;
#   3. lines it adds contain no session link, no email address other than
#      no-reply/example ones, and no token-looking string. For a merge, the
#      lines it adds are its conflict resolutions: lines new to every parent.
#   4. optional private terms (bank and account names, places, anything that
#      must never be published): one per line, case-insensitive, in
#      `$PRIVACY_TERMS_FILE` or `.git/info/privacy-terms`. That file stays
#      outside the repository, so the terms themselves are never committed,
#      and a match is reported without the term.
# Run it before every push; CI runs it on every pull request.
set -euo pipefail

base="${1:-origin/main}"
head="${2:-HEAD}"
exclude=()
for ref in upstream/main refs/remotes/upstream/main; do
  if git rev-parse -q --verify "$ref^{commit}" >/dev/null; then exclude=(--not "$ref"); break; fi
done

commits=$(git rev-list "$base..$head" "${exclude[@]}")
[ -z "$commits" ] && { echo "privacy-check: no new commits"; exit 0; }

ok_email='(@users\.noreply\.github\.com|^noreply@github\.com|^noreply@anthropic\.com|^[0-9]+\+[a-z0-9-]+\[bot\]@users\.noreply\.github\.com)$'
session='claude\.ai/code|Claude-Session:|session_0[0-9A-Za-z]{10,}'
email='[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
ok_in_text='users\.noreply\.github\.com|noreply@anthropic\.com|noreply@github\.com|@example\.(com|org|net)|@test\b|@localhost|\.test\b|@securo\.'
token='(ghp_|gho_|github_pat_|sk-ant-|xox[bp]-|AKIA)[A-Za-z0-9_-]{10,}'

terms_file="${PRIVACY_TERMS_FILE:-$(git rev-parse --git-path info/privacy-terms)}"
terms=()
if [ -f "$terms_file" ]; then
  while IFS= read -r term || [ -n "$term" ]; do
    term="${term%$'\r'}"
    [ -n "${term// }" ] && [ "${term:0:1}" != "#" ] && terms+=("$term")
  done < "$terms_file"
fi
has_term() {  # stdin: text; true if it holds any private term
  local text; text=$(cat)
  local term
  for term in "${terms[@]}"; do
    grep -qiF -- "$term" <<<"$text" && return 0
  done
  return 1
}

# Lines a commit adds. A merge's own lines are its conflict resolutions: in the
# combined diff those are the lines marked added against every parent ("++").
added_lines() {
  local c=$1
  local pathspec=(-- . ':(exclude)*.lock' ':(exclude)*lock.json' ':(exclude)scripts/fork/privacy-check.sh')
  if [ "$(git rev-list --parents -n1 "$c" | wc -w)" -gt 2 ]; then
    git show --cc --format= --unified=0 "$c" "${pathspec[@]}" | grep -E '^\+\+' | grep -vE '^\+\+\+ ' | sed 's/^+//' || true
  else
    git show --format= --unified=0 "$c" "${pathspec[@]}" | grep -E '^\+' | grep -vE '^\+\+\+' || true
  fi
}

fail=0
report() { echo "privacy-check: $1"; fail=1; }

for c in $commits; do
  short=$(git rev-parse --short "$c")
  for who in "$(git log -1 --format=%ae "$c")" "$(git log -1 --format=%ce "$c")"; do
    echo "$who" | grep -qiE "$ok_email" || report "$short: author/committer email is not a no-reply address"
  done
  message=$(git log -1 --format=%B "$c")
  grep -qiE "$session" <<<"$message" && report "$short: commit message has a session link"
  [ "${#terms[@]}" -gt 0 ] && has_term <<<"$message" && report "$short: commit message has a private term"
  added=$(added_lines "$c")
  [ -z "$added" ] && continue
  [ "${#terms[@]}" -gt 0 ] && has_term <<<"$added" && report "$short: adds a private term"
  echo "$added" | grep -qiE "$session" && report "$short: adds a session link"
  echo "$added" | grep -oE "$email" | grep -viE "$ok_in_text" | grep -q . && report "$short: adds an email address"
  echo "$added" | grep -qE "$token" && report "$short: adds something that looks like a token"
done

[ "$fail" -eq 0 ] && echo "privacy-check: $(echo "$commits" | wc -l | tr -d ' ') commit(s) clean"
exit "$fail"
