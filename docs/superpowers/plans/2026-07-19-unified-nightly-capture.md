# Unified Nightly Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tb nightly` capture everything that accrues live — folding the browser-bound Ariba-attachment and Zoo/TRCA/EP board-report captures (and a monthly council pass) into one isolated run before the export — while retiring the dead `enrich-titles --scrape` path and preserving the 891 final Bid Award Panel agendas as a data asset.

**Architecture:** Orchestration stays in Python inside `_cmd_nightly` so the Slack summary keeps computing from real `db.counts()` deltas. Each new capture is wrapped in its own `try/except` exactly as the existing sync/award-summary/portal steps are — a browser failure records to the failures list and never stops sync, export, or publish. Agency-body capture is extracted into one helper shared by `_cmd_nightly` and `_cmd_enrich_agencies` (DRY). The deploy side loses the separate Ariba timer, raises the nightly timeout, and gains an idempotent agenda-archive upload in `publish-data.sh`.

**Tech Stack:** Python 3.12, `uv`, pytest (offline, fixture-based, `monkeypatch`). Bash for `deploy/*.sh`. systemd user units. `gh` CLI for release uploads.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-19-unified-nightly-capture-design.md` — every task implements part of it.
- **No lint/format/typecheck exists** — do not invent `ruff`/`mypy`/`black` commands. The only check is `uv run pytest`, run from `scrapers/`.
- All Python work is under `scrapers/`; run `cd scrapers` (or use `uv run --project scrapers`) for every test command.
- **Per-step isolation is load-bearing:** a new nightly step must be in its own `try/except` that appends `(name, str(exc))` to `failures` and lets later steps run. Never let a capture failure stop the export or publish.
- **Browser steps in the nightly pass `virtual_display=True` unconditionally** (unattended server path).
- **The dead slice is narrow:** remove only the `enrich-titles --scrape` trigger and the BA/BD `term_starts` default/constant. Do NOT touch `scrape_agendas`/`discover_meetings`/`cached_agendas`/`parse_agenda_pdfs` (Zoo/EP import them) or any offline ingest (`store_bids`, `parse_bid_tables`, composite/pre-Ariba matching).
- Commit trailers on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE
  ```
- Branch is `feat-unified-nightly-capture` (already checked out). Do not commit to `main`.

## File Structure

- `scrapers/toronto_bids/cli.py` — remove `enrich-titles --scrape`/`--virtual-display` args + scrape branch (Task 1); add `_capture_agency_bodies` helper + refactor `_cmd_enrich_agencies` (Task 2); add browser + monthly-council + supplier steps to `_cmd_nightly` (Task 3).
- `scrapers/toronto_bids/sources/bid_award_panel.py` — remove `TERM_STARTS`, require `term_starts` on `discover_meetings`/`scrape_agendas` (Task 1).
- `scrapers/toronto_bids/store/db.py` — add `ariba_attachment` to `counts()` (Task 4).
- `scrapers/toronto_bids/notify.py` — extend `summarize` with agency/attachment deltas (Task 4).
- `scrapers/tests/test_bid_award_panel.py`, `tests/test_nightly.py`, `tests/test_notify.py` (may be new), `tests/test_cli_agencies.py` (new) — tests.
- `deploy/publish-data.sh` — idempotent `council-agendas` archive guard (Task 5).
- `deploy/tb-nightly.service` — `TimeoutStartSec=3h` (Task 6).
- `deploy/tb-ariba-attachments.{service,timer}` — deleted (Task 6).
- `deploy/README.md` — new nightly scope, retired timer, provisioning path (Tasks 5, 6).

---

## Task 1: Retire the dead BA/BD scrape path

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (enrich-titles argparse ~lines 28-37, 78-82; `_cmd_enrich_titles` ~lines 192-225)
- Modify: `scrapers/toronto_bids/sources/bid_award_panel.py` (`TERM_STARTS` ~line 193; `discover_meetings` ~line 203; `scrape_agendas` ~line 309)
- Test: `scrapers/tests/test_bid_award_panel.py` (~line 199-208), `scrapers/tests/test_cli_titles.py` (new)

**Interfaces:**
- Produces: `discover_meetings(fetch, log=..., max_per_term=260, stop_after_misses=4, *, term_starts)` and `scrape_agendas(agenda_dir, *, virtual_display=False, log=..., term_starts)` — `term_starts` now required (keyword-only, no default). `TERM_STARTS` no longer exists. `enrich-titles` no longer accepts `--scrape`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing test — enrich-titles rejects --scrape, and scrape_agendas requires term_starts**

