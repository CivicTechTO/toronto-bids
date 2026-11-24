"""Import the source-reviewed TRCA minute book items selected in issue #316."""

import argparse
import hashlib
import json
from collections import defaultdict
from contextlib import closing
from dataclasses import fields
from pathlib import Path

from toronto_bids.linking.agency_reference import normalize_agency_reference
from toronto_bids.linking.procurement import AGENCY_CONTRACT, register_agency_contract_keys
from toronto_bids.linking.supplier import supplier_key
from toronto_bids.models import AgencyAward, AgencyBid, AgencySolicitation, Supplier
from toronto_bids.sources.trca_history import KIND, document_url
from toronto_bids.store import db

CLOTHING = 1328270
FISHLEIGH = 1452956
ORIGINAL_CLOTHING_BIDS = 1327362
ORIGINAL_CLOTHING_AWARD = 1327312
PORT_UNION = 1046313
SOURCE_HASHES = {
    CLOTHING: "aedd3fd609eaa2969add1e07dfbfbd46b98d70eb1583df919285dde5283e2e6e",
    FISHLEIGH: "958e2ab8965f7b2e913cd04be1ff0d5a787704257a92da4a71298c6236b74682",
    ORIGINAL_CLOTHING_BIDS: "3c0b7f61b3fcf314bf83422cbc503ab7df76c16e297b2f822f104abd6e6d3bf4",
    ORIGINAL_CLOTHING_AWARD: "1dbffe4d95145eb60e9f6c67652852be4960ee94b33774645924de5139f4e494",
    PORT_UNION: "93d7361a51238ecab229d58039aadd930e8e492c23bc049635441d4971aa4e76",
}
TABLES = ("agency_solicitation", "agency_bid", "agency_award")


def _reviewed_rows(buyer_id):
    # Authority A167/15, PDF pp. 361, 411–412. Annual bid estimates and a two-year award estimate.
    # Authority A110/17, PDF pp. 164, 239–242. Bid totals and award ceilings exclude HST.
    clothing = (CLOTHING, "2015-08-07", "2015-09-25", "Estimated annual cost (plus HST)")
    stone = (FISHLEIGH, "2017-05-25", "2017-06-23", "Total value of contract (plus HST)")
    items = (
        ("10000813", "Supply and delivery of clothing 2015–2017", clothing,
         "Dufferin Apparel", "$296,000.00", (
             ("Dufferin Apparel", "$148,391.15"),
             ("G & L Promotions", "$163,147.00"),
             ("Needham Promotions", "$170,166.79"),
             ("Cotton Candy", "$207,298.01"),
         )),
        ("10005031", "Fishleigh Drive erosion control – supply and delivery of 3,800 tonnes of rip-rap",
         stone, "Glenn Windrem Trucking", "$120,650.00", (
             ("Glenn Windrem Trucking", "$120,650.00"),
             ("CDR Young Aggregates", "$129,998.00"),
             ("Btown Group", "$136,990.00"),
             ("Miller Paving Ltd", "$139,650.00"),
             ("Ritchie Rocks", "$150,100.00"),
             ("Atlantis Marine", "$159,600.00"),
             ("Amos Excavation", "$298,490.00"),
         )),
        ("10005032", "Fishleigh Drive erosion control – supply and delivery of 4,650 tonnes of armourstone",
         stone, "CDR Young Aggregates Inc", "$246,729.00", (
             ("CDR Young Aggregates", "$246,729.00"),
             ("Atlantis Marine", "$260,400.00"),
             ("Cut Above Natural Stone", "$265,747.50"),
             ("Btown Group", "$267,607.50"),
             ("Glenn Windrem Trucking", "$299,925.00"),
             ("Ritchie Rocks", "$306,900.00"),
             ("Amos Excavation", "$498,015.00"),
         )),
        ("10005033", "Fishleigh Drive erosion control – supply and delivery of 1,400 tonnes of armourstone",
         stone, "CDR Young Aggregates Inc", "$74,284.00", (
             ("CDR Young Aggregates", "$74,284.00"),
             ("Redstone Quarries", "$75,460.00"),
             ("Atlantis Marine", "$78,400.00"),
             ("Cut Above Natural Stone", "$80,010.00"),
             ("Btown Group", "$80,500.00"),
             ("Glenn Windrem Trucking", "$90,300.00"),
             ("Ritchie Rocks", "$92,400.00"),
             ("Amos Excavation", "$156,940.00"),
         )),
    )
    solicitations, bids, awards = [], [], []
    for ref, title, (docid, posted, decided, price_header), recipient, amount, quotes in items:
        url = document_url(docid)
        solicitations.append(AgencySolicitation(buyer_id, ref, title, "awarded", posted, source=KIND))
        awards.append(AgencyAward(buyer_id, ref, recipient, amount, award_date=decided,
                                  report_url=url, source=KIND))
        # The Fishleigh table abbreviates the same recipient named in its carried motion.
        table_winner = "CDR Young Aggregates" if recipient == "CDR Young Aggregates Inc" else recipient
        for name, price in quotes:
            bids.append(AgencyBid(buyer_id, ref, name, price, report_url=url, source=KIND,
                                  hst_basis="excluding", price_header=price_header,
                                  status="compliant" if name == table_winner else "not_stated", result_stage="Tender submissions"))
    # Executive Committee B120/14, pp. 536–537, for bids. Authority A215/14, p. 536, for the award.
    # Grant Custom Products did not submit by the deadline. MARCC submitted a disqualified bid.
    ref = "10000128"
    solicitations.append(AgencySolicitation(buyer_id, ref, "Supply and delivery of clothing 2015–2017",
                                            "awarded", source=KIND))
    awards.append(AgencyAward(buyer_id, ref, "Needham Promotions Inc.", "$270,000.00",
                              award_date="2014-12-12", report_url=document_url(ORIGINAL_CLOTHING_AWARD),
                              source=KIND))
    for name, price, status in (
        ("Needham Promotions Inc.", "$132,470.45", "compliant"),
        ("Dufferin Apparel", "$137,525.65", "not_stated"),
        ("ANS Promotional Marketing Inc.", "$156,854.00", "not_stated"),
        ("Cotton Candy", "$165,870.80", "not_stated"),
        ("MARCC Apparel Co.", None, "disqualified"),
    ):
        bids.append(AgencyBid(buyer_id, ref, name, price, report_url=document_url(ORIGINAL_CLOTHING_BIDS),
                              source=KIND, hst_basis="excluding" if price else None,
                              price_header="Estimated annual cost (plus HST)", status=status,
                              result_stage="Tender submissions"))

    # Tender Opening Committee #10/13, pp. 17–18. These bids were referred to staff for review.
    ref = "RSD13-096"
    solicitations.append(AgencySolicitation(buyer_id, ref, "Port Union Waterfront parking lot construction",
                                            source=KIND))
    for name, price in (
        ("Four Seasons Site Development", "$70,000.00"),
        ("LocPav Construction Ltd.", "$69,900.00"),
        ("Lomco Contracting", "$87,980.00"),
        ("Mopal Construction", "$215,002.00"),
        ("Pacific Paving", "$94,950.00"),
        ("Serve Construction", "$118,200.00"),
    ):
        bids.append(AgencyBid(buyer_id, ref, name, price, report_url=document_url(PORT_UNION),
                              source=KIND, hst_basis="excluding", price_header="Total tender amount (plus HST)",
                              status="not_stated", result_stage="Tender submissions"))
    return solicitations, bids, awards


