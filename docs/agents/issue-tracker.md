# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues on
[`CivicTechTO/toronto-bids`](https://github.com/CivicTechTO/toronto-bids). Use the `gh` CLI for
all operations.

## Two repo-specific rules that override the conventions below

1. **`gh issue create` does not work from an agent session here** — the harness's classifier
   denies it. Create issues through the API instead:

   ```shell
   gh api repos/CivicTechTO/toronto-bids/issues \
     -f title="..." \
     -f body="$(cat <<'EOF'
   ...multi-line body...
   EOF
   )"
   ```

   Everything else (`view`, `list`, `comment`, `edit`, `close`) works through the normal `gh`
   subcommands.

2. **Confirm with Alex before any outward GitHub write.** Creating an issue, commenting,
   labelling and closing are all publicly visible on a public CivicTechTO repo. Reading is free;
   writing is not. Draft the body, show it, then write once approved.

## Conventions

- **Create an issue**: `gh api repos/CivicTechTO/toronto-bids/issues -f title=... -f body=...`
  (see rule 1 — not `gh issue create`).
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also
  fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`
  with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature
requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr`
equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`
  then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE`
  (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`,
  `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve
with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue (via `gh api`, per rule 1, after confirming per rule 2).

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

Issue numbers are load-bearing in this repo's own documentation: `CLAUDE.md` and the commit
history reference findings by number (#94, #116, #151, #203 …), and those numbers point at
these issues. When a skill needs the background behind a `#nnn` mentioned in the codebase, that
is where to read it.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog
  body. Create via `gh api` (rule 1), then `gh issue edit <n> --add-label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues
  endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and
  put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>`
  (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the
  driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible
  representation. Add an edge with
  `gh api --method POST repos/CivicTechTO/toronto-bids/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`,
  where `<blocker-db-id>` is the blocker's numeric **database id**
  (`gh api repos/CivicTechTO/toronto-bids/issues/<n> --jq .id`, _not_ the `#number` or
  `node_id`). GitHub reports `issue_dependencies_summary.blocked_by` (open blockers only — the
  live gate). Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line
  at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the
  map's sub-issues / task list), drop any with an open blocker
  (`issue_dependencies_summary.blocked_by > 0`, or an open issue in the `Blocked by` line) or an
  assignee; first in map order wins.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append
  a context pointer (gist + link) to the map's Decisions-so-far.