Create `scrapers/tests/test_cli_titles.py`:

```python
import pytest

from toronto_bids import cli


def test_enrich_titles_no_longer_accepts_scrape():
    # The Bid Award Panel is abolished; the scrape path is removed. argparse must reject it.
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["enrich-titles", "--scrape"])


def test_scrape_agendas_requires_explicit_term_starts():
    from toronto_bids.sources.bid_award_panel import scrape_agendas
    with pytest.raises(TypeError):
        scrape_agendas("/tmp/whatever")  # no term_starts -> TypeError, not a BA/BD default
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_cli_titles.py -v`
Expected: FAIL — `--scrape` is still accepted (no SystemExit); `scrape_agendas` still has a default.

- [ ] **Step 3: Remove the `--scrape` and `--virtual-display` args from the enrich-titles parser**

In `cli.py`, delete these lines from `build_parser` (the two `p_titles.add_argument` calls for `--scrape` and `--virtual-display`, currently ~32-37):

```python
    p_titles.add_argument(
        "--scrape", action="store_true",
        help="Fetch Bid Award Panel agendas first (headed browser; ~10 min on a cold "
             "cache, seconds once cached). Without it, only agendas already on disk are used")
    p_titles.add_argument("--virtual-display", action="store_true",
                          help="Run the headed browser under Xvfb (implies --scrape's needs)")
```

Keep the `enrich-titles` parser itself and its `--reports` argument (plain HTTP, still valid). Update the `enrich-titles` help string to drop "(offline unless --scrape)":

```python
    p_titles = sub.add_parser(
        "enrich-titles",
        help="Recover titles the City never published, from the cached Bid Award Panel "
             "agendas and the legacy archive (offline; the Panel was abolished 2025-10-01 so "
             "the cached corpus is final)")
```

- [ ] **Step 4: Remove the scrape branch in `_cmd_enrich_titles`**

In `_cmd_enrich_titles`, replace the scrape/cached branch (currently ~212-220):

```python
        if args.scrape:
            agendas = scrape_agendas(config.COUNCIL_AGENDAS_DIR,
                                     virtual_display=args.virtual_display,
                                     log=lambda m: print(m, flush=True))
        else:
            agendas = cached_agendas(config.COUNCIL_AGENDAS_DIR)
            if not agendas:
                print(f"No cached agendas in {config.COUNCIL_AGENDAS_DIR} — "
                      f"run with --scrape to fetch them (needs the 'council' extra).")
```

with the offline-only version:

```python
        agendas = cached_agendas(config.COUNCIL_AGENDAS_DIR)
        if not agendas:
            print(f"No cached agendas in {config.COUNCIL_AGENDAS_DIR} — download the "
                  f"council-agendas archive from the data release and unpack it there "
                  f"(deploy/README.md).")
```

Then remove `scrape_agendas` from the import block at the top of `_cmd_enrich_titles` (the `from toronto_bids.sources.bid_award_panel import (...)` list) and update the line that prints `({'scraped' if args.scrape else 'cached'})` to just `(cached)`:

```python
            print(f"Bid Award Panel agendas: {len(agendas)} (cached)")
```

- [ ] **Step 5: Require `term_starts` in the prober and remove `TERM_STARTS`**

In `bid_award_panel.py`, delete the `TERM_STARTS = [...]` constant block (~line 193). Change the two signatures so `term_starts` is keyword-only and required:

```python
def discover_meetings(fetch, log=lambda _m: None, max_per_term=260, stop_after_misses=4,
                      *, term_starts):
```

```python
def scrape_agendas(agenda_dir, *, virtual_display=False, log=lambda _m: None,
                   term_starts) -> dict:
```

Update both docstrings: replace the sentences that say `term_starts` "defaults to TERM_STARTS (the BA/BD series)" with "`term_starts` is the committee's term list (e.g. `zoo_board.ZB_TERM_STARTS`); the Bid Award Panel is abolished, so no in-repo default remains."

Verify the live callers already pass it as a keyword (they do): `zoo_board.scrape_zb_agendas` calls `scrape_agendas(config.ZOO_AGENDAS_DIR, virtual_display=..., log=..., term_starts=ZB_TERM_STARTS)` and `ep_board.scrape_ep_agendas` similarly. **Do not change those.** Confirm no other caller of `scrape_agendas` passes `agenda_dir, virtual_display` positionally — if any does, it will now break on the keyword-only `virtual_display`; grep: `grep -rn "scrape_agendas(" toronto_bids tests`.

