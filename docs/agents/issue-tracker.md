# Issue tracker: GitHub

Issues and specs live on [`CivicTechTO/toronto-bids`](https://github.com/CivicTechTO/toronto-bids). Use the `gh` CLI (it infers the repository from `git remote -v`).

## Repo-specific rules

1. **`gh issue create` is blocked in agent sessions.** Create issues with `gh api` instead:

   ```shell
   gh api repos/CivicTechTO/toronto-bids/issues \
     -f title="..." \
     -f body="$(cat <<'EOF'
   ...multi-line body...
   EOF
   )"
   ```

   Other subcommands (`view`, `list`, `comment`, `edit`, `close`) work normally.

2. **Confirm with Alex before any outward GitHub write.** Creating issues, commenting, labelling, and closing are public on this CivicTechTO repository. Show the draft first and write only after approval.

## Common commands

- **Create an issue:** `gh api repos/CivicTechTO/toronto-bids/issues -f title=... -f body=...`
- **Read an issue:** `gh issue view <number> --comments`
- **List open issues:** `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`
- **Comment:** `gh issue comment <number> --body "..."`
- **Edit labels:** `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close:** `gh issue close <number> --comment "..."`

Documentation and commit messages reference prior findings by issue number (for example `#94`, `#116`, `#151`, `#203`). Fetch those issues with `gh issue view <number> --comments` when you need background context.

## Pull requests as a triage surface

**PRs as a request surface: no.** (Set to `yes` if external PRs should be triaged as feature requests.)

If enabled (`yes`), use the `gh pr` equivalents:

- **Read:** `gh pr view <number> --comments` and `gh pr diff <number>`
- **List external PRs:** `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`, filtering `authorAssociation` to `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE`
- **Comment / label / close:** `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`

Because GitHub shares numbering between issues and PRs, resolve an ambiguous `#42` by trying `gh pr view 42` and falling back to `gh issue view 42`.

## Wayfinding operations (`/wayfinder`)

The map is a single GitHub issue with child issues as tickets:

- **Map:** Create via `gh api`, then run `gh issue edit <n> --add-label wayfinder:map`. Holds the Notes, Decisions-so-far, and Fog sections.
- **Child ticket:** Link as a GitHub sub-issue via the sub-issues API (if unavailable, add it to a task list in the map body and add `Part of #<map>` to the child body). Apply `wayfinder:<type>` (`research`, `prototype`, `grilling`, or `task`) and assign when claimed.
- **Blocking:** Use GitHub native issue dependencies:
  `gh api --method POST repos/CivicTechTO/toronto-bids/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`
  Here `<blocker-db-id>` is the numeric database ID from `gh api repos/CivicTechTO/toronto-bids/issues/<n> --jq .id` (not the issue number or `node_id`). Open blockers appear in `issue_dependencies_summary.blocked_by`. If native dependencies are unavailable, use a `Blocked by: #<n>` line at the top of the child body.
- **Frontier query:** List open child issues on the map and filter out any with an assignee or open blocker (`issue_dependencies_summary.blocked_by > 0`); pick the first in map order.
- **Claim:** `gh issue edit <n> --add-assignee @me`.
- **Resolve:** `gh issue comment <n> --body "<answer>"`, close with `gh issue close <n>`, and append a summary link to the map's Decisions-so-far section.
