# Image-only City report text

Some Bid Committee reports are scans with no text layer. The PDF is readable to a person but `pdftotext` returns nothing, so the shared extraction path previously skipped it as `no_text`. Native text remains the first choice. `pdf_text.py` adds bounded local preparation for held, textless `bd_report` documents; it is reusable infrastructure, not a bid-table parser.

## Text preparation and evidence

The original PDF bytes, SHA-256 and `background_pdf.text` are preserved. Derived text lives separately in `pdf_text`, keyed by the original byte hash. Its immutable proof binds the exact text digest, method and provenance. OCR provenance includes the Tesseract version, English language-data digest, resolution, segmentation settings, every page's text and every recognized word's coordinates/confidence.

The fallback first retries native `pdftotext -layout`. Only an empty result invokes Tesseract. Pages render serially through Poppler at 300 DPI and use Tesseract PSM 6 with interword spacing preserved. There is a 40-page bound, subprocess timeouts and one OCR thread. Every page must complete before a record is stored. Empty output, failed tools, changed original bytes or altered accepted text fail visibly; they never become an empty extraction request or a partially accepted document. A missing local original remains a portable-archive gap.

The [Tesseract quality guide](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html) documents image resolution and segmentation considerations; [its CLI documentation](https://github.com/tesseract-ocr/tessdoc/blob/main/Command-Line-Usage.md) describes PSM and spacing controls. Those settings do not guarantee correct names, prices or relationships.

The eleven reviewed source derivatives are shipped in `data/city_image_pdf_text.json.gz`, avoiding a new recognition run or different engine output when deploying their reviews. Lookups are read-only. Normal text preparation verifies held original bytes before accepting a shipped derivative; source-review preflight independently verifies those bytes before atomic evidence/fact persistence. Already accepted records replay without local originals or an OCR installation. A changed derivative requires an explicit review and repair, not replacement under the same cache key.

## Extraction and source admission

Text preparation feeds the unchanged Sonnet request builder, prompt/version, budget and batch queue. Original PDF hashes remain the document cache keys. Keyless uncached accounting uses the same preparation/eligibility rules. Existing native-text corpora are unaffected; automatic new OCR preparation is initially confined to historical `bd_report` extraction.

Recognition output is review evidence, not a guarantee of literal supplier or price spelling. Generic BD backfill holds OCR-derived observations until an original-image source review exists. The reviewed cohort uses the existing scoped response representation described in [scoped-city-bids.md](scoped-city-bids.md): one unpriced firm participation, separate source scopes, response cells and qualified quoted values.

Each image review binds the complete OCR proof, original PDF, raw model result/contract collection, independent City identity proof, original agenda hash and the City's own item transcription. Image-space witness rectangles reproduce exact raw OCR crops. Geometric checks bind each response to its supplier, scope and alternative. All source tables were also read visually.

Explicit OCR corrections retain both readings and must be corroborated by the independently held City transcription. Name/value cleanup preserves alphanumeric supplier identities and every price digit; broader renaming or numeric repairs are refused. Small noncritical text repairs and source-header cleanup are separately bounded. Typography and the City's `percent`/`%` rendering can corroborate each other. Corrections cannot turn a quotation found elsewhere in the document into a supplier/price reassignment. Unresolved recognition conflicts remain held for stronger evidence.

The original agenda can also establish independent historical identity. A separately labelled initial Schedule A award is accepted only when the recommendation assigns that exact schedule to its full supplier and its exact first net amount and dates uniquely match the publisher. Adjacent schedules and combined totals do not qualify. Source/publisher method discrepancies remain explicit in the existing reviewed discrepancy proof class.

## Operation and deployment

For new recognition work install Poppler and English Tesseract, for example `apt-get install poppler-utils tesseract-ocr tesseract-ocr-eng`. An equivalent user-local installation on PATH works; validation used unpacked distribution packages because this account requires interactive sudo authentication. CI installs the system packages and runs a real raster-PDF regression.

Use the normal shared writer lock for operational archive writes. `tb extract --batch --corpus bd_report --limit N` prepares eligible held reports and queues the normal economical Sonnet requests. It does not add a historical crawl to the nightly. New unreviewed OCR facts stay held even after a model response completes.

A queue and its spending ledger belong to one operational archive. For a private rehearsal, collect operationally into that archive under its lock, then copy validated immutable cache entries to the rehearsal. Collecting directly into a copy while sharing the live queue can advance acknowledgement before the authoritative cache receives the result. The validation pass detected and repaired exactly that handoff from retained completed receipts, with no paid retry or publication; queue history was preserved.

After merge, deploy the backend and perform the normal backed-up, locked resolution/supplier/export/publish recovery. The shipped derivative and committed reviews cover this cohort; source/cache drift aborts before replacing facts. Verify the [dated cohort record](research/city-image-pdf-validation-2026-11-06.md) and published scoped pages. The existing frontend supports these optional facts; the companion label correction clarifies unknown HST treatment without discarding published price-header qualifications.