- [ ] **Step 6: Fix the one test that imports `TERM_STARTS`**

In `tests/test_bid_award_panel.py`, delete `test_a_terms_meetings_do_not_always_start_at_one` (it asserts against the removed constant and the retired BA/BD scrape data; the `first_n` mechanism it documented is dead now that no live caller uses `first_n != 1`). Leave every other test in the file untouched.

- [ ] **Step 7: Run tests**

Run: `cd scrapers && uv run pytest tests/test_cli_titles.py tests/test_bid_award_panel.py tests/test_zoo_reports.py tests/test_ep_reports.py -v`
Expected: PASS (new tests green; agency + panel tests still green).

- [ ] **Step 8: Full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS (count drops by one vs baseline from the deleted TERM_STARTS test, plus two new tests → net +1).

- [ ] **Step 9: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/toronto_bids/sources/bid_award_panel.py scrapers/tests/test_cli_titles.py scrapers/tests/test_bid_award_panel.py
git commit -m "refactor: retire the dead enrich-titles --scrape path (#unified-nightly)

The Bid Award Panel was abolished 2025-10-01; the 891 cached agendas are the
final corpus, so re-scraping BA/BD can never find a new agenda. Remove the
--scrape trigger and the BA/BD term_starts default+constant; keep the shared
prober (Zoo/EP pass their own term_starts) and all offline ingest.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 2: Extract `_capture_agency_bodies` and refactor `_cmd_enrich_agencies`

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (`_cmd_enrich_agencies` ~lines 489-579)
- Test: `scrapers/tests/test_cli_agencies.py` (new)

**Interfaces:**
- Produces: `_capture_agency_bodies(conn, ids, *, bodies, fetch, scrape, virtual_display, out) -> list[tuple[str, str]]` — runs the TRCA/Zoo/EP board-report capture for each body in `bodies`, each isolated; returns a list of `(name, error)` failures. `ids` is the dict from `seed_buyers(conn)`. Does NOT do the portal step or the supplier rebuild (callers own those).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

Create `scrapers/tests/test_cli_agencies.py`:

```python
from toronto_bids import cli
from toronto_bids.buyers import seed_buyers


def test_capture_agency_bodies_isolates_a_failing_body(conn, monkeypatch):
    # TRCA raises; Zoo and EP still run and the failure is reported, not raised.
    ids = seed_buyers(conn)
    import toronto_bids.sources.trca_board as trca
    import toronto_bids.sources.zoo_board as zoo
    import toronto_bids.sources.ep_board as ep

    monkeypatch.setattr(trca, "store_trca_reports", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(zoo, "cached_zb_agendas", lambda *a, **k: {})
    monkeypatch.setattr(zoo, "store_zoo_reports", lambda *a, **k: {"solicitations": 0, "awards": 0})
    monkeypatch.setattr(ep, "cached_ep_agendas", lambda *a, **k: {})
    monkeypatch.setattr(ep, "store_ep_reports", lambda *a, **k: {"solicitations": 0, "awards": 0, "bids": 0})

    failures = cli._capture_agency_bodies(
        conn, ids, bodies=["trca", "zoo", "ep"],
        fetch=False, scrape=False, virtual_display=False, out=lambda _m: None)

    assert [name for name, _ in failures] == ["trca"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_cli_agencies.py -v`
Expected: FAIL — `cli._capture_agency_bodies` does not exist (AttributeError).

- [ ] **Step 3: Add the helper**

In `cli.py`, add this module-level function above `_cmd_enrich_agencies`. It is the three body-blocks lifted verbatim from the current `_cmd_enrich_agencies`, parameterised:

