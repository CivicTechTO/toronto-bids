# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the
actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding
label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## State of these labels on the repo (checked 2026-08-06)

`wontfix` already exists (a GitHub default) and matches this table exactly. The other four do
**not** exist yet and will need creating the first time `/triage` applies one:

```shell
gh label create needs-triage    --description "Maintainer needs to evaluate this issue"
gh label create needs-info      --description "Waiting on reporter for more information"
gh label create ready-for-agent --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --description "Requires human implementation"
```

Creating a label is an outward write on a public repo — confirm first, per
`docs/agents/issue-tracker.md`.

The repo's existing vocabulary (`bug`, `enhancement`, `documentation`, `question`, `api`,
`priority: high`, `priority: low`) describes *what* an issue is and how urgent it is. These five
describe *what state it is in*. They are orthogonal and both apply — a triaged issue can carry
`bug` + `priority: high` + `ready-for-agent`.
