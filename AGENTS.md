# AGENTS.md

Rules for any agent — LLM, script or automation — working in this repository.

## Outbound writes

An outbound write is anything that leaves this machine or this clone: `git push`, opening or editing a PR or issue, posting a comment, publishing a release, changing repo settings, or calling a third-party service.

- **Max 1 PR per repo per day.** Never open a PR to a repo where the issue hasn't been claimed.
- **Never push bulk branches.** One branch, one intent. Branch names must not match `auto-fix-*`; a global `pre-push` hook (`pr-guard`) blocks those by default, and the block is the point.
- **No PR until the code has run.** A PR is allowed only after the patch has been executed and its actual output pasted into `## Checks run`. Generated code that was never executed stays local.
- **Running is not sufficient.** An exit code of 0 only proves the script ran. Before a generated patch goes into a PR, check what it produced:
  - **Every URL must resolve.** Fetch each one and require a real 200 from the real host — not a 404, not a guessed `github.com/<org>/<repo>` path, not a redirect to a login page or a parked domain. `link-check` does this over the staged diff as a `pre-commit` hook (installed by `harvester-guard/install.sh`): a real 4xx/5xx blocks the commit, an unreachable host is skipped so an offline machine is not stuck.
  - **Generated copy must be real.** Product names, features, endpoints and links must come from this repo or its docs. Placeholder text ("Empowering developers with AI-powered tools!", "Lorem ipsum", "TODO", an empty value, a bare `# Title`) is a failed check, not a passing one.

  A patch that runs cleanly and ships a dead link is worse than one that fails, because it looks reviewed.
- **No agent identity on someone else's repo.** Commit as the human owner of the account; never as a bot, a second account, or a named-agent identity when pushing to a third-party repo.
- **No unprompted comments.** A review request, a claim (`/claim`, `/attempt`), or an "any update?" ping is an outbound write. Ask the human first unless they asked for that exact message.
- **Don't rewrite history on someone else's branch.** No force-push to a branch a maintainer may have based work on, and never to a release tag.
- **Don't route around a blocked push.** If `pr-guard` refuses a push, fix the cause. `PR_GUARD_ALLOW=1` is for the human, per push, not for an agent's retry loop. `git commit --no-verify` is the same: a bypass for a false positive, not a workflow.

## Local work

Reading, branching, committing and testing are local and unrestricted.

## Why these rules exist

On 2026-09-06 an automation opened 16 PRs across 7 third-party repos, 15 of them inside 109 minutes, all on `auto-fix-<issue>` branches. One shipped a literal `$categories` because the generator wrote `cat << 'EOF'` and never ran it. Another ran cleanly and still merged a dead URL (`github.com/misakanet/mcp-remote`, a 404) with placeholder copy — the maintainer reverted it three hours later. Fifteen were withdrawn or closed. All of it was avoidable damage.