```python
def _capture_agency_bodies(conn, ids, *, bodies, fetch, scrape, virtual_display, out):
    """Capture TRCA/Zoo/EP board-report awards+bids, each body isolated. Returns failures.

    Shared by `tb enrich-agencies` and `tb nightly`. TRCA is plain HTTP (eSCRIBE); Zoo and EP
    need a headed browser for TMMIS discovery, so `scrape`/`virtual_display` apply to them.
    Does not run the portal step or the supplier rebuild — the caller owns those.
    """
    failures: list[tuple[str, str]] = []

    if "trca" in bodies:
        try:
            from toronto_bids.sources.trca_board import download_reports, store_trca_reports
            if fetch:
                http = HttpClient()
                try:
                    print(f"  trca reports fetched : {download_reports(conn, http, log=out)}")
                finally:
                    http.close()
            got = store_trca_reports(conn, ids["trca"])
            print(f"  trca stored          : {got['solicitations']} solicitations, "
                  f"{got['awards']} awards, {got['bids']} bids")
        except Exception as exc:
            failures.append(("trca", str(exc)))

    if "zoo" in bodies:
        try:
            from toronto_bids.sources.zoo_board import (
                cached_zb_agendas, download_zoo_reports, scrape_zb_agendas, store_zoo_reports)
            agendas = (scrape_zb_agendas(virtual_display=virtual_display, log=out)
                       if scrape else cached_zb_agendas())
            print(f"  zoo ZB agendas       : {len(agendas)}"
                  f" ({'scraped' if scrape else 'cached'})")
            if agendas and (fetch or scrape):
                http = HttpClient()
                try:
                    print(f"  zoo reports fetched  : "
                          f"{download_zoo_reports(conn, http, agendas, log=out)}")
                finally:
                    http.close()
            got = store_zoo_reports(conn, ids["toronto-zoo"])
            print(f"  zoo stored           : {got['solicitations']} solicitations, "
                  f"{got['awards']} awards")
        except Exception as exc:
            failures.append(("zoo", str(exc)))

    if "ep" in bodies:
        try:
            from toronto_bids.sources.ep_board import (
                cached_ep_agendas, download_ep_reports, scrape_ep_agendas, store_ep_reports)
            agendas = (scrape_ep_agendas(virtual_display=virtual_display, log=out)
                       if scrape else cached_ep_agendas())
            print(f"  ep EP agendas        : {len(agendas)}"
                  f" ({'scraped' if scrape else 'cached'})")
            if agendas and (fetch or scrape):
                http = HttpClient()
                try:
                    print(f"  ep reports fetched   : "
                          f"{download_ep_reports(conn, http, agendas, log=out)}")
                finally:
                    http.close()
            got = store_ep_reports(conn, ids["exhibition-place"])
            print(f"  ep stored            : {got['solicitations']} solicitations, "
                  f"{got['awards']} awards, {got['bids']} bids")
        except Exception as exc:
            failures.append(("ep", str(exc)))

    return failures
```

- [ ] **Step 4: Refactor `_cmd_enrich_agencies` to call the helper**

Replace the three body-blocks (the `if "trca" in bodies:` … through the end of the `if "ep" in bodies:` block) in `_cmd_enrich_agencies` with a single call, keeping the portal and supplier steps as they are:

```python
        ids = seed_buyers(conn)
        bodies = [args.only] if args.only else ["trca", "zoo", "ep"]

        failures.extend(_capture_agency_bodies(
            conn, ids, bodies=bodies, fetch=args.fetch, scrape=args.scrape,
            virtual_display=args.virtual_display, out=out))

        if args.portal:
            ...  # unchanged
        try:
            print(f"  suppliers            : {build_supplier_dimension(conn)}")
        ...  # unchanged
```

- [ ] **Step 5: Run tests**

Run: `cd scrapers && uv run pytest tests/test_cli_agencies.py tests/test_agencies.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_cli_agencies.py
git commit -m "refactor: extract _capture_agency_bodies for reuse by the nightly

No behaviour change to `tb enrich-agencies`; the three isolated body-blocks
become one helper the nightly will also call.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 3: Fold the browser captures + monthly council into `_cmd_nightly`

**Files:**
- Modify: `scrapers/toronto_bids/cli.py` (`_cmd_nightly` ~lines 386-486; add `_is_first_of_month` helper)
- Test: `scrapers/tests/test_nightly.py` (fixture ~lines 14-27; new tests)

**Interfaces:**
- Consumes: `_capture_agency_bodies` (Task 2).
- Produces: `_is_first_of_month() -> bool` (test seam for the monthly council gate).

- [ ] **Step 1: Write the failing tests**

Add to `scrapers/tests/test_nightly.py`. First extend the `nightly` fixture to stub the new capture calls (so the default nightly stays offline), then add the isolation + gating tests:

In the `nightly` fixture, after the existing `monkeypatch.setattr(bids_tenders, ...)` line, add:

```python
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: False)
    from toronto_bids.sources import council as council_src
    monkeypatch.setattr(council_src, "enrich_council", lambda *a, **k: 0)
