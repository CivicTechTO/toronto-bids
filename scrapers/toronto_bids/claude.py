"""Direct Claude Messages API; shared prompt/cache contract lives in extract.py."""

import json
import os
from decimal import Decimal, ROUND_CEILING

from toronto_bids.llm_budget import BudgetExceeded, UsageBudget

# Sonnet is the conservative default until Haiku passes real-document evaluation.
DEFAULT_MODEL = "claude-sonnet-5-5"
# USD per million tokens, checked against official pricing on 2026-10-23.
# input, 5-minute cache writes, cache reads, output; Haiku has a >100k tier.
_RATES = {
    "claude-sonnet-5-5": ("2", "2.5", "0.10", "10"),
    "claude-haiku-5-5": ("0.10", "0.125", "0.01", "0.50"),
}
_HAIKU_LONG = ("0.50", "0.625", "0.05", "2.50")
MAX_TOKENS = 32768


def _object(properties):
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


def _array(item):
    return {"type": "array", "items": item}


# Mirrors the existing shared prompt; this is a transport constraint, not a
# source-specific extraction prompt. Keep nullable, verbatim evidence fields.
_TEXT = {"type": ["string", "null"]}
_COUNT = {"type": ["integer", "null"]}
_BASIS = {"anyOf": [
    {"type": "string", "enum": ["plus_HST", "net_of_taxes", "including_HST", "unknown"]},
    {"type": "null"},
]}
EXTRACTION_SCHEMA = _object({
    "contracts": _array(_object({
        "reference": _TEXT,
        "solicitation_type": {"type": "string", "enum": [
            "RFT", "RFP", "RFQ", "RFSQ", "VOR", "sole_source", "blanket", "unknown",
        ]},
        "title": {"type": "string"},
        "buyer": _TEXT,
        "posted_date": _TEXT,
        "closed_date": _TEXT,
        "documents_taken": {"type": ["integer", "string", "null"]},
        "declared_submissions": _COUNT,
        "declared_compliant": _COUNT,
        "stage": {"type": "string", "enum": ["single_stage", "prequalification_then_tender"]},
        "prequalified": _array({"type": "string"}),
        "invited_to_tender": _array({"type": "string"}),
        "bids": _array(_object({
            "supplier_name": {"type": "string"},
            "amount_raw": _TEXT,
            "amount_basis": _BASIS,
            "status": {"type": "string", "enum": [
                "compliant", "non_compliant", "disqualified", "withdrawn", "no_bid", "not_stated",
            ]},
            "rank": _COUNT,
        })),
        "awards": _array(_object({
            "supplier_name": {"type": "string"},
            "amount_raw": _TEXT,
            "amount_basis": _BASIS,
            "contingency_raw": _TEXT,
            "value_confidential": {"type": "boolean"},
        })),
        "funding_source": _TEXT,
        "decision_date": _TEXT,
        "decision_body": _TEXT,
    })),
})


def _charge(model, usage):
    if not isinstance(usage, dict):
        raise ValueError("Claude response missing usage counters")
    keys = (
        "input_tokens", "cache_creation_input_tokens",
        "cache_read_input_tokens", "output_tokens",
    )
    counts = []
    for key in keys:
        value = usage.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid Claude usage counter: {key}")
        counts.append(value)
    if "input_tokens" not in usage or "output_tokens" not in usage:
        raise ValueError("Claude response missing usage counters")
    rates = _RATES[model]
    if model == "claude-haiku-5-5" and sum(counts[:3]) > 100_000:
        rates = _HAIKU_LONG
    # Tokens * USD/MTok is already microdollars. Round upward, never undercount.
    cost = sum(Decimal(rate) * count for rate, count in zip(rates, counts))
    return int(cost.to_integral_value(rounding=ROUND_CEILING))


def parse_response(response):
    from toronto_bids.extract import parse_llm_response

    if response.get("stop_reason") != "end_turn":
        raise ValueError(f"Claude extraction incomplete: stop_reason={response.get('stop_reason')}")
    blocks = response.get("content")
    if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
        raise ValueError("Claude returned malformed content blocks")
    try:
        text = "".join(b["text"] for b in blocks if b.get("type") == "text")
        return parse_llm_response({"choices": [{"message": {"content": text}}]})
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Claude returned malformed extraction JSON") from exc


class ClaudeExtractionClient:
    def __init__(self, api_key=None, models=None, retries=3, backoff=1.0, timeout=120.0):
        from toronto_bids.config import DATA_DIR

        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.key.strip() or self.key.startswith("your-"):
            raise ValueError("ANTHROPIC_API_KEY is not set or is a placeholder. Configure a direct Claude API key.")
        self.models = models or [os.environ.get("TB_CLAUDE_MODEL", DEFAULT_MODEL)]
        for model in self.models:
            if model not in _RATES:
                raise ValueError(f"Claude model has no reviewed price schedule: {model}")
        self.effort = os.environ.get("TB_CLAUDE_EFFORT")
        if self.effort is not None and self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("TB_CLAUDE_EFFORT must be low, medium, high, xhigh or max")
        self.budget = UsageBudget(
            DATA_DIR / "llm-usage.sqlite", os.environ.get("TB_LLM_MONTHLY_USD", "20")
        )
        self.retries, self.backoff, self.timeout = retries, backoff, timeout

    def extract(self, text):
        import time
        import httpx

        from toronto_bids.extract import build_prompt

        prefix = build_prompt("")
        content = [{
            "type": "text", "text": prefix,
            "cache_control": {"type": "ephemeral"},
        }]
        if text:
            content.append({"type": "text", "text": text})
        last = None
        for model in self.models:
            for attempt in range(self.retries + 1):
                try:
                    return self._call(httpx, model, content)
                except BudgetExceeded:
                    raise
                except (httpx.HTTPStatusError, httpx.TransportError, ValueError) as exc:
                    last = exc
                    if isinstance(exc, httpx.HTTPStatusError):
                        status = exc.response.status_code
                        if status in (401, 402, 403):
                            raise
                        if status < 500 and status != 429:
                            break
                    if attempt < self.retries:
                        time.sleep(self.backoff * 2**attempt)
        raise last

    def _call(self, httpx, model, content):
        body = {
            "model": model, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
            "output_config": {
                "effort": self.effort or ("high" if model == "claude-sonnet-5-5" else "medium"),
                "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            },
        }
        # UTF-8 bytes plus framing is a deliberately loose input-token bound.
        # Reserve the highest input tier, including cache-write premium, and all
        # possible output (including thinking). Reconcile to returned usage.
        rates = _HAIKU_LONG if model == "claude-haiku-5-5" else _RATES[model]
        input_bound = len(json.dumps(body, ensure_ascii=False).encode()) + 4096
        worst_cost = Decimal(rates[1]) * input_bound + Decimal(rates[3]) * MAX_TOKENS
        allowance = int(worst_cost.to_integral_value(rounding=ROUND_CEILING))
        reservation = self.budget.reserve(model, allowance)
        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages", json=body,
                headers={
                    "x-api-key": self.key, "anthropic-version": "2026-10-21",
                    "content-type": "application/json",
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                self.budget.settle(reservation, 0, "rejected")
            raise
        response = resp.json()
        if not isinstance(response, dict):
            raise ValueError("Claude returned a non-object response")
        usage = response.get("usage", {})
        self.budget.settle(reservation, _charge(model, usage), "reported", usage)
        # Account even malformed, refused or truncated outputs: these are billed.
        return parse_response(response)
