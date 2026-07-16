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
    reference   TEXT,
    kind        TEXT,
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
    reference          TEXT NOT NULL,   -- council item, e.g. '2022.BA189.2'
    document_number    TEXT,            -- NULL pre-2019: no Ariba doc numbers existed yet
    bidder_name_raw    TEXT NOT NULL,
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
    reference, bidder_name_raw, COALESCE(bid_price, ''), source
);
