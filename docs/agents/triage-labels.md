# Triage labels

Maps the five standard triage roles to GitHub label strings in `CivicTechTO/toronto-bids`:

| Role | Repo label | Meaning |
|---|---|---|
| `needs-triage` | `needs-triage` | Maintainer needs to evaluate the issue |
| `needs-info` | `needs-info` | Waiting on reporter for more information |
| `ready-for-agent` | `ready-for-agent` | Fully specified and ready for an automated agent |
| `ready-for-human` | `ready-for-human` | Requires human implementation |
| `wontfix` | `wontfix` | Will not be actioned |

## Setup status (checked 2026-08-06)

`wontfix` already exists as a default GitHub label. The other four need to be created the first time triage uses them (confirm with the maintainer first, per `docs/agents/issue-tracker.md`):

```shell
gh label create needs-triage    --description "Maintainer needs to evaluate this issue"
gh label create needs-info      --description "Waiting on reporter for more information"
gh label create ready-for-agent --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --description "Requires human implementation"
```

These state labels are orthogonal to the repo's existing category and priority labels (`bug`, `enhancement`, `documentation`, `question`, `api`, `priority: high`, `priority: low`). An issue can carry both (for example `bug`, `priority: high`, and `ready-for-agent`).
