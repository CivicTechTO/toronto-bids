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