```

New tests:

```python
def test_a_raising_ariba_attachment_step_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    from toronto_bids.sources import ariba_attachments
    monkeypatch.setattr(ariba_attachments, "capture_attachments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser died")))
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_a_raising_agency_capture_does_not_stop_the_export(nightly, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_capture_agency_bodies",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tmmis blocked")))
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_an_agency_body_failure_is_recorded_but_export_still_runs(nightly, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [("zoo", "boom")])
    assert nightly() == 1
    assert (tmp_path / "export" / "bids.json").exists()


def test_council_runs_only_on_the_first_of_the_month(nightly, monkeypatch):
    calls = []
    from toronto_bids.sources import council as council_src
    monkeypatch.setattr(council_src, "enrich_council", lambda *a, **k: calls.append(1) or 0)
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: False)
    nightly()
    assert calls == []            # not the 1st -> council skipped
    monkeypatch.setattr(cli, "_is_first_of_month", lambda: True)
    nightly()
    assert calls == [1]           # the 1st -> council runs
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_nightly.py -v`
Expected: FAIL — `cli._is_first_of_month` and the new steps don't exist; the fixture patches error (`_capture_agency_bodies` exists from Task 2, but `_is_first_of_month` does not).

- [ ] **Step 3: Add the `_is_first_of_month` helper**

In `cli.py`, add near the top (after imports):

```python
def _is_first_of_month() -> bool:
    """The monthly-council gate for the nightly (a test seam)."""
    from datetime import date
    return date.today().day == 1
```

- [ ] **Step 4: Add the capture steps to `_cmd_nightly`**

In `_cmd_nightly`, inside the `if http is not None:` block, immediately after the portal `try/except` (the block ending `failures.append(("portal", str(exc)))`) and BEFORE the `finally: http.close()`, insert the browser captures. Then after the `finally: http.close()` and BEFORE the export, add the supplier rebuild. Concretely, insert after the portal block:

```python
                try:
                    from toronto_bids.sources import ariba_attachments as aa
                    n = aa.capture_attachments(conn, log=out, virtual_display=True)
                    print(f"  ariba attachments    : {n} bundles captured")
                except Exception as exc:
                    failures.append(("ariba_attachments", str(exc)))
                try:
                    from toronto_bids.buyers import seed_buyers
                    ids = seed_buyers(conn)
                    failures.extend(_capture_agency_bodies(
                        conn, ids, bodies=["trca", "zoo", "ep"],
                        fetch=True, scrape=True, virtual_display=True, out=out))
                except Exception as exc:
                    failures.append(("agencies", str(exc)))
                if _is_first_of_month():
                    try:
                        from functools import partial
                        from toronto_bids.sources.council import (
                            enrich_council, fetch_agenda_item)
                        fetch = partial(fetch_agenda_item, virtual_display=True)
                        print(f"  council enriched     : "
                              f"{enrich_council(conn, http, fetch=fetch)}")
                    except Exception as exc:
                        failures.append(("council", str(exc)))
```

Then, after the `finally: http.close()` block and before `written = export_json(...)`, insert the supplier rebuild (it must run after the agency captures so new suppliers land, and it needs only `conn`):

```python
        try:
            from toronto_bids.linking.supplier import build_supplier_dimension
            print(f"  suppliers            : {build_supplier_dimension(conn)}")
        except Exception as exc:
            failures.append(("supplier_linking", str(exc)))
```

Update the `_cmd_nightly` docstring's first line to: `"""The whole unattended run: sync -> award summaries -> portal -> ariba attachments -> agency board reports -> (monthly) council -> supplier rebuild -> export -> tell Slack."""`

- [ ] **Step 5: Run tests**

Run: `cd scrapers && uv run pytest tests/test_nightly.py -v`
Expected: PASS (all new isolation + gating tests green; existing nightly tests still green).

- [ ] **Step 6: Full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/cli.py scrapers/tests/test_nightly.py
git commit -m "feat(nightly): capture ariba attachments + agency board reports + monthly council

Folds the browser-bound captures into `tb nightly` before the export, each
isolated (a browser failure records and never stops sync/export/publish).
Council is gated to the 1st of the month; supplier dimension rebuilds after
the agency captures so new winners/bidders land.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 4: Surface the new captures in the Slack summary

**Files:**
- Modify: `scrapers/toronto_bids/store/db.py` (`counts` ~line 252)
- Modify: `scrapers/toronto_bids/notify.py` (`summarize` ~line 40)
- Test: `scrapers/tests/test_notify.py` (add/extend)

**Interfaces:**
- Consumes: `db.counts` now includes `ariba_attachment`.
- Produces: `summarize` line includes non-zero agency-award, agency-bid, and ariba-attachment deltas.

- [ ] **Step 1: Write the failing test**

Read the existing `summarize` to match its exact format first: `cd scrapers && sed -n '40,60p' toronto_bids/notify.py`. Then add to `scrapers/tests/test_notify.py` (create if absent):

```python
from toronto_bids import notify


