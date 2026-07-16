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
    award_amount       TEXT,
    award_date         TEXT,
    source             TEXT,
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_number, supplier_name_raw, source)
);

CREATE INDEX IF NOT EXISTS idx_award_docnum ON award (document_number);

CREATE TABLE IF NOT EXISTS noncompetitive (
    workspace_number        TEXT PRIMARY KEY,
    supplier_name_raw       TEXT,
    supplier_id             INTEGER,
    reason                  TEXT,
    contract_amount         TEXT,
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
