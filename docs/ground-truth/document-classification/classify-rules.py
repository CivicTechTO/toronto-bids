"""Rules baseline for document classification — the thing a cheap LLM classifier must beat.

Built from what the sample actually showed, not from what the sources were assumed to look like:

  - EP and the Zoo use `REPORT FOR ACTION` with the title on the FOLLOWING lines and no
    `Subject:` label at all. That single omission left 809 of 1,200 EP documents unclassified.
  - `REPORT FOR ACTION WITH CONFIDENTIAL ATTACHMENT` is a variant, and confidentiality is a
    FLAG that cross-cuts classes, not a class.
  - Length decides two classes outright (empty fragment, meeting package). Never pay a model
    for something `len()` answers.
  - Header typos are real (`Atttachment 1: Site Plan`), so anchors must tolerate them.
"""
import collections
import re
import sys

sys.path.insert(0, "/home/alex/toronto-bids/scrapers")
from toronto_bids import config          # noqa: E402
from toronto_bids.store import db        # noqa: E402

PACKAGE_CHARS = 100_000
FRAGMENT_CHARS = 1_500

CONFIDENTIAL = re.compile(r"CONFIDENTIAL ATTACHMENT", re.I)

# title, per source convention
T_RE = re.compile(r"^\s*RE:\s*(.{4,200})", re.M | re.S)                 # TRCA
T_SUBJ = re.compile(r"^\s*Subject:\s*(.{4,200})", re.M | re.S | re.I)   # Zoo staff report
T_RFA = re.compile(r"REPORT FOR ACTION(?:\s+WITH\s*\n?\s*CONFIDENTIAL ATTACHMENT)?\s*\n"
                   r"(.{6,220}?)\n\s*Date:", re.S | re.I)               # EP / Zoo
T_AWARD = re.compile(r"CONTRACT AWARD\s*\n(.{10,220}?)\n\s*Date:", re.S | re.I)   # council bgrd
T_ITEM = re.compile(r"^\s*\d{1,2}\.\d{1,2}\s+([A-Z][^\n]{10,160})", re.M)         # TRCA item head

MINUTES = re.compile(r"^\s*(PRESENT\b|MINUTES OF|MEMBERS PRESENT)", re.M | re.I)
AGENDA = re.compile(r"^\s*(AGENDA|ORDER OF BUSINESS|NOTICE OF MEETING)", re.M | re.I)
# tolerant of typos: Atttachment, Appendx
ATTACH = re.compile(r"^\s*(At+ach?ment|Appendi?x|Figure|Map|Schedule)\s*\d*\s*[:.\-]", re.I)
LETTER = re.compile(r"Toronto City Councillor|City Councillor\s*$", re.M)

SUBJECT_RULES = [
    ("procurement_award", r"REQUEST FOR (TENDER|PROPOSAL|QUOT|PREQUAL|SUPPLIER)|\bRF[TPQ]\b|"
                          r"RFSQ|VENDOR[S]? OF RECORD|\bVOR\b|CONTRACT AWARD|TENDER AWARD|"
                          r"AWARD OF|SOLE SOURCE|SINGLE SOURCE|BLANKET CONTRACT|NON-?COMPETITIVE|"
                          r"PROCUREMENT OF"),
    ("permit_regulatory", r"PERMIT|SECTION 28|S\.28|ONTARIO REGULATION|DELEGATED"),
    ("land_property", r"ACQUISITION|GREENLANDS|EXPROPRIAT|PURCHASE OF LAND|DISPOSAL|CONVEYANCE|"
                      r"EASEMENT|LAND EXCHANGE|SITE PLAN"),
    ("agreement_or_mou", r"MEMORANDUM OF UNDERSTANDING|SERVICE LEVEL|\bAGREEMENT\b|LICEN[CS]E|"
                         r"PARTNERSHIP|\bLEASE\b"),
    ("governance_finance", r"POLICY|BY-?LAW|APPOINTMENT|TERMS OF REFERENCE|GOVERNANCE|"
                           r"STRATEGIC PLAN|BUDGET|FINANCIAL|AUDIT|INSURANCE|WSIB|MEETING DATES|"
                           r"YEAR END|ANNUAL GENERAL"),
    ("status_update", r"UPDATE|PROGRESS|STATUS|SUMMARY|ANNUAL REPORT|WORK ?PLAN|REVIEW|REPORT ON"),
]
SUBJECT_RULES = [(n, re.compile(p, re.I)) for n, p in SUBJECT_RULES]


def title_of(text):
    for pat in (T_RFA, T_AWARD, T_RE, T_SUBJ, T_ITEM):
        m = pat.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def classify(text):
    """-> (class, title_or_None, confidential_flag). Length first: never ask a model
    what len() answers."""
    n = len(text or "")
    conf = bool(CONFIDENTIAL.search(text[:3000])) if text else False
    if n < FRAGMENT_CHARS and not title_of(text or ""):
        return "empty_or_fragment", None, conf
    if n > PACKAGE_CHARS:
        return "meeting_package", None, conf
    head = text[:2500]
    if LETTER.search(head):
        return "councillor_letter", None, conf
    if MINUTES.search(head):
        return "minutes", None, conf
    if AGENDA.search(head):
        return "agenda", None, conf
    if ATTACH.match(text.lstrip()[:120]):
        return "attachment_or_map", None, conf
    t = title_of(text)
    if not t:
        return "UNCLASSIFIED", None, conf
    for name, pat in SUBJECT_RULES:
        if pat.search(t):
            return name, t, conf
    return "other_titled", t, conf


if __name__ == "__main__":
    c = db.connect(config.DB_PATH)
    srcs = (("TRCA", "kind='agency_board' AND url LIKE '%escribemeetings%'"),
            ("EP", "kind='agency_board' AND url LIKE '%/ep/%'"),
            ("Zoo", "kind='agency_board' AND url LIKE '%/zb/%'"),
            ("council bgrd", "kind='bgrd'"),
            ("award summary", "kind='award_summary'"),
            ("committee", "kind='committee_award'"))
    grand = collections.Counter()
    unc_ex = collections.defaultdict(list)
    for label, where in srcs:
        rows = [(r["url"], r["text"]) for r in c.execute(
            f"select url,text from background_pdf where {where} and text is not null")]
        cnt = collections.Counter()
        for u, t in rows:
            k, _, _ = classify(t)
            cnt[k] += 1
            grand[k] += 1
            if k == "UNCLASSIFIED" and len(unc_ex[label]) < 3:
                unc_ex[label].append(u.rsplit("/", 1)[-1])
        unc = cnt["UNCLASSIFIED"]
        print(f"{label:14s} {len(rows):5d} docs   unclassified {unc:4d} ({unc/max(1,len(rows)):4.0%})")
    print("\nOVERALL")
    tot = sum(grand.values())
    for k, n in grand.most_common():
        print(f"   {n:5d}  {n/tot:5.1%}  {k}")
    print(f"\n   unclassified rate: {grand['UNCLASSIFIED']/tot:.1%} "
          f"(was 31% with the previous rules)")
    for lab, ex in unc_ex.items():
        print(f"   still unclassified in {lab}: {ex}")
