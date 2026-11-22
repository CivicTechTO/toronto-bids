"""Reproduce the 2026-11-22 candidate campaign measurements offline, from the repository root.

uv run --project scrapers python docs/ground-truth/extraction-qualification/followups/2026-11-22/summarize.py
"""

import json
from pathlib import Path

from toronto_bids.qualification_candidate import summarize_candidate_campaign

ROOT = Path(__file__).parent
PRIOR_COHORTS = (
    ROOT.parents[1] / 'cohort.json',
    ROOT.parents[1] / 'cohort-comparison-2026-11-13.json',
    ROOT.parents[1] / 'followups/2026-11-15/cohort.json',
)


def summarize(root=ROOT):
    return summarize_candidate_campaign(root, prior_cohorts=PRIOR_COHORTS)


if __name__ == '__main__':
    summary = summarize()
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'validation_interventions'}, indent=2))
