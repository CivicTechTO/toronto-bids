-- Archive-owned procurement identities; source natural keys live in procurement_key.
CREATE TABLE IF NOT EXISTS procurement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merged_into INTEGER REFERENCES procurement(id),
    merge_evidence TEXT,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (merged_into IS NULL OR merged_into != id)
);
CREATE TABLE IF NOT EXISTS procurement_key (
    namespace TEXT NOT NULL,
    value TEXT NOT NULL,
    procurement_id INTEGER NOT NULL REFERENCES procurement(id),
    PRIMARY KEY (namespace, value)
);
CREATE TABLE IF NOT EXISTS procurement_document (
    procurement_id INTEGER NOT NULL REFERENCES procurement(id),
    url TEXT NOT NULL REFERENCES background_pdf(url),
    PRIMARY KEY (procurement_id, url)
);
CREATE TABLE IF NOT EXISTS procurement_council_item (
    procurement_id INTEGER NOT NULL REFERENCES procurement(id),
    reference TEXT NOT NULL REFERENCES council_item(reference),
    PRIMARY KEY (procurement_id, reference)
);
CREATE TABLE IF NOT EXISTS portal_listing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    portal_url TEXT NOT NULL,
    native_ref TEXT,
    title TEXT,
    status TEXT,
    posted_date TEXT,
    closing_date TEXT,
    source TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (buyer_id, portal_url)
);

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
    publisher_award_identity_json TEXT,
    publisher_identity_json TEXT,
    odata_id             TEXT,
    source               TEXT,
    -- Provenance for recovered titles ('bid_award_panel' | 'legacy_ariba_html' | 'council_pre_ariba'; NULL = City feed).
    -- Omitted from Solicitation model so OData sync cannot overwrite it.
    title_source         TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dual-provenance: source is part of the unique key (filter source='odata' to deduplicate).
