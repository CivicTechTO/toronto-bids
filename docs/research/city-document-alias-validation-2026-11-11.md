# Published document alias validation — November 11, 2026

This local component adds one source-proven recovery to the cumulative exhaustive branch. It has not been deployed, pushed or opened as a standalone PR.

## Independent original identity

[The original City's call page](https://web.archive.org/web/20190615041306id_/https://wx.toronto.ca/inter/pmmd/calls.nsf/0/A82A07B374C7DA89852583F6006A9D14?OpenDocument) prints `Call number: 3721-19-0159` and, within its Description field, `See ARIBA Doc 1745310659`. Labelled dates are May 10 / June 14, 2019. The complete original UTF-8 HTML is embedded in the committed approval; its original byte SHA-256 is `0a10c92ffcab84cb37dee1f3aaa9380893e6c65afd92a9528f20f9c87e24c2c6`. Original control/newline bytes are retained rather than normalized.

An original award publication independently identifies current City document 3721190159 using exact supplier, printed award date and amount against complete frozen/current publisher populations. The call-page dates match its current publisher fields. No numeric conversion, rounded value or supplier-name alias establishes this relation. The alternative Doc has no competing current City solicitation record.

[The original BA40.8 report](https://www.toronto.ca/legdocs/mmis/2019/ba/bgrd/backgroundfile-136556.pdf), SHA-256 `04dc52bb84a73daeb8f5d1b721faa2a719f95aca09b330aa4fb1637621940259`, names three received bidders under Doc1745310659. All three bid rows were already stored. Canonical resolution now associates them with the legacy City record while preserving their original Doc, reference, IDs, prices, statuses and first-seen values. The City canonical ID is retained, with a redirect from the alternate identity. This adds no newly inferred bid row.

## Full production-copy result

A fresh production backup was resolved twice and exported with the final branch code. The private artifacts are `native-rehearsal.json`, `native-rehearsal.sqlite` and `native-bids.json` under `.validation-city-exhaustive-2026-11-10/`.

- Missing City award coverage: **3,316 → 3,248**, **68** cumulative recoveries (the prior 67 plus this alias).
- All **21,715** original bid IDs, payloads and first-seen values survive; the prior component's **227** new unpriced participation rows remain unchanged.
- All original source facts, City solicitation canonical IDs and prior immutable identity proofs survive. Source-based coverage and independently assigned canonical coverage agree; foreign keys pass.
- Every recovered solicitation exports its source report. The alias case exports all three original bid names and their original alternate Doc identifiers. All prior native participant names remain exact.
- Repeat resolution is stable. Full suite: **1,793 passed, 1 skipped**. Packaging contains the new module, exact original page/approval and all prior 67 native reviews.

The production-copy rehearsal initially exposed a real audit integration gap: source-based attribution did not read approved document aliases. A regression reproduced it. Attribution now reads the immutable applied proof independently of canonical IDs and still reports deliberately corrupted canonical attribution. Other tests cover wrong/rehashed source labels, different call/dates, competing identifiers, foreign provenance, removed/changed applied proofs, stable City IDs, equivalent reference labels, refusal of unproven anchor merges and rollback after a late conflict.

Production remains unchanged, with 3,316 unresolved. Zero cases are categorically unrecoverable. Additional primary-source discovery and the unsent bulk City request remain outside this component's completed scope.
