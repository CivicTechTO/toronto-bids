CREATE TABLE IF NOT EXISTS solicitation (
    document_number      TEXT PRIMARY KEY,
    status               TEXT,
    rfx_type             TEXT,
    noip_type            TEXT,
    form_type            TEXT,
    title                TEXT,
    description          TEXT,
    issue_date           TEXT,
    submission_deadline  TEXT,
    category             TEXT,
    division             TEXT,
    buyer_name           TEXT,
    buyer_email          TEXT,
    buyer_phone          TEXT,
    wards                TEXT,
    ariba_posting_link   TEXT,
    odata_id             TEXT,
    source               TEXT,
    -- Where the TITLE came from, when the City did not publish one:
    -- 'bid_award_panel' | 'legacy_ariba_html' | 'council_pre_ariba'. NULL = the City's feed.
    --
    -- Separate from `source` deliberately. `source` records which source last wrote the ROW,
    -- and the OData spine owns that: it re-upserts every row on every sync with
    -- overwrite=True, so anything a title pass writes to `source` is clobbered by the next
    -- sync — while the title itself survives, because COALESCE only guards NULL. Storing
    -- title provenance there made 890 recovered titles silently claim to come from the City
    -- feed, which is the one thing an archive must never get wrong.
    --
    -- This column is NOT on the Solicitation model, so db.upsert_row cannot write it and the
    -- spine cannot reach it. Only the title passes set it, like supplier_id.
    title_source         TEXT,
    first_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- NOTE: award is dual-provenance -- the same (document_number, supplier) can appear once per source
-- (source is in the UNIQUE key). For a de-duplicated view, filter source='odata' or GROUP BY
-- document_number, supplier_name_raw. Cross-source supplier de-dup (fuzzy) is a later phase.
CREATE TABLE IF NOT EXISTS award (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    document_number    TEXT NOT NULL,
    supplier_name_raw  TEXT,
    supplier_id        INTEGER,
    -- The City's published string, verbatim: "$1,317,169.92 CAD", "kj", "Metal Items at
    -- 109.11000 Percentage of the AMM published price". Archive fidelity — NOT summable.
    award_amount       TEXT,
    -- The number, where there plainly is one (toronto_bids/amount.py). NULL beside a
    -- non-NULL award_amount means the raw value is not a single CAD amount. Aggregate on
    -- this column, never on award_amount.
    award_amount_numeric REAL,
    award_amount_labelled  REAL,
    award_amount_verdict   TEXT,
    award_date         TEXT,
    source             TEXT,
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per award LINE, not per (document, supplier) (#73).
--
-- A document can award the same supplier many times — standing-offer call-ups are routine:
-- Cascades Recovery Inc. has 10 lines on doc 9154157025. Keying on
-- (document_number, supplier_name_raw, source) kept ONE arbitrary line and dropped the rest:
-- 326 lines and $451,879,325 of awarded value from the OData feed alone. It also made the two
-- City feeds look like they contradicted each other on 158 awards, because each happened to
-- keep a different survivor. Under this key they agree on all 6,745 shared pairs.
--
-- COALESCE, not a bare column list: SQLite treats NULLs as DISTINCT in a UNIQUE index, and 864
-- awards have no amount — a bare key would insert a fresh duplicate of every one of them on
-- every sync. The COALESCE keeps NULL in the data while making the index treat it as a value.
-- db._upsert_keyed's conflict target must match this expression exactly.
CREATE UNIQUE INDEX IF NOT EXISTS award_line_key ON award (
    document_number, supplier_name_raw,
    COALESCE(award_amount, ''), COALESCE(award_date, ''), source
);

CREATE INDEX IF NOT EXISTS idx_award_docnum ON award (document_number);

CREATE TABLE IF NOT EXISTS noncompetitive (
    workspace_number        TEXT PRIMARY KEY,
    supplier_name_raw       TEXT,
    supplier_id             INTEGER,
    reason                  TEXT,
    -- Raw string / parsed number: see the award table above.
    contract_amount         TEXT,
    contract_amount_numeric REAL,
    contract_amount_labelled REAL,
    contract_amount_verdict  TEXT,
    contract_date            TEXT,
    division                TEXT,
    council_authority_link  TEXT,
    odata_id                TEXT,
    source                  TEXT,
    first_seen              TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    rows_fetched   INTEGER DEFAULT 0,
    rows_upserted  INTEGER DEFAULT 0,
    error          TEXT
);

-- ariba_posting archives open SAP Ariba Discovery postings (which disappear when they close).
-- overwrite=True upserts fill NULL columns on later runs; a later 500 (all-NULL) never wipes an
-- earlier captured snapshot. document_number is bridged best-effort and may be NULL.
CREATE TABLE IF NOT EXISTS ariba_posting (
    rfx_id              TEXT PRIMARY KEY,
    document_number     TEXT,
    title               TEXT,
    posting_type        TEXT,
    status              TEXT,
    customer_name       TEXT,
    posted_date         TEXT,
    close_date          TEXT,
    categories          TEXT,
    amount_min          TEXT,
    amount_max          TEXT,
    currency            TEXT,
    public_posting_url  TEXT,
    sourcing_url        TEXT,
    external_rfx_id     TEXT,
    raw_json            TEXT,
    source              TEXT,
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ariba_posting_docnum ON ariba_posting (document_number);

-- suspended_firm mirrors the City's Suspended & Disqualified Firms registry (public HTML table).
-- Keyed on (supplier_name_raw, council_authority): one row per firm per council decision.
CREATE TABLE IF NOT EXISTS suspended_firm (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name_raw  TEXT NOT NULL,
    status             TEXT,
    start_date         TEXT,
    end_date           TEXT,
    suspension_type    TEXT,
    council_authority  TEXT,
    supplier_id        INTEGER,
    source             TEXT,
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (supplier_name_raw, council_authority)
);

-- supplier is the canonical supplier dimension: one row per normalized supplier_key,
-- with the raw name variants that mapped to it. award/noncompetitive/suspended_firm carry a
-- nullable supplier_id FK, backfilled by the build_supplier_dimension linking pass.
CREATE TABLE IF NOT EXISTS supplier (
    supplier_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_key  TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    variants      TEXT,
    first_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- council_item mirrors a TMMIS agenda item (a City Council decision), bridged to
-- suspended_firm via suspended_firm.council_authority = council_item.reference.
CREATE TABLE IF NOT EXISTS council_item (
    reference      TEXT PRIMARY KEY,
    title          TEXT,
    decision_text  TEXT,
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- background_pdf archives a staff-report (bgrd) or communication (comm) PDF linked
-- from a council_item, with its extracted text. Keyed on the URL.
CREATE TABLE IF NOT EXISTS background_pdf (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    -- The council item this PDF hangs off. NULL for kind='award_summary' (#114): the Bid
    -- Award Panel was abolished 2025-10-01, so those forms have no council item at all and
    -- are keyed on document_number instead. Do not conflate the two identifiers here.
    reference   TEXT,
    document_number TEXT,   -- set for kind='award_summary'; NULL for council PDFs
    kind        TEXT,       -- 'bgrd' | 'comm' | 'other' | 'award_summary'
    local_path  TEXT,
    sha256      TEXT,
    text        TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_background_pdf_reference ON background_pdf (reference);

-- Solicitations the City intends to issue but has not yet (#69, rewrite spec §2.1
-- "capital-project-pipeline"). Forward-looking, so there is no document_number and no join to
-- the spine — a project only gets one once it is actually solicited. Worth archiving anyway:
-- the City refreshes this list and entries drop off as they are sourced, so what was planned
-- is preserved nowhere else. That disappearing act is what this archive exists for.
--
-- Keyed on the City's combined name+contract string: 'No.' is a row index that churns on every
-- refresh. A renamed project therefore lands as a new row rather than updating the old one;
-- at 46 rows that is visible and tolerable, and archive semantics keep both.
CREATE TABLE IF NOT EXISTS capital_project (
    name                     TEXT PRIMARY KEY,
    contract_number          TEXT,
    type_of_work             TEXT,
    scope                    TEXT,
    delivery_division        TEXT,
    owner_division           TEXT,
    target_sourcing_year     TEXT,
    target_award_year        TEXT,
    sourcing_type            TEXT,
    estimated_range          TEXT,
    estimated_term_months    TEXT,
    source                   TEXT,
    first_seen               TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen                TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One bid on one solicitation, INCLUDING the ones that lost (#84).
--
-- Rewrite spec §2.5.2 lists this under "what everything downstream still cannot give us":
-- "Losing bidders and bid prices are never published anywhere. **Unrecoverable.**" That is
-- wrong — they are tabulated on every Bid Award Panel agenda, and 12,443 of them parse out of
-- the 475 agendas already cached under <DATA_DIR>/council/agendas/.
--
-- This is the table that lets the archive ask whether a procurement was *competitive*: how
-- many bids did it draw, was the winner cheapest, who keeps losing, whose bid was rejected
-- and why.
CREATE TABLE IF NOT EXISTS bid (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Both identifiers are partial, and which is present says where the bid came from. A Bid
    -- Award Panel bid always has a council item and had no document number before 2019. An
    -- Award Summary Form bid (#114) is the reverse: keyed on the document number, with no
    -- council item at all — the panel was abolished 2025-10-01. Neither can be NOT NULL.
    reference          TEXT,            -- council item, e.g. '2022.BA189.2'; NULL for #114
    document_number    TEXT,            -- NULL pre-2019: no Ariba doc numbers existed yet
    bidder_name_raw    TEXT NOT NULL,
    -- Backfilled by build_supplier_dimension, like award/noncompetitive/suspended_firm.
    -- This is what makes "which firms keep losing?" and "did a suspended firm keep bidding?"
    -- exact rather than string-matching (#87).
    supplier_id        INTEGER,
    -- Verbatim, footnote marker and all ('$2,982,036.67*'). The City also writes outcomes
    -- here — 'Non-Compliant', 'No bid', 'N/A' — which is why the raw string is kept: it
    -- records WHY a bid lost, and bid_price_numeric is NULL for exactly those.
    bid_price          TEXT,
    bid_price_numeric  REAL,
    -- 'including' | 'excluding' | NULL. Load-bearing: 5,752 bids are quoted including HST and
    -- 4,083 excluding it. Comparing or aggregating across the two without this is wrong.
    hst_basis          TEXT,
    price_header       TEXT,            -- the column header verbatim — provenance for hst_basis
    source             TEXT,
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COALESCE for the same reason as award_line_key (#73): SQLite treats NULLs as DISTINCT in a
-- UNIQUE index, and 1,909 bids have no price at all (scored RFPs list bidders without one),
-- so a bare key would insert a fresh duplicate of every one of them on every run.
-- db._upsert_keyed's conflict target must match this expression exactly.
CREATE UNIQUE INDEX IF NOT EXISTS bid_key ON bid (
    COALESCE(reference, ''), COALESCE(document_number, ''), bidder_name_raw,
    COALESCE(bid_price, ''), source
);

-- composite_award holds awards from the 2009-2012 Bid Committee composite reports (#96),
-- which predate Ariba and so carry no document_number. They are a THIRD KEYSPACE, keyed on
-- the Call Number, exactly as noncompetitive is keyed on workspace_number: there is no join
-- to `solicitation` and none can be manufactured. The City's feed covers almost none of this
-- period (2009: 0 awards, 2010: 1, 2011: 12), so for those years this table IS the record.
--
-- Deliberately separate from `award` rather than merged with a synthetic key: `award` is
-- keyed on document_number and every COUNT/SUM in the export assumes that, so admitting
-- keyless rows there would silently change what those numbers mean.
--
-- award_value_numeric is the FIRST "net of all applicable taxes" figure in the appendix,
-- i.e. the initial term excluding option years. Not a guess: on the 139 appendices whose
-- award the City's feed also published, that figure equals the feed's award_amount 137
-- times (98.6%). The option-year and "total potential" figures beside it are 2x larger and
-- are deliberately NOT summed here.
CREATE TABLE IF NOT EXISTS composite_award (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    call_number         TEXT NOT NULL,
    call_number_raw     TEXT,
    reference           TEXT,
    title               TEXT,
    supplier_name_raw   TEXT,
    supplier_id         INTEGER,
    award_value         TEXT,
    award_value_numeric REAL,
    source              TEXT,
    -- nullable supplier_id backfilled by build_supplier_dimension, declared bare exactly as
    -- award/noncompetitive/bid do. supplier's PK is `supplier_id`, not `id`.
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per award LINE, as in `award`: a call can name several recommended bidders. Same
-- COALESCE reason as award_line_key (#73) — SQLite treats NULLs as DISTINCT in a UNIQUE
-- index, so a bare key would re-insert every valueless row on each run.
-- db._upsert_keyed's conflict target must match this expression exactly.
CREATE UNIQUE INDEX IF NOT EXISTS composite_award_line_key ON composite_award (
    call_number, COALESCE(supplier_name_raw, ''), COALESCE(award_value, ''), source
);

CREATE INDEX IF NOT EXISTS idx_composite_award_call ON composite_award (call_number);

-- Human verdicts on the amounts the parser refuses (#74). A THIRD TIER, deliberately not
-- merged into *_numeric: that column's contract is "a number the machine derived from what
-- the City published", and it is what makes `numeric IS NULL` mean exactly "not
-- machine-parseable". Merging human judgement into it destroys the review queue and makes a
-- sum silently part-machine, part-opinion.
--
--     raw       award_amount           what the City published, verbatim
--     parsed    award_amount_numeric   machine-derived, conservative, no guesses
--     labelled  award_amount_labelled  human judgement, provenance in git
--
-- SUM(award_amount_numeric) is a defensible undercount. SUM(COALESCE(labelled, numeric)) is
-- fuller and the analyst knows they opted in. Neither is silently mixed.
--
-- Absent from the Award / NonCompetitive models on purpose, exactly as solicitation.
-- title_source is (#79): every sync re-upserts these rows, so anything db.upsert_row can
-- write, the feed can clobber. Only the labelling pass reaches these columns.

-- ariba_attachment indexes the actual solicitation documents behind Ariba's "Respond" gate
-- (#117) — RFP parts, drawings, addenda, pricing forms. The Discovery preview shows
-- Attachments (0); the files live inside the Sourcing event, downloadable only as a
-- participating supplier. sources/ariba_attachments.py archives the whole event as one
-- server-zipped bundle under <DATA_DIR>/ariba/attachments/ and records one row PER FILE here.
-- The bytes are NOT in the DB and NOT in git (multi-GB); this table is the INDEX — what
-- documents exist for a solicitation and where the bundle sits. Nothing is surfaced in the
-- export yet, by design (archive now, publish later).
--
-- document_number joins solicitation.document_number (the Ariba event = the 10-digit doc).
-- Respond is disabled once a posting closes, so rows only ever accrue for solicitations that
-- were OPEN at capture time — a recurring job, not a backfill.
CREATE TABLE IF NOT EXISTS ariba_attachment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_number TEXT NOT NULL,   -- the Ariba event; joins solicitation.document_number
    filename        TEXT NOT NULL,   -- the LEAF name of one file inside the bundle
    -- Full nested path within the bundle, e.g. 'Appendix C2.zip/drawings/site.pdf'. The real
    -- identity: leaf names collide across nested zips. Recursively expanded (#123).
    path            TEXT,
    file_size       INTEGER,         -- uncompressed bytes, from the zip central directory
    -- CRC32 comes free from the central directory (no decompression of a 160 MB bundle), so
    -- it fingerprints each file for dedup/integrity without ever inflating the entry.
    crc32           TEXT,
    zip_name        TEXT,            -- stored bundle basename under <DATA_DIR>/ariba/attachments/
    zip_sha256      TEXT,            -- sha256 of the whole bundle: integrity + cross-event dedup
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_number, path)
);

CREATE INDEX IF NOT EXISTS idx_ariba_attachment_document ON ariba_attachment (document_number);

-- The buyer dimension and agency tables (#135, first consumer of #103's keyspace decision).
-- Agencies/corporations procure OUTSIDE the City's PMMD feed, each in its own numbering —
-- a FOURTH keyspace, keyed (buyer_id, native_ref). Deliberately separate from the City
-- spine for the same reason composite_award is (#96): admitting foreign-keyed rows to
-- `solicitation` would silently change what every existing COUNT/SUM means. Partnered
-- bodies (TRCA: Toronto pays 62.6% of the levy) carry a flag so exports can segment.
CREATE TABLE IF NOT EXISTS buyer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT NOT NULL UNIQUE,
    name           TEXT,
    kind           TEXT,             -- 'agency' | 'corporation'
    partnered      INTEGER,          -- 1 = not wholly City-owned; segment, don't mix
    funding_share  REAL,             -- Toronto's share where partnered (TRCA: 0.626)
    platform       TEXT,             -- where it posts (bids&tenders here; MERX/Bonfire later)
    notes          TEXT,
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agency_solicitation (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id     INTEGER NOT NULL,
    -- The body's own identifier, normalized only by trim/uppercase/whitespace-collapse:
    -- TRCA '10039751', Zoo 'RFT-42' / 'RFP 18 (2018-03)'. Where a report names no ref at
    -- all, the TMMIS item reference (e.g. '2025.ZB15.3') stands in. No join to the City
    -- keyspaces is attempted — none can be manufactured.
    native_ref   TEXT NOT NULL,
    title        TEXT,
    status       TEXT,
    posted_date  TEXT,
    closing_date TEXT,
    portal_url   TEXT,
    source       TEXT,
    first_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (buyer_id, native_ref)
);

CREATE TABLE IF NOT EXISTS agency_award (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id             INTEGER NOT NULL,
    native_ref           TEXT NOT NULL,
    supplier_name_raw    TEXT,
    supplier_id          INTEGER,
    -- The extracted dollar token verbatim ('$1,193,040'), never the sentence around it
    -- (#96: a phrase leaves *_numeric NULL on every row and zeroes every SUM).
    award_amount         TEXT,
    award_amount_numeric REAL,
    -- 1 = the report routes financials to a CONFIDENTIAL ATTACHMENT (Zoo, 2025-era).
    -- Distinct from "not published": the award happened, the value is deliberately withheld.
    value_confidential   INTEGER DEFAULT 0,
    award_date           TEXT,
    report_url           TEXT,
    source               TEXT,
    first_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COALESCE for the same reason as award_line_key (#73): SQLite treats NULLs as DISTINCT in
-- a UNIQUE index, and a confidential award has NULL supplier and NULL amount — a bare key
-- would re-insert it on every run. db._upsert_keyed's conflict target must match exactly.
CREATE UNIQUE INDEX IF NOT EXISTS agency_award_line_key ON agency_award (
    buyer_id, native_ref, COALESCE(supplier_name_raw, ''), COALESCE(award_amount, ''), source
);

CREATE TABLE IF NOT EXISTS agency_bid (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id          INTEGER NOT NULL,
    native_ref        TEXT NOT NULL,
    bidder_name_raw   TEXT NOT NULL,
    supplier_id       INTEGER,
    -- Usually NULL: TRCA results tables are fused multi-line pdftotext output (the #83
    -- wrapped-names trap), so per-bid prices are refused rather than guessed. The bidder
    -- LIST is the competitive fact (#84); prices come from the award lines.
    bid_price         TEXT,
    bid_price_numeric REAL,
    report_url        TEXT,
    source            TEXT,
    first_seen        TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (buyer_id, native_ref, bidder_name_raw, source)
);

CREATE INDEX IF NOT EXISTS idx_agency_award_buyer ON agency_award (buyer_id, native_ref);
CREATE INDEX IF NOT EXISTS idx_agency_bid_buyer ON agency_bid (buyer_id, native_ref);