def _archived_sources(conn):
    for docid, expected in SOURCE_HASHES.items():
        row = conn.execute("SELECT local_path, sha256 FROM background_pdf WHERE url=? AND kind=?",
                           (document_url(docid), KIND)).fetchone()
        if row is None or not row["local_path"] or row["sha256"] != expected:
            raise ValueError(f"Laserfiche document {docid} does not match the reviewed archive record")
        with Path(row["local_path"]).open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise ValueError(f"Laserfiche document {docid} bytes differ from the reviewed PDF")


def _facts(conn, table, buyer_id, refs):
    # Use the normalizer for stored references that may have a printed prefix.
    matches = []
    for row in conn.execute(f"SELECT * FROM {table} WHERE buyer_id=?", (buyer_id,)):
        try:
            normalized = normalize_agency_reference("trca", row["native_ref"])
        except ValueError:
            # An ambiguous existing label is a collision if it contains any pilot reference.
            normalized = None
            if any(ref in row["native_ref"] for ref in refs):
                raise ValueError(f"Ambiguous existing TRCA reference {row['native_ref']!r}") from None
        if normalized in refs:
            matches.append(row)
    return matches


def _link_suppliers(conn, bids, awards):
    names = defaultdict(set)
    for name in [*(b.bidder_name_raw for b in bids), *(a.supplier_name_raw for a in awards)]:
        names[supplier_key(name)].add(name)
    for key, reviewed_names in names.items():
        row = conn.execute("SELECT * FROM supplier WHERE supplier_key=?", (key,)).fetchone()
        variants = set(json.loads(row["variants"] or "[]")) if row else set()
        all_names = sorted(variants | reviewed_names)
        if set(all_names) != variants:
            display = row["display_name"] if row else all_names[0]
            db.upsert_row(conn, Supplier(key, display, json.dumps(all_names)), overwrite=True)
    for table, name_column, reviewed in (
        ("agency_bid", "bidder_name_raw", bids), ("agency_award", "supplier_name_raw", awards)
    ):
        for row in reviewed:
            name = getattr(row, name_column)
            conn.execute(f"""UPDATE {table} SET supplier_id=(
                SELECT supplier_id FROM supplier WHERE supplier_key=?)
                WHERE buyer_id=? AND native_ref=? AND {name_column}=? AND source=?""",
                         (supplier_key(name), row.buyer_id, row.native_ref, name, KIND))


