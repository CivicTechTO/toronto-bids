# Agency board-report fixtures (#135)

pdftotext -layout output of real board award reports, captured 2026-07-18.

| fixture | source URL |
|---|---|
| trca_armour_stone_2023.txt | https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=14809 |
| trca_vor_appraisal_2021.txt | https://pub-trca.escribemeetings.com/filestream.ashx?DocumentId=7921 |
| zoo_energy_retrofit_2019.txt | https://www.toronto.ca/legdocs/mmis/2019/zb/bgrd/backgroundfile-124313.pdf |
| zoo_red_panda_2025.txt | https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-257571.pdf |
| zoo_perimeter_fence_2025.txt | https://www.toronto.ca/legdocs/mmis/2025/zb/bgrd/backgroundfile-260182.pdf |

The two 2025 Zoo reports route financials to a CONFIDENTIAL ATTACHMENT — they are the
value_confidential=1 cases. The 2019 Zoo report names its winner publicly. The TRCA
armour-stone report tabulates all four bidders with opening results; the VOR report is
the vendor-of-record shape (multiple winners, no per-bid prices).

## trca_escribe_2023.html — SYNTHETIC (#135)

Not a real capture. `curl -sL -A "Mozilla/5.0" "https://pub-trca.escribemeetings.com/?FillWidth=1&Year=2023"`
was run live on 2026-07-18 (network was available) and returned a real 286KB page, but
its shape doesn't match a static-HTML walker: the page is a JS-rendered FullCalendar
widget, and its two literal "Meeting.aspx" occurrences are inside a JS template string
assembled at click time (`$('#eventLink').attr('href', href = '/Meeting.aspx?Id=' +
event.id + '&lang=' + ...)`), not `<a href="Meeting.aspx?...">` anchors — so
`escribe_document_urls`'s regexes correctly find nothing on the real page. It carries no
`FileStream.ashx` links at all before JS runs. Per the task brief's Step 1 fallback,
this fixture is hand-written instead, containing the anchor shapes the extractor is
built to find (`Meeting.aspx?Id=...` and `FileStream.ashx?DocumentId=...`), standing in
for whatever rendered meeting-list/meeting-detail HTML the real walk would eventually
see. The extraction unit test exercises correct behavior on this shape regardless of
where the HTML came from.