def _counts(**kw):
    base = {k: 0 for k in ("solicitation", "award", "bid", "agency_award",
                           "agency_bid", "ariba_attachment")}
    base.update(kw)
    return base


def test_summary_shows_agency_and_attachment_growth():
    before = _counts(agency_award=100, agency_bid=200, ariba_attachment=1000)
    after = _counts(agency_award=107, agency_bid=215, ariba_attachment=1111)
    text = notify.summarize(before, after, [], 9, 31_000_000, 12.0)
    assert "agency awards 107 (+7)" in text
    assert "agency bids 215 (+15)" in text
    assert "ariba files 1,111 (+111)" in text


def test_summary_omits_zero_growth_agency_and_attachment_lines():
    c = _counts(solicitation=10)
    text = notify.summarize(c, c, [], 9, 1000, 1.0)
    assert "agency awards" not in text  # nothing captured -> no noise
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scrapers && uv run pytest tests/test_notify.py -v`
Expected: FAIL — the summary doesn't include the agency/attachment segments.

- [ ] **Step 3: Add `ariba_attachment` to `db.counts`**

In `db.py`, add `"ariba_attachment"` to the `tables` list in `counts()`:

```python
    tables = ["solicitation", "award", "noncompetitive", "ariba_posting",
              "suspended_firm", "supplier", "capital_project", "bid", "council_item",
              "background_pdf", "composite_award", "sync_run", "buyer",
              "agency_solicitation", "agency_award", "agency_bid", "ariba_attachment"]
```

- [ ] **Step 4: Extend `summarize`**

Read the current `summarize` body. It builds a segment string from before/after deltas. Add three optional segments after the existing ones, each emitted only when the after-count is present and the delta or value is non-zero. Use the existing helper/format the function already uses for `solicitations N (+d)` (match its `f"{v:,}"` thousands style and delta formatting). Add, in the same style the function already uses to assemble parts:

```python
    def _seg(label, key):
        a = after.get(key)
        if not a:
            return None
        d = a - before.get(key, 0)
        return f"{label} {a:,} (+{d:,})" if d else None

    extra = [s for s in (_seg("agency awards", "agency_award"),
                         _seg("agency bids", "agency_bid"),
                         _seg("ariba files", "ariba_attachment")) if s]