def _link_procurements(conn, buyer_id, keys, source_rows):
    for ref, procurement_id in keys.items():
        for table in TABLES:
            conn.execute(f"""UPDATE {table} SET procurement_id=?
                         WHERE buyer_id=? AND native_ref=? AND source=?""",
                         (procurement_id, buyer_id, ref, KIND))
    links = {(keys[row.native_ref], row.report_url) for row in source_rows}
    conn.executemany("INSERT INTO procurement_document (procurement_id, url) VALUES (?, ?)", sorted(links))
    for url in {url for _, url in links}:
        conn.execute("""UPDATE background_pdf SET procurement_id=(
            SELECT CASE WHEN COUNT(*)=1 THEN MIN(procurement_id) END
            FROM procurement_document WHERE url=?) WHERE url=? AND kind=?""", (url, url, KIND))


def import_pilot(conn):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _archived_sources(conn)
        buyer = conn.execute("SELECT id FROM buyer WHERE slug='trca'").fetchone()
        if not buyer:
            raise ValueError("TRCA buyer must already exist")
        buyer_id = buyer["id"]
        planned = _reviewed_rows(buyer_id)
        solicitations, _, _ = planned
        refs = {s.native_ref for s in solicitations}
        ns = AGENCY_CONTRACT + str(buyer_id)
        existing_refs = {r["value"] for r in conn.execute(
            "SELECT value FROM procurement_key WHERE namespace=?", (ns,))}
        existing = [_facts(conn, table, buyer_id, refs) for table in TABLES]
        if any(row["source"] != KIND for group in existing for row in group):
            raise ValueError("Pilot references conflict with an existing TRCA source")
        missing = refs - {row["native_ref"] for group in existing for row in group}
        if missing:
            if existing_refs & missing:
                raise ValueError("A pilot reference already has a canonical procurement")
            new_rows = [[row for row in group if row.native_ref in missing] for group in planned]
            for group in new_rows:
                for row in group:
                    db.upsert_row(conn, row, overwrite=False)
            _, new_bids, new_awards = new_rows
            _link_suppliers(conn, new_bids, new_awards)
            keys = register_agency_contract_keys(conn, buyer_id, missing)
            _link_procurements(conn, buyer_id, keys, [*new_bids, *new_awards])
            existing = [_facts(conn, table, buyer_id, refs) for table in TABLES]

        keys = {s["native_ref"]: s["procurement_id"] for s in existing[0]}
        if set(keys) != refs or None in keys.values() or len(set(keys.values())) != len(refs):
            raise ValueError("Pilot records must have distinct canonical procurements")
        for group, stored in zip(planned, existing, strict=True):
            columns = [field.name for field in fields(group[0])]
            expected = {tuple(getattr(row, col) for col in columns) for row in group}
            actual = {tuple(row[col] for col in columns) for row in stored}
            if actual != expected or len(stored) != len(group):
                raise ValueError("Existing pilot references conflict with the reviewed rows")
            for row in stored:
                if row["procurement_id"] != keys[row["native_ref"]]:
                    raise ValueError(f"Inconsistent procurement link for {row['native_ref']}")
                if "supplier_id" in row.keys() and not row["supplier_id"]:
                    raise ValueError(f"Missing supplier link for {row['native_ref']}")
                if "report_url" in row.keys() and not conn.execute(
                    "SELECT 1 FROM procurement_document WHERE procurement_id=? AND url=?",
                    (row["procurement_id"], row["report_url"]),
                ).fetchone():
                    raise ValueError(f"Missing source document link for {row['native_ref']}")
        for ref, procurement_id in keys.items():
            if not conn.execute("SELECT 1 FROM procurement_key WHERE namespace=? AND value=? AND procurement_id=?",
                                (ns, ref, procurement_id)).fetchone():
                raise ValueError(f"Missing canonical agency key for {ref}")
    return ("imported" if missing else "verified"), keys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    with closing(db.connect(args.database.resolve(strict=True))) as conn:
        action, keys = import_pilot(conn)
    print(f"TRCA history {action}: {len(keys)} reviewed procurements")
    for ref, pid in sorted(keys.items()):
        print(f"  {ref}: procurement {pid}")


if __name__ == "__main__":
    main()
