# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the
codebase.

**Layout: single-context.** One `CONTEXT.md` at the repo root plus `docs/adr/`. This is a single
`uv`-managed Python package (`scrapers/`) with no workspaces or sub-packages, so there is no
context map.

## Before exploring, read these

- **`CLAUDE.md`** at the repo root — in this repo it is the substantive architecture document,
  not a thin pointer file. It carries the source contract, ordering/overwrite semantics, the
  four keyspaces, and the per-source findings. Read it first; most questions a skill would go
  looking for are already answered there.
- **`CONTEXT.md`** at the repo root, if it exists.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest
creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and
`/improve-codebase-architecture`) creates them lazily when terms or decisions actually get
resolved.

## Where prior decisions actually live in this repo

`docs/adr/` does not exist yet. Until it does, decisions of ADR weight are recorded in three
other places, and a skill looking for "why is it like this" should check them:

- **`CLAUDE.md`** — the standing architectural rules and the measured findings behind them.
- **`docs/superpowers/specs/`** — design specs, including the rewrite design and the deployment
  design.
- **The GitHub issues named by number throughout `CLAUDE.md`** (#94, #116, #151, #203 …) —
  see `docs/agents/issue-tracker.md`.

A decision resolved from here on can go in `docs/adr/` as `0001-...md`; nothing needs
back-filling.

## File structure

Single-context repo (this repo, and most repos):

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
└── scrapers/
```

Multi-context repo (presence of `CONTEXT-MAP.md` at the root) — not applicable here:

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a
test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary
explicitly avoids.

This repo has a precise vocabulary already, whether or not a glossary file exists yet, and it is
worth honouring: *solicitation*, *award line*, *spine*, *keyspace*, *bridge*, *linking pass*,
*placeholder title*, *honeypot*. Several are load-bearing distinctions rather than style
preferences — an *award* is one line, not one document; a *bid price* is not an *award amount*;
*ground truth* means human-labelled and never machine-labelled.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing
language the project doesn't use (reconsider) or there's a real gap (note it for
`/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently
overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_

The same applies to `CLAUDE.md`'s standing rules, several of which are explicitly marked as
settled by measurement and not to be reopened without new evidence ("do not re-litigate those
without new evidence"). Contradicting one is allowed; doing it silently is not.
