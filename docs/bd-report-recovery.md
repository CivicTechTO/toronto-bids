# Systematic historical City bid recovery

The first implementation provides correctness repairs and a resumable evidence-capture job. It does not yet admit the captured 2013–2016 reports into model extraction or approve encoded Call Number aliases. The full plan and primary-source research are in [the investigation](research/systematic-city-bid-recovery-2026-10-31.md).

## Correctness gates

The historical supplier/value fallback keeps the original unique-candidate requirement. It rejects a candidate whose solicitation issue year follows the historical item, and requires a full supplier-name core match or a shared identifying word beyond generic industry/conjunction terms. Filtering a formerly ambiguous match cannot create a new join. These checks constrain the legacy fallback; they do not turn it into an independent publisher-identifier proof.

`data/city_identity_repairs.json` pins four explicit review contexts. Two are proven wrong-period/different-supplier joins (`2014.BD174.2` and `2015.BD25.1`); two are unresolved spelling/joint-venture variants that shared only generic words (`2014.BD184.7` and `2017.BA55.8`). The latter are withheld for review, not declared different procurements. Each review binds the source item/title, report URL/PDF SHA and target issue date. A changed context aborts the resolver transaction. A source report not downloaded yet may have a NULL database hash; once held, its hash must match the reviewed bytes.

Resolution moves only the unsupported `city_council_bid` key to a new identity, preserving City-document IDs and existing redirects. The new identity records the split proof and former shared ID in `merge_evidence`. An exact rejected derived title is cleared; publisher titles remain. Original observations, prices and source bytes are not rewritten by identity repair. Report/item associations are regenerated from the corrected keys, including items without stored bids.

The BD table parser separates recognized multiline HST header continuations and explicitly referenced trailing notes before validating every positional pair. It never skips a stray header after pairing. The real `2015.BD25.1` regression restores Bond Paving as the first supplier and preserves all eight verbatim prices with the correct firms and including-HST basis.

`store_bids` now re-derives each successfully parsed reference transactionally with `rebuild_rows`, restricted to `source='bid_award_panel'` and that reference. This replaces stale shifted keys instead of appending corrections beside them; unchanged keys retain their IDs and first-seen dates. A previously populated item that no longer parses completely, or yields fewer distinct observations, remains held and returns a review failure. `enrich-titles` surfaces those failures and exits nonzero. It does not silently erase an ambiguous item.

Full-archive parser calibration on 891 held agendas changes 53 references, with no BA-series changes. Five previously unreadable references yield rows; twelve previously emitted references now refuse ambiguous columns rather than manufacture alignment. Those twelve need source/PDF review; they are not approved data deletions. The production-copy rehearsal deliberately corrects only BD25.1, repairs/withholds the four reviewed joins and preserves other source facts.

## All-years evidence capture

From `scrapers/`:

```sh
uv run python -m toronto_bids.sources.bd_reports --database /path/to/bids.sqlite --directory /path/to/bd-reports --dry-run
uv run python -m toronto_bids.sources.bd_reports --database /path/to/bids.sqlite --directory /path/to/bd-reports
uv run python -m toronto_bids.sources.bd_reports --database /path/to/bids.sqlite --directory /path/to/bd-reports --resume
```

The initial inventory selects held database report URLs with `kind='bgrd'`, 2013–2016 BD references and no stored bid observations for their item. The research snapshot has 586 entries (187/123/158/118 by year). It covers all four years in one job; there is no per-year operator approval loop. `--dry-run` opens the database read-only and writes no inventory or files. `--limit N` bounds attempted downloads; an incomplete run exits 1.

`inventory.json` freezes source URL/reference pairs. Resuming validates every pair against the database before network use and retains entries even if later extraction adds bids. Source URLs are restricted to the official City BD report path. One advisory lock prevents concurrent processes from running the same inventory.

Requests are serial, spaced at least 1.1 seconds apart, and use bounded retries with exponential backoff and Retry-After. A long delay or persistent rate limit stops the batch and leaves remaining entries pending; it never moves to another URL to retry early. Resume later. A 403/404 remains a capture error, not proof of confidentiality or permanent removal.

Downloads use existing PDF capture/text facilities in a temporary directory. PDF signature and `pdfinfo` structure must validate before content-addressed bytes and metadata are stored. Held bytes are hash-verified on resume; missing/changed files and context changes are visible failures. A valid scanned PDF can be held without readable text; captured bytes do not imply successful extraction. Raw source kind stays `bgrd` and no bid/award/identity rows are written by capture.

This is a standalone historical job, not a new large nightly crawl. The command also holds the database directory’s `.pipeline.lock` while writing, coordinating with the existing nightly. Library callers own writer coordination; the inventory lock alone does not coordinate unrelated database workflows. The initial live capture runs on a private copy under `.validation-systematic-bids-2026-10-31/implementation/`, so production remains unchanged until reviewed deployment/promotion.

## Following stages

Admit historical BD reports to the shared batched Sonnet/cache path with contract-level source and partial-disclosure checks, then validate native Call Number crosswalks from independent source facts. Preserve lot/stage/JV structure and keep unsupported identities unresolved. The 669 held-bid candidates and 389 queued-report candidates from research are a planning inventory, not approved joins. Do not infer identity from digit stripping, right-padding, derived titles or equal prices.

After the historical path is validated, add complete committee agenda indexing and attachment-aware search against the demonstrated modern source set. Measure correctly identified newly covered solicitations and repaired relationships; file/name counts alone are not recovery metrics.