CREATE TABLE IF NOT EXISTS award (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    document_number    TEXT NOT NULL,
    supplier_name_raw  TEXT,
    supplier_id        INTEGER,
    -- Verbatim published string; aggregate on award_amount_numeric (NULL if not a single CAD amount).
    award_amount       TEXT,
    award_amount_numeric REAL,
    award_amount_labelled  REAL,
    award_amount_verdict   TEXT,
    award_date         TEXT,
    source             TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per award line. COALESCE avoids SQLite treating NULLs as distinct in UNIQUE indexes;
-- db._CONFLICT_TARGETS["award"] must match this expression exactly.
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
    contract_amount         TEXT,
    contract_amount_numeric REAL,
    contract_amount_labelled REAL,
    contract_amount_verdict  TEXT,
    contract_date            TEXT,
    division                TEXT,
    council_authority_link  TEXT,
    odata_id                TEXT,
    source                  TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
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

-- Open SAP Ariba Discovery postings (archived before close); document_number is best-effort.
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
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ariba_posting_docnum ON ariba_posting (document_number);

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

-- Canonical supplier dimension; supplier_id FKs on fact tables are backfilled by build_supplier_dimension.
CREATE TABLE IF NOT EXISTS supplier (
    supplier_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_key  TEXT NOT NULL UNIQUE,
    display_name  TEXT,
    variants      TEXT,
    first_seen    TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS council_item (
    reference      TEXT PRIMARY KEY,
    title          TEXT,
    decision_text  TEXT,
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS background_pdf (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL UNIQUE,
    reference   TEXT,       -- council item; NULL for kind='award_summary'
    document_number TEXT,   -- set for kind='award_summary'; NULL for council PDFs
    kind        TEXT,       -- 'bgrd' | 'comm' | 'other' | 'award_summary'
    local_path  TEXT,
    sha256      TEXT,
    text        TEXT,
    award_review_context_json TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_background_pdf_reference ON background_pdf (reference);

-- Forward-looking capital project pipeline (no document_number; keyed on name).
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
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen               TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen                TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Winning and losing bids on City solicitations.
CREATE TABLE IF NOT EXISTS bid (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    -- reference is NULL for post-2025-10-01 Award Summary Forms; document_number is NULL pre-2019.
    reference          TEXT,
    document_number    TEXT,
    bidder_name_raw    TEXT NOT NULL,
    supplier_id        INTEGER,
    bid_price          TEXT,
    bid_price_numeric  REAL,
    -- 'including' | 'excluding' | NULL; required before comparing prices across rows.
    hst_basis          TEXT,
    price_header       TEXT,
    status             TEXT,
    source             TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COALESCE avoids duplicate NULL rows; db._CONFLICT_TARGETS["bid"] must match this expression.
CREATE UNIQUE INDEX IF NOT EXISTS bid_key ON bid (
    COALESCE(reference, ''), COALESCE(document_number, ''), bidder_name_raw,
    COALESCE(bid_price, ''), source
);

-- Council item <-> City document_number links (city_call_crosswalk links survive council_pre_ariba rebuilds).
CREATE TABLE IF NOT EXISTS solicitation_link (
    reference        TEXT PRIMARY KEY,
    document_number  TEXT NOT NULL,
    method           TEXT NOT NULL
);

-- 2009-2012 pre-Ariba Bid Committee composite awards (keyed on call_number, no document_number).
-- award_value_numeric is the initial-term net-of-taxes figure (excludes option years).
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
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COALESCE avoids duplicate NULL rows; db._CONFLICT_TARGETS["composite_award"] must match this expression.
CREATE UNIQUE INDEX IF NOT EXISTS composite_award_line_key ON composite_award (
    call_number, COALESCE(supplier_name_raw, ''), COALESCE(award_value, ''), source
);

CREATE INDEX IF NOT EXISTS idx_composite_award_call ON composite_award (call_number);

-- Human verdicts on unparseable amounts (*_labelled / *_verdict) are omitted from Award/NonCompetitive
-- models so feed syncs cannot overwrite them; only linking/amount_labels.py writes them.

-- Index of files inside downloaded Ariba attachment zip bundles (bytes stay on disk under <DATA_DIR>/ariba/attachments/).
CREATE TABLE IF NOT EXISTS ariba_attachment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_number TEXT NOT NULL,
    filename        TEXT NOT NULL,
    -- Full nested path within the bundle (leaf filenames collide across nested zips).
    path            TEXT,
    file_size       INTEGER,
    crc32           TEXT,
    zip_name        TEXT,
    zip_sha256      TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen      TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (document_number, path)
);

CREATE INDEX IF NOT EXISTS idx_ariba_attachment_document ON ariba_attachment (document_number);

-- Non-City procuring agencies/corporations, keyed separately on (buyer_id, native_ref).
CREATE TABLE IF NOT EXISTS buyer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT NOT NULL UNIQUE,
    name           TEXT,
    kind           TEXT,             -- 'agency' | 'corporation'
    partnered      INTEGER,          -- 1 = not wholly City-owned
    funding_share  REAL,             -- Toronto's funding share where partnered (TRCA: 0.626)
    platform       TEXT,
    notes          TEXT,
    first_seen     TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agency_solicitation (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id     INTEGER NOT NULL,
    -- Buyer's own normalized identifier (or TMMIS item reference when unnamed).
    native_ref   TEXT NOT NULL,
    title        TEXT,
    status       TEXT,
    posted_date  TEXT,
    closing_date TEXT,
    portal_url   TEXT,
    source       TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
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
    award_amount         TEXT,
    award_amount_numeric REAL,
    -- 1 = financials withheld in a confidential attachment.
    value_confidential   INTEGER DEFAULT 0,
    award_date           TEXT,
    publication_date     TEXT,
    report_url           TEXT,
    source               TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- COALESCE avoids duplicate NULL rows; db._CONFLICT_TARGETS["agency_award"] must match this expression.
CREATE UNIQUE INDEX IF NOT EXISTS agency_award_line_key ON agency_award (
    buyer_id, native_ref, COALESCE(supplier_name_raw, ''), COALESCE(award_amount, ''), source
);

CREATE TABLE IF NOT EXISTS agency_bid (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id          INTEGER NOT NULL,
    native_ref        TEXT NOT NULL,
    bidder_name_raw   TEXT NOT NULL,
    supplier_id       INTEGER,
    bid_price         TEXT,
    bid_price_numeric REAL,
    hst_basis         TEXT,
    price_header      TEXT,
    status            TEXT,
    result_stage      TEXT,
    report_url        TEXT,
    source            TEXT,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen        TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (buyer_id, native_ref, bidder_name_raw, source)
);

CREATE INDEX IF NOT EXISTS idx_agency_award_buyer ON agency_award (buyer_id, native_ref);
CREATE INDEX IF NOT EXISTS idx_agency_bid_buyer ON agency_bid (buyer_id, native_ref);

-- Derivation audit log; active=1 marks the latest rebuild per corpus.
CREATE TABLE IF NOT EXISTS agency_derivation (
    id INTEGER PRIMARY KEY,
    corpus TEXT NOT NULL,
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    decision_sha256 TEXT NOT NULL,
    contract_index INTEGER NOT NULL,
    contract_count INTEGER NOT NULL,
    report_url TEXT NOT NULL,
    report_reference TEXT,
    native_ref TEXT,
    reason TEXT,
    contract_json TEXT NOT NULL,
    identity_evidence_json TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(corpus, sha256, extractor_version, result_sha256, contract_index, report_url, decision_sha256)
);
CREATE INDEX IF NOT EXISTS agency_derivation_active ON agency_derivation(corpus, active);

CREATE TABLE IF NOT EXISTS agency_coverage (
    corpus TEXT PRIMARY KEY,
    summary_json TEXT NOT NULL,
    rebuilt_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Extraction cache keyed on (sha256, extractor_version) so version bumps invalidate prior results.
CREATE TABLE IF NOT EXISTS extraction_cache (
    sha256            TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    result_json       TEXT,
    extracted_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (sha256, extractor_version)
);

-- Public HTML award publications (month-level observations).
CREATE TABLE IF NOT EXISTS award_publication (
    source_key TEXT PRIMARY KEY,
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    publication_month TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    source_position INTEGER NOT NULL,
    labels TEXT NOT NULL,
    source_url TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS published_award (
    source_key TEXT PRIMARY KEY,
    publication_key TEXT NOT NULL REFERENCES award_publication(source_key),
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    supplier_name_raw TEXT NOT NULL,
    supplier_id INTEGER,
    award_amount TEXT,
    award_amount_numeric REAL,
    title_extracted TEXT,
    evidence_json TEXT,
    extraction_sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS publication_extraction (
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    publication_month TEXT NOT NULL,
    extraction_sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    status TEXT NOT NULL,
    errors TEXT NOT NULL,
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (buyer_id, publication_month)
);

-- Maps MERX notice_id to its first observed public URL.
CREATE TABLE IF NOT EXISTS merx_notice (
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    notice_id TEXT NOT NULL,
    portal_url TEXT NOT NULL,
    PRIMARY KEY(buyer_id, notice_id),
    UNIQUE(buyer_id, portal_url)
);
CREATE TABLE IF NOT EXISTS merx_capture (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id INTEGER NOT NULL REFERENCES buyer(id),
    captured_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    complete INTEGER NOT NULL,
    report_json TEXT NOT NULL
);

-- Immutable reviewed proofs for council-item <-> City document_number crosswalks.
CREATE TABLE IF NOT EXISTS city_crosswalk_evidence (
    proof_sha256 TEXT PRIMARY KEY,
    reference TEXT NOT NULL,
    document_number TEXT NOT NULL,
    call_number TEXT NOT NULL,
    proof_class TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Immutable source/cache/identity witnesses for reviewed BA/BD bid projections.
CREATE TABLE IF NOT EXISTS city_bid_evidence (
    proof_sha256 TEXT PRIMARY KEY,
    document_number TEXT NOT NULL,
    reference TEXT NOT NULL,
    source TEXT NOT NULL,
    report_url TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    source_role TEXT NOT NULL,
    bid_count INTEGER NOT NULL,
    proof_json TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Programmatic whole-report participation admissions from the bulk City index.
CREATE TABLE IF NOT EXISTS city_bulk_bid_evidence (
    proof_sha256 TEXT PRIMARY KEY,
    document_number TEXT NOT NULL,
    report_url TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Source-specific native call alias inside a multi-contract report.
CREATE TABLE IF NOT EXISTS city_document_alias_evidence (
    proof_sha256 TEXT PRIMARY KEY,
    document_number TEXT NOT NULL,
    alias_document_number TEXT NOT NULL,
    call_number TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS city_native_bid_evidence (
    proof_sha256 TEXT PRIMARY KEY,
    document_number TEXT NOT NULL,
    call_number TEXT NOT NULL,
    reference TEXT NOT NULL,
    report_url TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Source-reviewed scoped responses; legacy bid rows remain participation summaries.
CREATE TABLE IF NOT EXISTS city_scoped_bid_evidence (
    proof_sha256 TEXT PRIMARY KEY,
    document_number TEXT NOT NULL,
    reference TEXT NOT NULL,
    source TEXT NOT NULL,
    report_url TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    participant_count INTEGER NOT NULL,
    response_count INTEGER NOT NULL,
    price_count INTEGER NOT NULL,
    proof_json TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bid_scope (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL,
    document_number TEXT,
    source TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_label TEXT,
    scope_status TEXT,
    proof_sha256 TEXT NOT NULL REFERENCES city_scoped_bid_evidence(proof_sha256),
    procurement_id INTEGER REFERENCES procurement(id),
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(reference,source,scope_key)
);
CREATE TABLE IF NOT EXISTS bid_response (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_id INTEGER NOT NULL REFERENCES bid_scope(id) ON DELETE CASCADE,
    response_key TEXT NOT NULL,
    bid_id INTEGER REFERENCES bid(id),
    bidder_name_raw TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    response_kind TEXT NOT NULL,
    alternative_label TEXT,
    status TEXT,
    row_quotation TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(scope_id,response_key)
);
CREATE TABLE IF NOT EXISTS bid_response_price (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id INTEGER NOT NULL REFERENCES bid_response(id) ON DELETE CASCADE,
    price_key TEXT NOT NULL,
    value_raw TEXT NOT NULL,
    value_kind TEXT NOT NULL,
    unit TEXT,
    value_numeric REAL,
    price_header TEXT NOT NULL,
    price_basis TEXT,
    hst_basis TEXT,
    quotation TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(response_id,price_key)
);

-- Locally derived OCR/native recovery text; raw PDF bytes and capture text stay intact.
CREATE TABLE IF NOT EXISTS pdf_text (
    sha256 TEXT PRIMARY KEY,
    method TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    proof_sha256 TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
