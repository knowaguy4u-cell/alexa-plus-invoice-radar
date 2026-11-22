# AGENTS.md

Rules for any agent — LLM, script or automation — working in this repository.

## Outbound writes

An outbound write is anything that leaves this machine or this clone: `git push`, opening or editing a PR or issue, posting a comment, publishing a release, changing repo settings, or calling a third-party service.

- **Max 1 PR per repo per day.** Never open a PR to a repo where the issue hasn't been claimed.
- **Never push bulk branches.** One branch, one intent. Branch names must not match `auto-fix-*`; a global `pre-push` hook (`pr-guard`) blocks those by default, and the block is the point.
- **No PR until the code has run.** A PR is allowed only after the patch has been executed and its actual output pasted into `## Checks run`. Generated code that was never executed stays local.
- **No agent identity on someone else's repo.** Commit as the human owner of the account; never as a bot, a second account, or a named-agent identity when pushing to a third-party repo.
- **No unprompted comments.** A review request, a claim (`/claim`, `/attempt`), or an "any update?" ping is an outbound write. Ask the human first unless they asked for that exact message.
- **Don't rewrite history on someone else's branch.** No force-push to a branch a maintainer may have based work on, and never to a release tag.
- **Don't route around a blocked push.** If `pr-guard` refuses a push, fix the cause. `PR_GUARD_ALLOW=1` is for the human, per push, not for an agent's retry loop.

## Local work

Reading, branching, committing and testing are local and unrestricted.

## Why these rules exist

In September 2026 an automation opened 13 PRs across 7 third-party repos in 109 minutes, all on `auto-fix-<issue>` branches, with patches that had never been executed — one shipped a literal `$categories` because the generator wrote `cat << 'EOF'` and never ran it. All 13 were closed within a day.
