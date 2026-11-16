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
import time
from typing import Optional, TypeVar

from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)
_lock = threading.Lock()
USED_KEYS: set[str] = set()  # cache files touched in this process (for --prune-cache)
_client = None


class Usage:
    def __init__(self):
        self.calls = 0
        self.cache_hits = 0
        self.refusals = 0
        self.errors = 0
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.latencies: list[float] = []
        self.replayed_cost = 0.0  # what the cached answers cost when they were generated

    def add(self, u: dict):
        with _lock:
            for k in self.tokens:
                self.tokens[k] += u.get(k, 0) or 0

    @property
    def cost_usd(self) -> float:
        return cost_of(self.tokens)

    def as_dict(self) -> dict:
        return {"api_calls": self.calls, "cache_hits": self.cache_hits, "refusals": self.refusals,
                "errors": self.errors, "tokens": self.tokens, "cost_usd": self.cost_usd,
                "full_run_cost_usd": round(self.cost_usd + self.replayed_cost, 4)}


def cost_of(tokens: dict, model: Optional[str] = None) -> float:
    p = config.PRICES[model or config.MODEL]
    return round(sum((tokens.get(k, 0) or 0) * p[k] / 1e6 for k in p), 4)


def cached_record(system: str, payload: str, schema: type[BaseModel]) -> Optional[dict]:
    """The raw cache entry (output, usage, latency) for a call, if it exists."""
    path = config.LLM_CACHE / f"{_key(system, payload, schema)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


USAGE = Usage()


def prune_cache() -> int:
    """Delete cached answers not used by the current run (stale prompts/payloads)."""
    stale = [p for p in config.LLM_CACHE.glob("*.json") if p.name not in USED_KEYS]
    for p in stale:
        p.unlink()
    return len(stale)


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
    with _lock:
        USED_KEYS.add(path.name)
    if path.exists():
        rec = json.loads(path.read_text(encoding="utf-8"))
        with _lock:
            USAGE.cache_hits += 1
            USAGE.replayed_cost += cost_of(rec.get("usage", {}), config.MODEL)
        return schema.model_validate(rec["output"])
    if not allow_api:
        return None

    import anthropic

    kwargs = {}
    if config.MODEL in config.FALLBACK_MODELS:
        # on a safety decline, the API retries on a suitable fallback model
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    t0 = time.perf_counter()
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
            **kwargs,
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise  # configuration problem: surface it, don't silently degrade
    except anthropic.APIError as e:  # rate limits/5xx are already retried by the SDK
        with _lock:
            USAGE.errors += 1
        print(f"  ! LLM call failed ({type(e).__name__}): {e}")
        return None

    latency = round(time.perf_counter() - t0, 2)
    u = resp.usage
    usage = {"input": u.input_tokens, "output": u.output_tokens,
             "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
             "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}
    with _lock:
        USAGE.calls += 1
        USAGE.latencies.append(latency)
    USAGE.add(usage)

    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        with _lock:
            USAGE.refusals += 1
        return None

    path.write_text(json.dumps({"model": resp.model, "prompt_version": config.PROMPT_VERSION,
                                "usage": usage, "latency_s": latency, "output": resp.parsed_output.model_dump()},
                               indent=1, ensure_ascii=False), encoding="utf-8")
    return resp.parsed_output
