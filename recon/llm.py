"""
Thin wrapper around the Claude API: structured outputs, a disk cache, and cost tracking.

The cache key is (model, prompt version, system prompt, payload). Cached answers are
committed to the repo, so the demo and the evaluation can be replayed without an API key and
without spending money; changing a prompt invalidates exactly the affected answers.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Optional, TypeVar

from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)
_lock = threading.Lock()
_client = None


class Usage:
    def __init__(self):
        self.calls = 0
        self.cache_hits = 0
        self.refusals = 0
        self.errors = 0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    def add(self, u: dict):
        with _lock:
            for k in self.tokens:
                self.tokens[k] += u.get(k, 0) or 0

    @property
    def cost_usd(self) -> float:
        return round(sum(self.tokens[k] * config.PRICE_PER_MTOK[k] / 1e6 for k in self.tokens), 4)

    def as_dict(self) -> dict:
        return {"api_calls": self.calls, "cache_hits": self.cache_hits, "refusals": self.refusals,
                "errors": self.errors, "tokens": self.tokens, "cost_usd": self.cost_usd}


USAGE = Usage()


def credentials_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _key(system: str, payload: str, schema: type[BaseModel]) -> str:
    h = hashlib.sha256()
    for part in (config.MODEL, config.PROMPT_VERSION, schema.__name__, system, payload):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


def structured_call(system: str, payload: str, schema: type[T], *, allow_api: bool = True) -> Optional[T]:
    """Return a validated `schema` instance, or None (refusal / error / no credentials)."""
    config.LLM_CACHE.mkdir(parents=True, exist_ok=True)
    path = config.LLM_CACHE / f"{_key(system, payload, schema)}.json"
    if path.exists():
        rec = json.loads(path.read_text(encoding="utf-8"))
        with _lock:
            USAGE.cache_hits += 1
        return schema.model_validate(rec["output"])
    if not allow_api:
        return None

    import anthropic

    try:
        resp = _get_client().beta.messages.parse(
            model=config.MODEL,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": payload}],
            output_format=schema,
            output_config={"effort": config.EFFORT},
            thinking={"type": "adaptive"},
            # No prompt caching: the shared prefix (system prompt) is below the minimum cacheable
            # size and every payload is unique, so caching only added the 25% cache-write premium
            # (observed in the first full run: 283k cache-write tokens, 0 cache reads).
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # on a safety decline, the API retries on a suitable fallback model
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise  # configuration problem: surface it, don't silently degrade
    except anthropic.APIError as e:  # rate limits/5xx are already retried by the SDK
        with _lock:
            USAGE.errors += 1
        print(f"  ! LLM call failed ({type(e).__name__}): {e}")
        return None

    u = resp.usage
    usage = {"input": u.input_tokens, "output": u.output_tokens,
             "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
             "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}
    with _lock:
        USAGE.calls += 1
    USAGE.add(usage)

    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        with _lock:
            USAGE.refusals += 1
        return None

    path.write_text(json.dumps({"model": resp.model, "prompt_version": config.PROMPT_VERSION,
                                "usage": usage, "output": resp.parsed_output.model_dump()},
                               indent=1, ensure_ascii=False), encoding="utf-8")
    return resp.parsed_output
