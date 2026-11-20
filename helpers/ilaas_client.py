"""Minimal client for the ILaaS / LiteLLM endpoint with header + payload logging.

Every call:
  * prints the budget/spend/duration headers (`< x-litellm-...`)
  * appends a JSON line (request, response, headers, timing) to logs/api_calls.jsonl.
    Paper text is NOT logged: message contents, rerank documents and embedding inputs are replaced by
    their length and a hash, embedding vectors by their size (ILAAS_LOG_FULL=1 logs everything, for debugging).
Rerank responses never keep the echoed documents (the reranker returns every passage sent): neither in the
returned body, nor in the log, nor in the cache. Use the result "index" to look the passage up.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from api_errors import classify, warnings

BASE_URL = os.environ.get("ILAAS_BASE_URL", "https://llm.aristote.education/v1")
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "api_calls.jsonl"
CACHE_DIR = LOG_DIR / "cache"
_LOG_LOCK = threading.Lock()
READ_TIMEOUT_WAIT = 30         # s to wait before retrying after a ReadTimeout (overloaded model)


def _text_stub(x):
    """'long paper text' -> {'chars': 15, 'sha1': '...'}; other values unchanged."""
    if isinstance(x, str):
        return {"chars": len(x), "sha1": hashlib.sha1(x.encode()).hexdigest()[:12]}
    if isinstance(x, list):                                    # multimodal content / batch inputs
        return [_text_stub(v) for v in x]
    if isinstance(x, dict) and isinstance(x.get("text"), str):
        return {**x, "text": _text_stub(x["text"])}
    return x


def redact_request(payload):
    """Request as logged: same keys and list lengths (api_errors / api_monitor still work), no paper text."""
    if not isinstance(payload, dict):
        return payload
    r = dict(payload)
    if isinstance(r.get("messages"), list):
        r["messages"] = [{**m, "content": _text_stub(m.get("content"))} if isinstance(m, dict) else m
                         for m in r["messages"]]
    for k in ("documents", "input", "prompt"):
        if k in r:
            r[k] = _text_stub(r[k])
    return r


def redact_response(body):
    """Response as logged: embedding vectors replaced by their length."""
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        body = {**body, "data": [{**d, "embedding": f"<{len(d['embedding'])} floats>"}
                                 if isinstance(d, dict) and isinstance(d.get("embedding"), list) else d
                                 for d in body["data"]]}
    return body


def strip_rerank_documents(body):
    for x in (body.get("results") or []) if isinstance(body, dict) else []:
        if isinstance(x, dict):
            x.pop("document", None)
    return body

# Headers printed for every call (our key only; the llm_provider-* ones belong to the upstream key)
SHOWN_HEADERS = [
    "x-litellm-key-max-budget",
    "x-litellm-key-spend",
    "x-litellm-response-duration-ms",
    "x-litellm-response-cost-original",
    "x-litellm-model-group",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
]


class APIError(RuntimeError):
    """status 0 = offline cache miss. .kind / .hint: what went wrong and how to fix it (api_errors.py)."""
    def __init__(self, status: int, body: str, kind: str = "", hint: str = ""):
        super().__init__(f"HTTP {status}: {body[:500]}" + (f"\n  -> {kind}: {hint}" if kind else ""))
        self.status = status
        self.body = body
        self.kind = kind
        self.hint = hint


def _say_hint(kind: str, hint: str) -> None:
    print(f"! {kind}: {hint}")


class ILaaSClient:
    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL,
                 timeout: float = 300.0, tag: str = "", verbose: bool = True,
                 use_cache: bool = False, cache_bundle: str | Path | None = None):
        """cache_bundle: one JSON file {sha1 key: response} shipped with an example (e.g.
        data/e2e_mm/api_cache.json); looked up after logs/cache, so the example replays without key."""
        key = (api_key or os.environ.get("ILAAS_API_KEY") or "").strip()
        self.use_cache = use_cache or os.environ.get("ILAAS_CACHE") == "1"
        if not key and not self.use_cache:
            raise SystemExit("ILAAS_API_KEY not set (export ILAAS_API_KEY=...), or use ILAAS_CACHE=1 to replay saved responses")
        self.has_key = bool(key)   # without a key only cached responses can be replayed (offline fallback)
        self.http = httpx.Client(base_url=base_url, timeout=timeout,
                                 headers={"Authorization": f"Bearer {key}"} if key else {})
        self.tag = tag
        self.verbose = verbose
        self.log_full = os.environ.get("ILAAS_LOG_FULL") == "1"
        bundle = cache_bundle or os.environ.get("ILAAS_CACHE_BUNDLE")
        self.bundle = json.loads(Path(bundle).read_text()) if bundle and Path(bundle).exists() else {}
        LOG_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------ core
    def _for_log(self, payload):
        return payload if self.log_full else redact_request(payload)

    def _log(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _LOG_LOCK, LOG_FILE.open("a") as f:  # thread-safe for concurrent callers
            f.write(line)

    def _request(self, method: str, path: str, payload: dict | None = None,
                 retries: int = 4) -> dict:
        """Send with retries on connection errors, 429 and 5xx (exponential backoff).

        With use_cache=True, a successful response for an identical payload is replayed from
        logs/cache/<sha1>.json without an API call (handout fallback: reuse saved LLM outputs).
        """
        cache_file = None
        if self.use_cache and payload is not None:
            key = hashlib.sha1(json.dumps({"path": path, **payload}, sort_keys=True).encode()).hexdigest()
            cache_file = CACHE_DIR / f"{key}.json"
            if cache_file.exists() or key in self.bundle:
                body = json.loads(cache_file.read_text()) if cache_file.exists() else json.loads(json.dumps(self.bundle[key]))
                if path.endswith("/rerank"):
                    strip_rerank_documents(body)     # entries cached before the documents were stripped
                if self.verbose:
                    print(f"> {method} {path}  [cache]  model={payload.get('model')}")
                body["_meta"] = {"wall_ms": 0.0, "headers": {}, "cached": True}
                return body
        if not self.has_key:
            msg = (f"cache miss for {path} (model={payload.get('model') if payload else None}) "
                   "and no ILAAS_API_KEY: this call was never made before, cannot replay offline")
            rec = {"ts": datetime.now(timezone.utc).isoformat(), "tag": self.tag, "method": method, "path": path,
                   "status": 0, "error": msg, "request": self._for_log(payload)}
            self._log(rec)                     # so api_monitor shows offline gaps too
            kind, hint = classify(rec)
            _say_hint(kind, hint)
            raise APIError(0, msg, kind, hint)
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                resp = self.http.request(method, path, json=payload)
            except httpx.TransportError as e:
                self._log({"ts": datetime.now(timezone.utc).isoformat(), "tag": self.tag,
                           "method": method, "path": path, "status": None, "attempt": attempt,
                           "error": f"{type(e).__name__}: {e}", "request": self._for_log(payload)})
                if attempt == retries:
                    kind, hint = classify({"status": None, "error": f"{type(e).__name__}: {e}", "path": path,
                                           "request": self._for_log(payload)})
                    _say_hint(kind, hint)
                    raise
                wait = 2 ** attempt
                if isinstance(e, httpx.ReadTimeout):
                    # the server is still working on the abandoned request (and bills it): an immediate retry
                    # doubles the load on an already overloaded model, so give it time first
                    wait = max(wait, READ_TIMEOUT_WAIT)
                print(f"! {type(e).__name__} on {path}, retry {attempt + 1}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            # a 500 caused by a bad parameter (e.g. unsupported reasoning_effort) is not transient
            # (the proxy answers many bad requests with HTTP 500 "'>=' not supported between instances of
            # 'str' and 'int'"; a request body over ~1 MB gives 500 "413 Request Entity Too Large")
            param_error = any(m in resp.text for m in ("not supported between instances", "BadRequestError",
                                                       "Request Entity Too Large"))
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < retries and not param_error:
                wait = float(resp.headers.get("retry-after", 2 ** attempt))
                if "all_deployments_in_cooldown" in resp.text:   # a failed call blocked the model for ~5 s
                    wait = max(wait, 6.0)
                print(f"! HTTP {resp.status_code} on {path}, retry {attempt + 1}/{retries} in {wait:.0f}s")
                self._log({"ts": datetime.now(timezone.utc).isoformat(), "tag": self.tag,
                           "method": method, "path": path, "status": resp.status_code,
                           "attempt": attempt, "error": resp.text[:500], "request": self._for_log(payload)})
                time.sleep(wait)
                continue
            break
        wall_ms = (time.perf_counter() - t0) * 1000
        litellm_headers = {k: v for k, v in resp.headers.items()
                           if k.lower().startswith(("x-litellm", "x-ratelimit"))}
        try:
            body = resp.json()
        except ValueError:
            body = {"_raw": resp.text}
        if path.endswith("/rerank"):
            strip_rerank_documents(body)   # the reranker echoes every passage; we only need index + score

        if self.verbose:
            print(f"> {method} {path}  [{resp.status_code}]  wall={wall_ms:.0f}ms"
                  + (f"  model={payload.get('model')}" if payload and "model" in payload else ""))
            for h in SHOWN_HEADERS:
                if h in resp.headers:
                    print(f"< {h}: {resp.headers[h]}")

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tag": self.tag,
            "method": method,
            "path": path,
            "status": resp.status_code,
            "wall_ms": round(wall_ms, 1),
            "prompt_sha1": hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if payload else None,
            "request": self._for_log(payload),
            "headers": litellm_headers,
            "response": body if self.log_full else redact_response(body),
        }
        self._log(record)

        if resp.status_code >= 400:
            kind, hint = classify(record) or ("", "")
            if kind:
                _say_hint(kind, hint)
            raise APIError(resp.status_code, resp.text, kind, hint)
        for kind, hint in warnings(record):     # 200, but truncated / empty / unknown parameter
            if kind != "truncated answer":       # chat() prints its own truncation line
                _say_hint(kind, hint)
        if cache_file is not None:
            CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(body, ensure_ascii=False))
        body["_meta"] = {"wall_ms": wall_ms, "headers": litellm_headers, "cached": False}
        return body

    # ------------------------------------------------------------- endpoints
    def models(self) -> list[str]:
        return [m["id"] for m in self._request("GET", "/models")["data"]]

    def chat(self, model: str, messages: list[dict], thinking: bool | None = None, **kw) -> dict:
        """thinking=False disables hidden reasoning on Qwen3-style models (qwen-3.8-27b reasons by
        default and can burn the whole max_tokens budget on it -> empty content)."""
        if thinking is not None:
            kw.setdefault("chat_template_kwargs", {})["enable_thinking"] = thinking
        body = self._request("POST", "/chat/completions", {"model": model, "messages": messages, **kw})
        ch = (body.get("choices") or [{}])[0]
        if ch.get("finish_reason") == "length":
            u = body.get("usage") or {}
            print(f"! {model}: truncated at max_tokens (completion={u.get('completion_tokens')}, "
                  f"reasoning={(u.get('completion_tokens_details') or {}).get('reasoning_tokens')})"
                  + (" -> EMPTY content" if not (ch.get("message") or {}).get("content") else "")
                  + ". Fix: raise max_tokens (>= 1000 for JSON); switch thinking off on reasoning models")
        return body

    def chat_text(self, model: str, prompt: str, system: str | None = None, **kw) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        return self.chat(model, msgs, **kw)["choices"][0]["message"].get("content") or ""

    def embed(self, model: str, inputs: list[str]) -> dict:
        return self._request("POST", "/embeddings", {"model": model, "input": inputs})

    def rerank(self, model: str, query: str, documents: list[str], top_n: int | None = None) -> dict:
        # ALWAYS send top_n: if omitted, the proxy forwards top_n="None" (a string) to one of the
        # reranker backends, which then fails randomly with HTTP 400 'body.top_n int_type'.
        payload = {"model": model, "query": query, "documents": documents,
                   "top_n": top_n or len(documents)}
        return self._request("POST", "/rerank", payload)


def parse_json_answer(text: str) -> dict | None:
    """Extract a JSON object from a model answer (tolerates ```json fences / <think> blocks)."""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
