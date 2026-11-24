# Fork contributing rules (for Claude and humans)

Moved here from `CLAUDE.md`, which now holds the facts a session needs. `CLAUDE.md` links here.

## Effort level: suggest it before every task

The owner switches Claude's effort level by hand and asked to be told which one fits.
Before starting any task, begin the reply with one line:

> **Effort: <low | medium | high> recommended** (<a few words why>).

- **low:** routine chores. Checking CI, merging a green PR, writing release notes or the publish link, explaining a screenshot or log, small text or translation tweaks.
- **medium (default):** anything that changes or reviews code, such as a feature, a fix, or a review of a PR.
- **high:** a large upstream sync with conflicts in files the fork changed, a security review, or changes to money calculations across several services.

If the task needs a higher level than the one the conversation has been running on, stop after the
line and wait for the owner to say "go". If a lower level would do, or the level is unchanged, add the
line and carry on. The current level can't be read from inside the session, so phrase it as a
suggestion ("switch if you're not on it").

Scheduled runs with no one watching (such as the daily upstream sync) skip the reminder.

## Code quality and security (OWASP)

Every change follows these, in every session.

**Process**
- If the Superpowers plugin is available, use its skills: `test-driven-development` for features and
  fixes, `systematic-debugging` for bugs, `verification-before-completion` before saying something works,
  and `requesting-code-review` before opening a PR. Without it, follow the same steps by hand.
- Write the failing test first for every fix, then make it pass. Run the repo's checks before pushing:
  backend `ruff`, `ty` and `pytest -W error`, frontend typecheck, eslint and `vitest`.
- Review your own diff before the PR, looking for bugs, edge cases, and consistency with upstream's
  behaviour (for example, the Budgets page numbers).

**Security checklist (OWASP Top 10 and API Top 10)**
Check each item a change touches. If the Security Guidance or claude-security plugin is available, use it
on the diff before opening the PR, and run a full claude-security scan before a release with many changes.
- **Access control:** every query is scoped to the caller's workspace. Every new `/api` route is in
  `backend/app/core/access_token_scopes.py` under the right scope, with a test. Write routes need write
  permission (`require_write` or `require_module_write`). Never trust an id from the client without
  checking that it belongs to the workspace.
- **Injection:** SQLAlchemy expressions only, never SQL built from strings. All input goes through
  Pydantic schemas with bounds (lengths, ranges, enums).
- **XSS:** no `dangerouslySetInnerHTML`, no unescaped HTML from data, and no user text in URLs without
  encoding.
- **SSRF and outbound calls:** call only fixed, known hosts (such as `api.bcb.gov.br`) with timeouts.
  Validate and sanity-check what comes back before storing it.
- **Secrets:** never in code, tests, logs or commits. Use settings or env vars. Error messages and logs
  must not leak tokens, passwords or personal data.
- **Auth and sessions:** don't weaken rate limits, token checks or password rules. Upstream's auth code
  is not changed by the fork.
- **Dependencies:** add as few as possible, pin versions, and prefer what upstream already uses. Look at
  Dependabot alerts that touch packages a change adds or upgrades.
- **Data integrity:** money uses `Decimal`, never float. Changes that delete or rewrite user data must be
  scoped, tested, and must not touch rows the user edited by hand.
- **Logging and failures:** fail closed (deny when unsure), and log enough to debug without personal data.

## Standing rules

- Commit as `Claude` <noreply@anthropic.com>: Claude Code sessions sign these, so GitHub shows them Verified. The owner's own commits use the noreply address `139562176+viniciuscmartins@users.noreply.github.com`.
- No personal details (email, location, banks, home or network setup) and no chat or session links in
  commits, PRs, release notes or files. If your attribution adds a session link, leave it out.
  `scripts/fork/privacy-check.sh` checks commits before a push; CI's "Fork privacy check" checks every PR.
- Versions follow upstream. Fork releases are tagged `v<upstream version>-plan.<N>` and the version
  numbers in the repo are never bumped.
- New strings go in English and Portuguese (pt-BR, pt-PT); other locales carry the English text.
- Don't merge a PR or publish a release unless the owner asks.
- Fork history and conventions: `docs/fork/HISTORY.md`.
