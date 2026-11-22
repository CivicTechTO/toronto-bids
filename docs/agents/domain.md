# Domain docs

How engineering tools and skills should use this repository's documentation.

**Layout:** Single-context repository (`scrapers/` is a single `uv`-managed Python package, so there is no `CONTEXT-MAP.md`).

## Reading order

1. **`CLAUDE.md`** at the repo root: core commands, architecture, invariants, gotchas, and an index of subsystem guides under `docs/`.
2. **`GLOSSARY.md`** at the repo root: standard archive terminology.
3. **`docs/adr/`**: architecture decision records covering the area you are editing (such as `0001-scoped-bid-responses.md`).
4. **`CONTEXT.md`** at the repo root, if present. If absent, continue without creating one upfront.

Additional architectural decisions live in `docs/designs/` and in the numbered GitHub issues referenced throughout `CLAUDE.md` (see `docs/agents/issue-tracker.md`). Record new decisions in `docs/adr/` (`NNNN-slug.md`).

## Vocabulary

Use the terms defined in `GLOSSARY.md` consistently in issue titles, code, tests, and docs: *participation*, *bid scope*, *submission*, *response observation*, *price variant*, *solicitation*, *spine*, *award line*, *keyspace*, *linking pass*, *placeholder title*, and *ground truth*.

Key distinctions to keep clear:

- An *award* row is one award line, not one document.
- A *bid price* is not an *award amount*.
- *Ground truth* means human-labelled data (`docs/ground-truth/`), never machine labels (`docs/machine-labels/`).

## Flagging conflicts with existing decisions

If a proposal contradicts an existing ADR or a standing rule in `CLAUDE.md` (especially findings backed by issue measurements), call it out explicitly rather than overriding it silently:

> _Contradicts ADR-0001 (scoped bid responses): proposing to change because..._
