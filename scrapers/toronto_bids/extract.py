"""LLM-based bid extraction client.

Text in, validated extraction records out. Two models via OpenRouter:
Nemotron 3 Ultra (free) as default, GPT-5.6-Luna as automatic fallback
on rate-limit or unavailability.
"""

import hashlib
import json
import os
import re

MODELS = [
    "nvidia/nemotron-3-ultra-253b-v1:free",
    "openai/gpt-5.6-luna",
]

_PLACEHOLDER_KEY = "your-openrouter-api-key"

_PROMPT_TEMPLATE = """\
You are a procurement-document data extractor for the City of Toronto archive.

Given the full text of a board report or award document, extract EVERY procurement
contract described in it. A single document may describe SEVERAL contracts.

Return a JSON object with this exact structure:

{{
  "contracts": [
    {{
      "reference": "RFT/RFP/RFQ/contract number as printed",
      "solicitation_type": "RFT | RFP | RFQ | RFSQ | VOR | sole_source | blanket | unknown",
      "title": "what is being bought",
      "buyer": "which body is procuring",
      "posted_date": "YYYY-MM-DD or null",
      "closed_date": "YYYY-MM-DD or null",
      "documents_taken": "stated count of firms that downloaded documents, or null",
      "declared_submissions": integer count of submissions stated by the document, or null,
      "declared_compliant": integer count of compliant submissions, or null,
      "stage": "single_stage | prequalification_then_tender",
      "prequalified": ["names of firms that made pre-qualification submissions only"],
      "invited_to_tender": ["names of firms invited to submit tenders"],
      "bids": [
        {{
          "supplier_name": "verbatim, exactly as printed (REQUIRED)",
          "amount_raw": "verbatim dollar amount or null if none shown",
          "amount_basis": "plus_HST | net_of_taxes | including_HST | unknown | null",
          "status": "compliant | non_compliant | disqualified | withdrawn | no_bid | not_stated",
          "rank": integer or null
        }}
      ],
      "awards": [
        {{
          "supplier_name": "verbatim (REQUIRED)",
          "amount_raw": "verbatim or null",
          "amount_basis": "plus_HST | net_of_taxes | including_HST | unknown | null",
          "contingency_raw": "stated contingency or null",
          "value_confidential": true or false
        }}
      ],
      "funding_source": "budget line or null",
      "decision_date": "YYYY-MM-DD or null",
      "decision_body": "Board of Directors | Bid Award Panel | CPO | committee name | null"
    }}
  ]
}}

CRITICAL RULES — each was learned from a real extraction error:

1. Pre-qualified != bid. A firm that made a pre-qualification submission and was never
   issued tender documents NEVER BID. Put them in "prequalified", NOT in "bids".
   A firm invited to tender but who did not submit goes in "invited_to_tender", not "bids".

2. A disqualified bidder IS a bid with status "disqualified" and amount_raw null.
   Withdrawn, non-compliant, and "no bid" are likewise bids with the appropriate status.

3. declared_submissions is the count the document states ("Eight (8) submissions were
   received"). Extract it even when it looks redundant — it is the runtime self-check.

4. Names and amounts VERBATIM, including typos, numeric-leading firm names
   ("2489960 Ontario Inc."), and legal suffixes. Normalisation belongs downstream.

5. Multiple awards on one contract is normal (Vendor of Record arrangements).

6. If the document describes NO procurement contracts, return {{"contracts": []}}.

7. amount_basis is load-bearing. "Plus HST" and "net of all applicable taxes" are different
   bases and must not be confused.

DOCUMENT TEXT:

{text}"""


def _prompt_hash():
    return hashlib.sha256(_PROMPT_TEMPLATE.encode()).hexdigest()[:12]


EXTRACTOR_VERSION = f"v1-{_prompt_hash()}"


def _real_openrouter_key() -> str | None:
    value = os.environ.get("OPENROUTER_API_KEY")
    if value is None or value == _PLACEHOLDER_KEY:
        return None
    return value


def build_prompt(text: str) -> str:
    return _PROMPT_TEMPLATE.format(text=text)


def validate_extraction(data) -> dict:
    """Refuse a response that breaks the contract, always with ValueError (#219).

    ValueError is what callers treat as one document's refusal; a TypeError from a shape
    surprise (`"bids": null`, a string where a dict belongs) used to escape as a crash.
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"Extraction result is not a JSON object: {type(data).__name__}"
        )
    if "contracts" not in data:
        raise ValueError("Extraction result missing 'contracts' key")
    if not isinstance(data["contracts"], list):
        raise ValueError("'contracts' must be a list")
    for i, contract in enumerate(data["contracts"]):
        if not isinstance(contract, dict):
            raise ValueError(f"Contract {i} is not a dict")
        for key in ("bids", "awards"):
            if not isinstance(contract.get(key, []), list):
                raise ValueError(f"'{key}' in contract {i} must be a list")
        for bid in contract.get("bids", []):
            if not isinstance(bid, dict):
                raise ValueError(f"Bid in contract {i} is not a dict")
            if "supplier_name" not in bid or not bid["supplier_name"]:
                raise ValueError(
                    f"Bid in contract {i} missing required 'supplier_name'"
                )
        for award in contract.get("awards", []):
            if not isinstance(award, dict):
                raise ValueError(f"Award in contract {i} is not a dict")
            if "supplier_name" not in award or not award["supplier_name"]:
                raise ValueError(
                    f"Award in contract {i} missing required 'supplier_name'"
                )
    return data


def parse_llm_response(response: dict) -> dict:
    content = response["choices"][0]["message"]["content"]
    fence = re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, re.DOTALL)
    if fence:
        content = fence.group(1)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM returned malformed JSON: {exc}") from exc
    return validate_extraction(data)


class ExtractionClient:
    """Calls OpenRouter, retries with backoff, falls back across models.

    Per model: 429, 5xx, transport errors and malformed output (ValueError) are
    retried with exponential backoff, then fall through to the next model. Any
    other 4xx (retired slug 404, payment 402, model unavailable) skips straight
    to the next model without retrying. 401/403 raise immediately: the same API
    key is used for every model, so falling back cannot help. If every model
    fails, the last error is raised.
    """

    def __init__(
        self,
        api_key: str | None = None,
        models: list[str] | None = None,
        retries: int = 3,
        backoff: float = 1.0,
        timeout: float = 120.0,
    ):
        self._api_key = api_key or _real_openrouter_key()
        if not self._api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is not set or is a placeholder. "
                "Set it in scrapers/.env with a real key."
            )
        self._models = models or list(MODELS)
        self._retries = retries
        self._backoff = backoff
        self._timeout = timeout

    def extract(self, text: str) -> dict:
        import time

        import httpx

        prompt = build_prompt(text)
        last_exc = None
        for model in self._models:
            for attempt in range(self._retries + 1):
                try:
                    return self._call(httpx, model, prompt)
                except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
                    last_exc = exc
                    if isinstance(exc, httpx.HTTPStatusError):
                        status = exc.response.status_code
                        if status in (401, 403):
                            # Same key for every model: no fallback can help.
                            raise
                        if status < 500 and status != 429:
                            # Retired slug (404), no credit (402), model
                            # unavailable: retrying this model is futile, but
                            # the next one may serve.
                            break
                    if attempt < self._retries:
                        time.sleep(self._backoff * (2**attempt))
        raise last_exc  # type: ignore[misc]

    def _call(self, httpx, model: str, prompt: str) -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if model.startswith("openai/"):
            body["service_tier"] = "flex"
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return parse_llm_response(resp.json())