```

Then append `extra` segments into the same `·`-joined parts list the function returns (match the exact join separator and ordering the existing code uses — insert them alongside the `bids …` / `export …` segments). Do not change the failure/❌ line format.

- [ ] **Step 5: Run tests**

Run: `cd scrapers && uv run pytest tests/test_notify.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `cd scrapers && uv run pytest -q`
Expected: PASS. (The export's `meta.counts` now carries `ariba_attachment`; if `tests/test_export_document.py` asserts the exact key set of `counts`, update that assertion — grep `ariba_attachment` and `counts` in tests to confirm.)

- [ ] **Step 7: Commit**

```bash
git add scrapers/toronto_bids/store/db.py scrapers/toronto_bids/notify.py scrapers/tests/test_notify.py
git commit -m "feat(nightly): surface agency + ariba-attachment growth in the Slack summary

Add ariba_attachment to db.counts and three non-zero-only segments to the
summary so the nightly line reflects what the new captures added.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 5: Preserve the 891 agendas — idempotent archive upload

**Files:**
- Modify: `deploy/publish-data.sh` (after the `latest` upload / before the frontend trigger)
- Modify: `deploy/README.md` (provisioning path)

**Interfaces:**
- Consumes: the cached agenda dir at `<TB_DATA_DIR>/council/agendas/`.
- Produces: an immutable `council-agendas` release on `$TB_DATA_REPO` carrying `council-agendas.zip`, uploaded once. Overridable env: `TB_AGENDAS_DIR` (default `$DATA_DIR/council/agendas`).

- [ ] **Step 1: Add the idempotent archive guard to `publish-data.sh`**

In `deploy/publish-data.sh`, after the `latest` upload block (step 5) and before the snapshot block (step 6), insert:

```bash
# 5b. Preserve the FINAL Bid Award Panel agenda corpus as an immutable data asset (#unified-
#     nightly). The Panel is abolished; these 891 pages never change, so upload once and skip
#     forever after — a cheap `release view` check per night, no re-upload. Best-effort: the
#     data publish above is the deliverable; a missing agenda archive must not fail the run.
AGENDAS_DIR="${TB_AGENDAS_DIR:-$DATA_DIR/council/agendas}"
if gh_run release view council-agendas -R "$DATA_REPO" >/dev/null 2>&1; then
  echo "publish-data: council-agendas archive already present — skipping"
elif [ -d "$AGENDAS_DIR" ] && [ -n "$(ls -A "$AGENDAS_DIR" 2>/dev/null)" ]; then
  AGENDAS_ZIP="$EXPORT_DIR/council-agendas.zip"
  ( cd "$AGENDAS_DIR" && zip -q -r -X "$AGENDAS_ZIP" . ) \
    && gh_run release create council-agendas -R "$DATA_REPO" \
         --title "Bid Award Panel agenda corpus (final)" \
         --notes "The 891 cached Bid Award Panel / Bid Committee agendas — the complete, final corpus (the Panel was abolished 2025-10-01). Unpack into <DATA_DIR>/council/agendas/ and run 'tb enrich-titles' to re-derive titles/bids/composite. Immutable." \
         "$AGENDAS_ZIP" \
    || echo "publish-data: WARNING — could not publish the council-agendas archive (data is published)" >&2
else
  echo "publish-data: WARNING — no cached agendas at $AGENDAS_DIR to archive" >&2
fi
```

Note: in `TB_PUBLISH_DRY_RUN=1`, `gh_run release view` echoes and returns 0, so the guard prints "already present — skipping". To exercise the create path in dry-run, the reviewer can point `TB_DATA_REPO` at a name and temporarily test; this mirrors the existing dry-run limitation for the `latest` create branch.

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/publish-data.sh && echo OK`
Expected: `OK`.

- [ ] **Step 3: Dry-run over the real cached agendas (plexbox)**

Run: `cd /home/alex/toronto-bids && TB_PUBLISH_DRY_RUN=1 TB_DATA_DIR="$HOME/tb-data" TB_PUBLISH_DAY=15 deploy/publish-data.sh 2>&1 | grep -iE "council-agendas|generated_at|done"`
Expected: prints the `export generated_at=…`, a `council-agendas` line (skipping, since dry-run `release view` returns 0), and `publish-data: done`, exit 0.

- [ ] **Step 4: Document the provisioning path in `deploy/README.md`**

In the "Publishing the export" section, add a subsection:

```markdown
### Provisioning a fresh box from the archives

The DB itself is published (`bids.sqlite` on the `latest` release), so a new box can start from
it directly. To rebuild the *derived* Bid Award Panel data from primary sources instead (the
`enrich-titles --scrape` path was retired — the Panel is abolished and the corpus is final),
download the agenda archive and re-derive offline:

```shell
gh release download council-agendas -R CivicTechTO/toronto-bids-data -D /tmp/agendas
mkdir -p ~/tb-data/council/agendas
unzip -q -o /tmp/agendas/council-agendas.zip -d ~/tb-data/council/agendas
cd ~/toronto-bids/scrapers && TB_DATA_DIR=~/tb-data uv run tb enrich-titles   # offline, no browser
```
```

- [ ] **Step 5: Commit**

```bash
git add deploy/publish-data.sh deploy/README.md
git commit -m "feat(deploy): archive the final 891 Bid Award Panel agendas as a data asset

publish-data.sh idempotently uploads council-agendas.zip to an immutable
council-agendas release (once; a cheap release-view check thereafter). Replaces
the retired --scrape as the way to obtain the primary-source corpus; README
documents the offline re-derive path.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Task 6: Deployment wiring — retire the Ariba timer, raise the timeout, update docs

**Files:**
- Delete: `deploy/tb-ariba-attachments.service`, `deploy/tb-ariba-attachments.timer`
- Modify: `deploy/tb-nightly.service` (`TimeoutStartSec`)
- Modify: `deploy/README.md`

**Interfaces:** none (deploy-only).

- [ ] **Step 1: Raise the nightly timeout**

In `deploy/tb-nightly.service`, change:

```
TimeoutStartSec=30m
```
to
```
# Browser captures (Ariba attachments cold sweep ~1-2h; agency scrapes incremental) now run
# in-line before the export, so the nightly can be long. 3h is a generous ceiling (#unified-nightly).
TimeoutStartSec=3h
```

Update the unit `Description=` to `toronto-bids nightly: sync, enrich (award summaries, ariba attachments, agency board reports, monthly council), export, publish`.

- [ ] **Step 2: Delete the retired Ariba timer + service**

```bash
git rm deploy/tb-ariba-attachments.service deploy/tb-ariba-attachments.timer
```

- [ ] **Step 3: Update `deploy/README.md`**

- In the nightly overview, list the new in-line captures (Ariba attachments, Zoo/TRCA/EP board reports, monthly council).
- Delete/rewrite the "## Ariba document capture (opt-in, headed browser under Xvfb) — #122" section: its **prerequisites** (Xvfb, `uv sync --extra council`, `playwright install chromium`, `install-deps`, Ariba creds in `tb.env`) are now **required by the nightly**, so move that prerequisites block up into the main setup as "Browser prerequisites (now required — the nightly drives a headed browser)". Remove the separate timer install/enable steps for `tb-ariba-attachments`.
- In "What does NOT run here", remove `enrich-titles --scrape` (the flag no longer exists) and keep `enrich-council` note updated to "runs monthly inside the nightly (gated to the 1st)".

- [ ] **Step 4: Verify no dangling references**

Run: `grep -rn "tb-ariba-attachments\|enrich-titles --scrape\|--scrape" deploy/ | grep -v README` and `grep -rn "tb-ariba-attachments" deploy/README.md`
Expected: no unit files reference the deleted timer; README mentions it only in the "retired" context if at all. Fix any stragglers.

- [ ] **Step 5: Commit**

```bash
git add deploy/tb-nightly.service deploy/README.md
git rm --cached deploy/tb-ariba-attachments.service deploy/tb-ariba-attachments.timer 2>/dev/null || true
git commit -m "deploy: retire the separate ariba-attachments timer; nightly does it all

The Ariba attachment capture is now an in-line nightly step, so its timer is
removed and its browser prerequisites become required nightly prerequisites.
Raise TimeoutStartSec 30m -> 3h for the longer browser-driven run.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01B9GFHCLueSNypaFqkgpPRE"
```

---

## Post-implementation: live verification on plexbox (per the #136/#138 discipline)

Not a code task — do this after all tasks merge and the checkout is on the updated `main`:

1. `cp deploy/tb-nightly.service ~/.config/systemd/user/ && systemctl --user daemon-reload`
2. `systemctl --user disable --now tb-ariba-attachments.timer` (retire the running timer on the box).
3. `systemctl --user start tb-nightly.service` and watch `journalctl --user -u tb-nightly -f`.
4. Confirm from the journal: every step ran; any browser failure was isolated (recorded, export + publish still completed); the Slack line shows agency/attachment growth; and `gh release view council-agendas -R CivicTechTO/toronto-bids-data` exists.

---

## Self-Review

**Spec coverage:**
- §2 nightly order (sync→award→portal→ariba→agencies→council→supplier→export→publish) → Tasks 3 (steps) + existing publish wrapper. ✓
- §2 monthly council gating → Task 3 (`_is_first_of_month`). ✓
- §2 retire `tb-ariba-attachments.timer` → Task 6. ✓
- §3 isolation of every new step → Task 3 tests. ✓
- §3 browser steps `virtual_display=True` → Task 3. ✓
- §3 timeout 30m→3h, Slack summary extended → Task 6, Task 4. ✓
- §4 remove `--scrape` + BA/BD `term_starts` default/constant, keep prober + offline ingest → Task 1. ✓
- §5 preserve 891 agendas as immutable asset + idempotent guard + README provisioning → Task 5. ✓
- §8 tests (isolation, no-scrape, required term_starts, archive guard, live) → Tasks 1,3,4,5 + post-impl. ✓

**Placeholder scan:** No TBD/TODO; every code step shows the code. The `summarize` extension (Task 4 step 4) references "match the existing join/format" — mitigated by requiring the implementer read the function first (step 1) and giving the exact `_seg` helper and expected output strings in the test.

**Type consistency:** `_capture_agency_bodies(conn, ids, *, bodies, fetch, scrape, virtual_display, out) -> list[tuple[str,str]]` defined in Task 2, consumed identically in Task 3. `_is_first_of_month() -> bool` defined and used in Task 3. `capture_attachments(conn, log, virtual_display)` and `enrich_council(conn, http, fetch=)` match the real signatures read from source. `db.counts` key `ariba_attachment` added in Task 4 and consumed by `summarize` + tests.
